from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import yt_dlp
import logging
import tempfile
import os
import uuid
import re
import shutil
import glob
import time
import json
import hmac
import hashlib
import secrets
import smtplib
import threading
import urllib.request
import base64
from email.message import EmailMessage
from datetime import datetime

from typing import Optional, List, Dict, Any


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("video-downloader")


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Universal Social Video Downloader & SSTE Multi-Inverter Gateway",
    version="4.0.0",
    description="Universal social media video downloader (URL or name search) with Admin-Guarded Multi-Device Inverter IoT Gateway, Midnight Archive, Reset-All & Firebase Password Reset"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MULTI-DEVICE IN-MEMORY REGISTRY & SECURITY CONFIG
# ============================================================

# Render > Environment me ADMIN_MASTER_KEY set karein (default purani key compatibility ke liye)
ADMIN_MASTER_KEY = os.environ.get("ADMIN_MASTER_KEY", "SAJJAD_ADMIN_786")

registered_devices: set = set()
devices_live_data: Dict[str, Any] = {}
devices_pending_commands: Dict[str, Any] = {}

# Midnight Rollover & Archive Trackers
device_current_dates: Dict[str, str] = {}
device_daily_archives: Dict[str, list] = {}


# ============================================================
# MODELS
# ============================================================

class VideoRequest(BaseModel):
    url: str


class ExtractResponse(BaseModel):
    title: str
    thumbnail: Optional[str]
    duration: int
    platform: str
    formats: List[Dict[str, Any]]


# ============================================================
# CONSTANTS
# ============================================================

VIDEO_EXTENSIONS = {
    "mp4",
    "webm",
    "mkv",
    "mov",
    "avi",
    "flv",
    "ts",
    "m4v"
}

AUDIO_EXTENSIONS = {
    "mp3",
    "m4a",
    "aac",
    "opus",
    "wav",
    "flac",
    "ogg"
}


# ============================================================
# HELPERS
# ============================================================

def clean_url(raw_url: str) -> str:
    if not raw_url:
        raise ValueError("URL is empty")

    url = str(raw_url).strip()
    url = url.strip("'").strip('"').strip()

    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url

    while url.endswith(("'", '"', ">", ".", ",")):
        url = url[:-1].strip()

    return url


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
            return int(float(value))
        return default
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def safe_filename(name: str) -> str:
    name = safe_str(name, "video")
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .")
    if not name:
        name = "video"
    return name[:150]


def format_size(size: int) -> Optional[float]:
    if size <= 0:
        return None
    return round(size / 1048576, 2)


def is_audio_format(f: Dict[str, Any]) -> bool:
    ext = safe_str(f.get("ext"), "").lower()
    vcodec = safe_str(f.get("vcodec"), "none").lower()
    return vcodec == "none" or ext in AUDIO_EXTENSIONS


def is_video_format(f: Dict[str, Any]) -> bool:
    ext = safe_str(f.get("ext"), "").lower()
    vcodec = safe_str(f.get("vcodec"), "none").lower()
    return vcodec != "none" and ext not in AUDIO_EXTENSIONS


def admin_key_valid(provided: Optional[str]) -> bool:
    try:
        return hmac.compare_digest(
            str(provided or "").encode("utf-8"),
            ADMIN_MASTER_KEY.encode("utf-8")
        )
    except Exception:
        return False


def get_ffmpeg_path() -> Optional[str]:
    path = shutil.which("ffmpeg")
    if path:
        return path

    possible = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "C:\\ffmpeg\\bin\\ffmpeg.exe",
        "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
    ]

    for p in possible:
        if os.path.isfile(p):
            return p

    return None


# ============================================================
# YT-DLP OPTIONS
# ============================================================

def get_base_options() -> Dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 10,
        "extractor_retries": 5,
        "concurrent_fragment_downloads": 4,
        "http_chunk_size": 10485760,
        "continuedl": False,
        "overwrites": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        ),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }


def get_extract_options() -> Dict[str, Any]:
    opts = get_base_options()
    opts.update({"skip_download": True})
    return opts


# ============================================================
# SMART FORMAT SELECTION
# ============================================================

def get_format_selector(format_id: str) -> str:
    requested = safe_str(format_id, "best")

    if requested.lower() in {"", "best", "auto", "default"}:
        return (
            "bv*[ext=mp4]+ba[ext=m4a]/"
            "bv*+ba/"
            "b[ext=mp4]/"
            "b"
        )

    if requested.lower() in {"audio", "bestaudio"}:
        return (
            "ba[ext=m4a]/"
            "ba/"
            "bestaudio"
        )

    return (
        f"{requested}+ba[ext=m4a]/"
        f"{requested}+ba/"
        f"{requested}/"
        "bv*[ext=mp4]+ba[ext=m4a]/"
        "bv*+ba/"
        "b[ext=mp4]/"
        "b"
    )


# ============================================================
# MULTI-DEVICE INVERTER IOT GATEWAY ENDPOINTS
# ============================================================

# 1. Duplicate ID Check
@app.get("/api/device/check/{device_id}")
async def check_device_id_availability(device_id: str):
    clean_id = device_id.strip()
    if clean_id in registered_devices or clean_id in devices_live_data:
        return {
            "available": False,
            "device_id": clean_id,
            "message": "Already Exists! Yeh device pehle se bound hai. Admin se rabta karein."
        }
    return {
        "available": True,
        "device_id": clean_id,
        "message": "Device ID is available."
    }


# 2. Get List of All Active Devices
@app.get("/api/devices")
async def list_all_devices():
    all_devs = sorted(list(registered_devices.union(set(devices_live_data.keys()))))
    return {
        "count": len(all_devs),
        "devices": all_devs
    }


# 3. Dynamic Telemetry Push with Midnight Rollover Logic
@app.put("/api/{device_id}/live")
async def update_device_live(device_id: str, request: Request):
    global devices_live_data, registered_devices, device_current_dates, device_daily_archives
    clean_id = device_id.strip()
    try:
        payload = await request.json()
        registered_devices.add(clean_id)
        
        # Current system date check (Midnight Rollover)
        today_date = datetime.now().strftime("%Y-%m-%d")
        if clean_id not in device_current_dates:
            device_current_dates[clean_id] = today_date
        elif device_current_dates[clean_id] != today_date:
            old_date = device_current_dates[clean_id]
            logger.info(f"MIDNIGHT ROLLOVER: Date changed for {clean_id} from {old_date} to {today_date}")
            
            if clean_id in devices_live_data:
                if clean_id not in device_daily_archives:
                    device_daily_archives[clean_id] = []
                
                device_daily_archives[clean_id].append({
                    "date": old_date,
                    "solarEnergy": devices_live_data[clean_id].get("solarEnergy", 0),
                    "loadEnergy": devices_live_data[clean_id].get("loadEnergy", 0),
                    "importEnergy": devices_live_data[clean_id].get("importEnergy", 0),
                    "exportEnergy": devices_live_data[clean_id].get("exportEnergy", 0),
                })
            
            device_current_dates[clean_id] = today_date

        devices_live_data[clean_id] = {
            **payload,
            "device_id": clean_id,
            "server_timestamp": int(time.time() * 1000)
        }
        return {"status": "success", "device_id": clean_id, "message": "Telemetry updated with midnight rollover check"}
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(err)}")


# 4. Dynamic Telemetry Read
@app.get("/api/{device_id}/live")
async def get_device_live(device_id: str):
    clean_id = device_id.strip()
    if clean_id not in devices_live_data:
        raise HTTPException(status_code=404, detail=f"Device '{clean_id}' not found or offline.")
    return devices_live_data[clean_id]


# 5. Archived Daily History Endpoint for Month/Year View
@app.get("/api/{device_id}/archive")
async def get_device_archive(device_id: str):
    clean_id = device_id.strip()
    return {
        "device_id": clean_id,
        "daily_history": device_daily_archives.get(clean_id, [])
    }


# 6. ESP32 Polls Commands
@app.get("/api/{device_id}/command")
async def poll_device_command(device_id: str):
    global devices_pending_commands
    clean_id = device_id.strip()
    cmd = devices_pending_commands.get(clean_id, {})
    devices_pending_commands[clean_id] = {}
    return cmd


# 7. App Posts Command
@app.post("/api/{device_id}/command")
async def queue_device_command(device_id: str, request: Request):
    global devices_pending_commands
    clean_id = device_id.strip()
    try:
        cmd = await request.json()
        devices_pending_commands[clean_id] = cmd
        return {"status": "queued", "device_id": clean_id, "command": cmd}
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Invalid Command JSON: {str(err)}")


# 8. ADMIN EXCLUSIVE: Release / Delete Device Lock
@app.delete("/api/admin/device/{device_id}")
async def admin_release_device(device_id: str, request: Request):
    global registered_devices, devices_live_data, devices_pending_commands
    clean_id = device_id.strip()

    admin_key = request.headers.get("X-Admin-Key")
    if not admin_key_valid(admin_key):
        raise HTTPException(status_code=403, detail="Unauthorized: Only Admin (Mr. Sajjad) can release bound hardware.")

    was_registered = clean_id in registered_devices
    was_live = clean_id in devices_live_data

    if clean_id in registered_devices:
        registered_devices.remove(clean_id)

    if clean_id in devices_live_data:
        del devices_live_data[clean_id]

    if clean_id in devices_pending_commands:
        del devices_pending_commands[clean_id]

    if was_registered or was_live:
        logger.info(f"ADMIN ACTION: Device '{clean_id}' successfully released and deleted from server registry.")
        return {
            "status": "success",
            "device_id": clean_id,
            "message": f"Device '{clean_id}' released successfully. Already Exists lock cleared."
        }
    else:
        raise HTTPException(status_code=404, detail=f"Device '{clean_id}' not found on server registry.")


# 9. UNIVERSAL CLEAR ALL RESET: Flush All Energy, Peaks and History
@app.delete("/api/admin/device/{device_id}/reset-all")
async def admin_reset_all_device_data(device_id: str, request: Request):
    global devices_live_data, device_daily_archives, devices_pending_commands
    clean_id = device_id.strip()

    admin_key = request.headers.get("X-Admin-Key")
    if not admin_key_valid(admin_key):
        raise HTTPException(status_code=403, detail="Unauthorized: Admin key required to reset all data.")

    if clean_id in devices_live_data:
        devices_live_data[clean_id]["solarEnergy"] = 0.0
        devices_live_data[clean_id]["loadEnergy"] = 0.0
        devices_live_data[clean_id]["importEnergy"] = 0.0
        devices_live_data[clean_id]["exportEnergy"] = 0.0
        devices_live_data[clean_id]["battChgEnergy"] = 0.0
        devices_live_data[clean_id]["battDisEnergy"] = 0.0
        devices_live_data[clean_id]["solarPeak"] = 0.0

    if clean_id in device_daily_archives:
        device_daily_archives[clean_id] = []

    devices_pending_commands[clean_id] = {"command": "CLEAR_ALL_RESET", "target": "all"}

    logger.info(f"FULL SYSTEM RESET EXECUTED FOR: {clean_id}")
    return {
        "status": "success",
        "device_id": clean_id,
        "message": "All energy counters, peaks, and history cleared successfully across Cloud and Hardware."
    }


# ============================================================
# FIRMWARE AUTO-UPDATE CHECK ENDPOINT (Direct Cloud OTA v1.20)
# ============================================================

@app.get("/api/firmware-check")
def check_firmware_update():
    return {
        "latest_version": "v1.20",  
        "firmware_url": "https://github.com/izhartecnicion-prog/video-downloader-backend/releases/download/v1.20/TEST_TIGER.ino.bin" 
    }


# Fallback Single-Device Endpoints
@app.put("/api/live")
async def legacy_update_inverter_live(request: Request):
    return await update_device_live("default", request)

@app.get("/api/live")
async def legacy_get_inverter_live():
    return devices_live_data.get("default", {})

@app.get("/api/command")
async def legacy_poll_inverter_command():
    return await poll_device_command("default")

@app.post("/api/command")
async def legacy_queue_inverter_command(request: Request):
    return await queue_device_command("default", request)


# ============================================================
# HEALTH & ROOT
# ============================================================

@app.get("/")
def root():
    ffmpeg = get_ffmpeg_path()
    return {
        "status": "online",
        "service": "Universal Social Video Downloader & SSTE Multi-Inverter API",
        "version": "4.0.0",
        "yt_dlp": yt_dlp.version.__version__,
        "ffmpeg": bool(ffmpeg),
        "ffmpeg_path": ffmpeg,
        "connected_inverters": len(devices_live_data),
        "endpoints": [
            "POST /api/extract",
            "GET /api/download",
            "GET /api/stream",
            "GET /api/health",
            "GET /api/device/check/{device_id}",
            "GET /api/devices",
            "PUT /api/{device_id}/live",
            "GET /api/{device_id}/live",
            "GET /api/{device_id}/archive",
            "GET /api/{device_id}/command",
            "POST /api/{device_id}/command",
            "DELETE /api/admin/device/{device_id}",
            "DELETE /api/admin/device/{device_id}/reset-all",
            "GET /api/firmware-check",
            "GET|POST /api/search  (search by name)",
            "POST /api/extract  (URL or name)",
            "GET /api/auth/status",
            "POST /api/auth/forgot-password",
            "POST /api/auth/reset-password",
            "POST /api/auth/change-password",
            "POST /api/v1/chat  (Firmware Studio AI chat)"
        ]
    }


@app.get("/api/health")
def health():
    ffmpeg = get_ffmpeg_path()
    return {
        "status": "ok",
        "service": "video-downloader-backend",
        "version": "4.0.0",
        "yt_dlp": yt_dlp.version.__version__,
        "ffmpeg": bool(ffmpeg),
        "active_devices": list(devices_live_data.keys()),
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "gemini_model": GEMINI_MODEL
    }


# ============================================================
# SMART SEARCH (VIDEO NAME OR URL)
# ============================================================

# NOTE: Sirf wahi platforms jin ka yt-dlp search support deta hai.
# TikTok / Instagram / Facebook ka naam-search possible nahi, un ka URL paste karna hota hai.
SEARCH_PROVIDERS: Dict[str, str] = {
    "youtube": "ytsearch",
    "soundcloud": "scsearch",
    "bilibili": "bilisearch",
}

SEARCH_CACHE_TTL = 300
SEARCH_CACHE_MAX = 200
_search_cache: Dict[str, Any] = {}


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    platform: str = "youtube"


def looks_like_url(text: Any) -> bool:
    value = safe_str(text)
    if not value or " " in value:
        return False
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "//", "www.")):
        return True
    return bool(re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/|\?|#|$)", lowered))


def _normalize_search_entry(entry: Dict[str, Any], provider: str) -> Optional[Dict[str, Any]]:
    if not entry or not isinstance(entry, dict):
        return None

    video_id = safe_str(entry.get("id"))
    url = safe_str(entry.get("webpage_url") or entry.get("url"))

    if provider == "youtube" and not url.startswith("http") and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"

    if not url.startswith("http"):
        return None

    thumbnail = safe_str(entry.get("thumbnail"))
    if not thumbnail:
        thumbs = entry.get("thumbnails") or []
        if isinstance(thumbs, list) and thumbs and isinstance(thumbs[-1], dict):
            thumbnail = safe_str(thumbs[-1].get("url"))
    if not thumbnail and provider == "youtube" and video_id:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    duration = safe_int(entry.get("duration"), 0)

    return {
        "id": video_id,
        "title": safe_str(entry.get("title"), "Untitled"),
        "url": url,
        "thumbnail": thumbnail,
        "duration": duration,
        "duration_formatted": (
            f"{duration // 60:02d}:{duration % 60:02d}" if duration > 0 else ""
        ),
        "author": safe_str(
            entry.get("uploader") or entry.get("channel") or entry.get("creator"), ""
        ),
        "view_count": safe_int(entry.get("view_count"), 0),
        "platform": provider,
    }


def run_search(query: str, limit: int = 10, platform: str = "youtube") -> Dict[str, Any]:
    query = safe_str(query)
    if not query:
        raise HTTPException(status_code=400, detail="Search text is empty.")

    limit = max(1, min(safe_int(limit, 10), 30))
    platform_key = safe_str(platform, "youtube").lower()

    if platform_key == "all":
        providers = list(SEARCH_PROVIDERS.items())
    elif platform_key in SEARCH_PROVIDERS:
        providers = [(platform_key, SEARCH_PROVIDERS[platform_key])]
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported search platform '{platform_key}'. Use: all, {', '.join(SEARCH_PROVIDERS)}."
        )

    cache_key = f"{platform_key}|{limit}|{query.lower()}"
    cached = _search_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < SEARCH_CACHE_TTL:
        return cached[1]

    per_provider = limit if len(providers) == 1 else max(3, limit // len(providers))
    results: List[Dict[str, Any]] = []
    errors: List[str] = []

    for name, prefix in providers:
        try:
            opts = get_extract_options()
            opts.update({
                "extract_flat": True,
                "noplaylist": False,
                "playlistend": per_provider,
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"{prefix}{per_provider}:{query}", download=False)

            for entry in (info or {}).get("entries") or []:
                item = _normalize_search_entry(entry, name)
                if item:
                    results.append(item)
        except Exception as search_error:
            logger.warning(f"SEARCH FAILED | provider={name} | {search_error}")
            errors.append(f"{name}: {search_error}")

    if not results:
        if errors:
            raise HTTPException(status_code=502, detail="Search failed. Please try again later.")
        raise HTTPException(status_code=404, detail="No results found.")

    payload = {
        "type": "search",
        "query": query,
        "platform": platform_key,
        "count": len(results),
        "results": results,
    }

    if len(_search_cache) >= SEARCH_CACHE_MAX:
        oldest = sorted(_search_cache.items(), key=lambda kv: kv[1][0])[: SEARCH_CACHE_MAX // 2]
        for key, _ in oldest:
            _search_cache.pop(key, None)
    _search_cache[cache_key] = (time.time(), payload)

    return payload


def resolve_to_url(text: str) -> str:
    """URL ho to clean karo, naam ho to pehla search result ka URL return karo."""
    if looks_like_url(text):
        return clean_url(text)
    result = run_search(text, limit=1, platform="youtube")
    return result["results"][0]["url"]


@app.get("/api/search")
@app.get("/api/v1/search")
def search_videos_get(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=30),
    platform: str = Query("youtube")
):
    return run_search(q, limit, platform)


@app.post("/api/search")
@app.post("/api/v1/search")
def search_videos_post(data: SearchRequest):
    return run_search(data.query, data.limit, data.platform)


# ============================================================
# AUTH: FORGOT / RESET / CHANGE PASSWORD (FIREBASE ADMIN SDK)
# ============================================================
#
# Asal masla: app naya password sirf database me likhti thi, jab k login
# Firebase Authentication se hota hai. Password sirf Admin SDK ke
# auth.update_user(uid, password=...) se badalta hai, aur wo server par hi chal sakta hai.
#
# Render Environment Variables:
#   FIREBASE_SERVICE_ACCOUNT_JSON = poori service-account JSON
#   BREVO_API_KEY + MAIL_FROM  (recommended: HTTPS email API)
#   ya SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
#   PASSWORD_MIN_LENGTH (optional, default 6)

PASSWORD_MIN_LENGTH = max(6, safe_int(os.environ.get("PASSWORD_MIN_LENGTH"), 6))
OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = 60
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# In-memory store: Render restart ya multiple instances par OTP reset ho jata hai.
_otp_store: Dict[str, Dict[str, Any]] = {}
_firebase_lock = threading.Lock()
_firebase_auth_module: Any = None


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    id_token: str
    new_password: str


def get_firebase_auth() -> Any:
    """Firebase Admin lazily initialise hota hai, taake env missing ho to downloader/gateway na ruke."""
    global _firebase_auth_module
    if _firebase_auth_module is not None:
        return _firebase_auth_module

    with _firebase_lock:
        if _firebase_auth_module is not None:
            return _firebase_auth_module

        try:
            import firebase_admin
            from firebase_admin import auth as fb_auth
            from firebase_admin import credentials
        except ImportError:
            logger.error("firebase-admin package is not installed")
            raise HTTPException(status_code=503, detail="Password service unavailable (server setup incomplete).")

        try:
            if not firebase_admin._apps:
                raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
                file_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE", "").strip()

                if raw:
                    cred = credentials.Certificate(json.loads(raw, strict=False))
                elif file_path and os.path.isfile(file_path):
                    cred = credentials.Certificate(file_path)
                else:
                    logger.error("Firebase credentials env variable is missing")
                    raise HTTPException(status_code=503, detail="Password service unavailable (server setup incomplete).")

                firebase_admin.initialize_app(cred)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Firebase initialisation failed")
            raise HTTPException(status_code=503, detail="Password service unavailable (server setup incomplete).")

        _firebase_auth_module = fb_auth
        return _firebase_auth_module


def email_is_configured() -> bool:
    if os.environ.get("BREVO_API_KEY") and (os.environ.get("MAIL_FROM") or os.environ.get("SMTP_FROM")):
        return True
    return bool(
        os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS")
    )


def send_reset_email(to_email: str, otp: str) -> None:
    subject = "SSTE / EMP Password Reset Code"
    body = (
        f"Your password reset code is: {otp}\n\n"
        f"This code expires in {OTP_TTL_SECONDS // 60} minutes.\n"
        "If you did not request this, you can ignore this email."
    )
    sender_email = (
        os.environ.get("MAIL_FROM") or os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or ""
    )
    sender_name = os.environ.get("MAIL_FROM_NAME", "SSTE Solar Smart Energy")

    brevo_key = os.environ.get("BREVO_API_KEY", "").strip()
    if brevo_key:
        if not sender_email:
            raise RuntimeError("MAIL_FROM is not set")
        payload = json.dumps({
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": body,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload,
            headers={
                "api-key": brevo_key,
                "Content-Type": "application/json",
                "accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"Brevo returned HTTP {resp.status}")
        return

    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "")
    if not (host and user and password):
        raise RuntimeError("Email is not configured")

    port = safe_int(os.environ.get("SMTP_PORT"), 587)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_email or user}>"
    msg["To"] = to_email
    msg.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)


def _normalize_email(value: Any) -> str:
    email = safe_str(value).lower()
    if not EMAIL_REGEX.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    return email


def _validate_new_password(value: Any) -> str:
    password = value if isinstance(value, str) else ""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    if len(password) > 128:
        raise HTTPException(status_code=400, detail="Password is too long.")
    return password


def _hash_otp(email: str, otp: str) -> str:
    return hashlib.sha256(f"{email}:{otp}".encode("utf-8")).hexdigest()


def _purge_expired_otps() -> None:
    now = time.time()
    for key in [k for k, v in _otp_store.items() if now > v.get("exp", 0)]:
        _otp_store.pop(key, None)


@app.get("/api/auth/status")
def auth_status():
    """Render par setup check karne ke liye (koi secret return nahi hota)."""
    try:
        import firebase_admin  # noqa: F401
        sdk_installed = True
    except ImportError:
        sdk_installed = False

    return {
        "firebase_admin_installed": sdk_installed,
        "firebase_credentials_set": bool(
            os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON") or os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE")
        ),
        "email_configured": email_is_configured(),
        "password_min_length": PASSWORD_MIN_LENGTH,
    }


@app.post("/api/auth/forgot-password")
def forgot_password(data: ForgotPasswordRequest):
    email = _normalize_email(data.email)
    fb_auth = get_firebase_auth()

    if not email_is_configured():
        logger.error("Forgot-password requested but email is not configured")
        raise HTTPException(status_code=503, detail="Password service unavailable (email not configured).")

    _purge_expired_otps()
    existing = _otp_store.get(email)
    if existing and (time.time() - existing["sent_at"]) < OTP_RESEND_COOLDOWN:
        raise HTTPException(status_code=429, detail="Please wait a minute before requesting another code.")

    generic = {"status": "ok", "detail": "If this email is registered, a reset code has been sent."}
    now = time.time()

    try:
        fb_auth.get_user_by_email(email)
    except fb_auth.UserNotFoundError:
        # Account enumeration se bachao: same response aur same cooldown
        _otp_store[email] = {"hash": None, "exp": now + OTP_TTL_SECONDS, "sent_at": now, "attempts": 0}
        return generic
    except Exception:
        logger.exception("Firebase user lookup failed")
        raise HTTPException(status_code=502, detail="Could not process the request. Try again later.")

    otp = f"{secrets.randbelow(1_000_000):06d}"
    try:
        send_reset_email(email, otp)
    except Exception:
        logger.exception("Reset email sending failed")
        raise HTTPException(status_code=502, detail="Could not send the reset email. Try again later.")

    _otp_store[email] = {
        "hash": _hash_otp(email, otp),
        "exp": now + OTP_TTL_SECONDS,
        "sent_at": now,
        "attempts": 0,
    }
    logger.info("PASSWORD RESET CODE SENT")
    return generic


@app.post("/api/auth/reset-password")
def reset_password(data: ResetPasswordRequest):
    email = _normalize_email(data.email)
    otp = safe_str(data.otp)
    new_password = _validate_new_password(data.new_password)

    _purge_expired_otps()
    invalid = HTTPException(status_code=400, detail="Invalid or expired code.")

    entry = _otp_store.get(email)
    if not entry or not entry.get("hash") or time.time() > entry["exp"]:
        raise invalid

    entry["attempts"] += 1
    if entry["attempts"] > OTP_MAX_ATTEMPTS:
        _otp_store.pop(email, None)
        raise HTTPException(status_code=429, detail="Too many attempts. Please request a new code.")

    if not hmac.compare_digest(entry["hash"], _hash_otp(email, otp)):
        raise invalid

    fb_auth = get_firebase_auth()
    try:
        user = fb_auth.get_user_by_email(email)
        fb_auth.update_user(user.uid, password=new_password)   # <-- asal fix
        fb_auth.revoke_refresh_tokens(user.uid)                 # purane sessions khatam
    except fb_auth.UserNotFoundError:
        _otp_store.pop(email, None)
        raise invalid
    except Exception:
        logger.exception("Firebase password update failed")
        raise HTTPException(status_code=500, detail="Password update failed. Please try again.")

    _otp_store.pop(email, None)
    logger.info("PASSWORD RESET COMPLETED")
    return {"status": "ok", "detail": "Password updated. Please log in with your new password."}


@app.post("/api/auth/change-password")
def change_password(data: ChangePasswordRequest):
    """Logged-in user ke liye: app Firebase ID token bhejti hai + naya password."""
    new_password = _validate_new_password(data.new_password)
    fb_auth = get_firebase_auth()

    try:
        decoded = fb_auth.verify_id_token(safe_str(data.id_token), check_revoked=True)
        uid = decoded["uid"]
    except Exception:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    try:
        fb_auth.update_user(uid, password=new_password)
        fb_auth.revoke_refresh_tokens(uid)
    except Exception:
        logger.exception("Firebase password change failed")
        raise HTTPException(status_code=500, detail="Password update failed. Please try again.")

    return {"status": "ok", "detail": "Password changed. Please log in again."}


# ============================================================
# EXTRACT & DOWNLOAD (URL OR NAME)
# ============================================================

@app.post("/api/extract")
@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):
    try:
        # Agar user ne URL ki jagah naam likha hai to search results return hon
        if not looks_like_url(data.url):
            logger.info(f"EXTRACT -> SEARCH MODE: {data.url}")
            return run_search(data.url, limit=15, platform="youtube")

        original_url = clean_url(data.url)
        logger.info(f"EXTRACT REQUEST: {original_url}")

        opts = get_extract_options()

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(original_url, download=False)

        if not info:
            raise HTTPException(
                status_code=400,
                detail="Unable to extract video information."
            )

        title = safe_str(info.get("title"), "Social Media Video")
        duration = safe_int(info.get("duration"), 0)
        thumbnail = safe_str(info.get("thumbnail"), "")
        platform = safe_str(info.get("extractor_key") or info.get("extractor"), "Unknown")
        uploader = safe_str(info.get("uploader") or info.get("creator") or info.get("channel"), "")

        formats_list = []
        formats = info.get("formats") or []

        for f in formats:
            try:
                format_id = safe_str(f.get("format_id"), "best")
                height = safe_int(f.get("height"), 0)
                width = safe_int(f.get("width"), 0)
                ext = safe_str(f.get("ext"), "mp4").lower()
                vcodec = safe_str(f.get("vcodec"), "none")
                acodec = safe_str(f.get("acodec"), "none")
                filesize = safe_int(f.get("filesize") or f.get("filesize_approx"), 0)
                fps = safe_int(f.get("fps"), 0)
                abr = safe_float(f.get("abr"), 0)
                vbr = safe_float(f.get("vbr"), 0)
                bitrate = abr or vbr
                audio = is_audio_format(f)
                video = is_video_format(f)

                if not audio and not video:
                    continue

                if audio:
                    if bitrate > 0:
                        resolution = f"{int(bitrate)} kbps"
                    else:
                        resolution = "Audio"
                elif height > 0:
                    if height >= 2160:
                        resolution = "2160p 4K"
                    elif height >= 1440:
                        resolution = "1440p 2K"
                    elif height >= 1080:
                        resolution = "1080p Full HD"
                    elif height >= 720:
                        resolution = "720p HD"
                    elif height >= 480:
                        resolution = "480p"
                    else:
                        resolution = f"{height}p"
                elif width > 0 and height > 0:
                    resolution = f"{width}x{height}"
                else:
                    resolution = "Auto"

                formats_list.append({
                    "id": format_id,
                    "format_id": format_id,
                    "resolution": resolution,
                    "quality_label": resolution,
                    "height": height,
                    "width": width,
                    "ext": ext,
                    "extension": ext,
                    "filesize": filesize,
                    "filesize_bytes": filesize,
                    "filesize_mb": format_size(filesize),
                    "url": None,
                    "direct_url": None,
                    "is_audio": audio,
                    "is_video": video,
                    "vcodec": vcodec,
                    "acodec": acodec,
                    "bitrate": int(bitrate),
                    "fps": fps
                })

            except Exception as format_error:
                logger.warning(f"FORMAT SKIPPED: {format_error}")
                continue

        if not formats_list:
            formats_list.append({
                "id": "best",
                "format_id": "best",
                "resolution": "Best Quality",
                "quality_label": "Best Quality",
                "height": safe_int(info.get("height"), 0),
                "width": safe_int(info.get("width"), 0),
                "ext": "mp4",
                "extension": "mp4",
                "filesize": 0,
                "filesize_bytes": 0,
                "filesize_mb": None,
                "url": None,
                "direct_url": None,
                "is_audio": False,
                "is_video": True,
                "vcodec": "unknown",
                "acodec": "unknown",
                "bitrate": 0,
                "fps": 0
            })

        unique_formats = {}
        for item in formats_list:
            key = (item["format_id"], item["is_audio"])
            if key not in unique_formats:
                unique_formats[key] = item

        formats_list = list(unique_formats.values())
        formats_list.sort(
            key=lambda x: (
                0 if not x.get("is_audio") else 1,
                -safe_int(x.get("height"), 0),
                -safe_int(x.get("bitrate"), 0)
            )
        )

        return {
            "type": "video",
            "title": title,
            "thumbnail": thumbnail,
            "duration": duration,
            "duration_seconds": duration,
            "duration_formatted": (
                f"{duration // 60:02d}:{duration % 60:02d}"
                if duration > 0
                else "00:00"
            ),
            "platform": platform,
            "author": uploader,
            "uploader": uploader,
            "original_url": original_url,
            "url": original_url,
            "formats": formats_list,
            "video_formats": [x for x in formats_list if x.get("is_video")],
            "audio_formats": [x for x in formats_list if x.get("is_audio")]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("EXTRACTION ERROR")
        message = str(e)
        if "login" in message.lower():
            message = "This media requires login or is not publicly accessible."
        elif "sign in" in message.lower() or "bot" in message.lower():
            message = "Platform protection blocked this request. Please try another URL."
        raise HTTPException(status_code=400, detail=f"Extraction failed: {message}")


@app.get("/api/download")
@app.get("/api/v1/download")
@app.get("/api/stream")
@app.get("/api/v1/stream")
def download_video(
    url: str = Query(...),
    format_id: str = Query("best")
):
    temp_dir = tempfile.mkdtemp(prefix="video_dl_")
    job_id = uuid.uuid4().hex

    try:
        original_url = resolve_to_url(url)
        requested_format = safe_str(format_id, "best")

        ffmpeg_path = get_ffmpeg_path()
        format_selector = get_format_selector(requested_format)

        output_template = os.path.join(temp_dir, f"{job_id}.%(ext)s")
        opts = get_base_options()
        opts.update({
            "format": format_selector,
            "outtmpl": output_template,
            "noplaylist": True,
            "merge_output_format": "mp4",
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 5,
            "file_access_retries": 5,
        })

        if ffmpeg_path:
            opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
        else:
            if requested_format.lower() in {"best", "auto", "default", ""}:
                opts["format"] = "b[ext=mp4]/b/best"

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(original_url, download=True)

        if not info:
            raise HTTPException(status_code=404, detail="Could not extract media from this URL.")

        possible_files = []
        for root, dirs, files in os.walk(temp_dir):
            for filename in files:
                full_path = os.path.join(root, filename)
                if os.path.isfile(full_path):
                    possible_files.append(full_path)

        possible_files = [f for f in possible_files if os.path.getsize(f) > 1024]

        if not possible_files:
            raise HTTPException(status_code=500, detail="yt-dlp completed but no media file was created.")

        mp4_files = [f for f in possible_files if f.lower().endswith(".mp4")]
        if mp4_files:
            final_file = max(mp4_files, key=os.path.getsize)
        else:
            final_file = max(possible_files, key=os.path.getsize)

        if not os.path.exists(final_file):
            raise HTTPException(status_code=500, detail="Downloaded media file not found.")

        file_size = os.path.getsize(final_file)
        if file_size < 1024:
            raise HTTPException(status_code=500, detail="Downloaded file is empty or invalid.")

        actual_ext = os.path.splitext(final_file)[1].lower().replace(".", "")
        if not actual_ext:
            actual_ext = "mp4"

        title = safe_filename(info.get("title", "video"))
        title = re.sub(r"\.(mp4|webm|mkv|mov|avi|m4a|mp3)$", "", title, flags=re.IGNORECASE)

        if actual_ext == "mp4":
            media_type = "video/mp4"
        elif actual_ext == "webm":
            media_type = "video/webm"
        elif actual_ext == "mkv":
            media_type = "video/x-matroska"
        elif actual_ext == "m4a":
            media_type = "audio/mp4"
        elif actual_ext == "mp3":
            media_type = "audio/mpeg"
        else:
            media_type = "application/octet-stream"

        download_name = f"{title}.{actual_ext}"

        return FileResponse(
            path=final_file,
            media_type=media_type,
            filename=download_name,
            background=BackgroundTask(shutil.rmtree, temp_dir, True)
        )

    except HTTPException:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        raise

    except Exception as e:
        logger.exception(f"DOWNLOAD ERROR | JOB={job_id}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        message = str(e)
        raise HTTPException(status_code=500, detail=f"Video download failed: {message}")


# ============================================================
# FIRMWARE STUDIO - AI CHAT (GEMINI)
# Render > Environment: GEMINI_API_KEY (zaruri), GEMINI_MODEL (optional)
# ============================================================

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash").strip()
GEMINI_MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "30000"))

_gemini_client = None
_gemini_lock = threading.Lock()


def get_gemini_client():
    """Lazy init: google-genai na bhi ho to baaki service (downloader/inverter) chalti rahe."""
    global _gemini_client
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY Render Environment me set nahi hai.")
    with _gemini_lock:
        if _gemini_client is None:
            try:
                from google import genai
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"google-genai install nahi hai. requirements.txt me 'google-genai' add karo. ({e})"
                )
            _gemini_client = genai.Client(api_key=api_key)
        return _gemini_client


# ---------- emoji / mojibake sanitizer (HTML numeric entities) ----------

_EMOJI_ENTITIES = {
    "\U0001F4E1": "&#128225;",
    "\U0001F39B": "&#127899;&#65039;",
    "\u2600": "&#9728;&#65039;",
    "\U0001F50B": "&#128267;",
    "\U0001F3E0": "&#127968;",
    "\U0001F50C": "&#128268;",
    "\U0001F4CD": "&#128205;",
    "\u26A1": "&#9889;",
    "\U0001F504": "&#128260;",
    "\u2699": "&#9881;&#65039;",
    "\u2715": "&#10005;",
    "\u274C": "&#10005;",
    "\u2713": "&#10003;",
    "\u2714": "&#10003;",
    "\u2139": "&#8505;&#65039;",
    "\U0001F4E5": "&#128229;",
    "\u2022": "&#8226;",
    "\u00B0": "&#176;",
    "\U0001F5D1": "&#128465;&#65039;",
    "\u2B05": "&#11013;&#65039;",
    "\U0001F50D": "&#128269;",
    "\u26A0": "&#9888;&#65039;",
}


def _make_mojibake(text: str, codec: str) -> str:
    out = []
    for b in text.encode("utf-8"):
        try:
            out.append(bytes([b]).decode(codec))
        except Exception:
            out.append(chr(b))
    return "".join(out)


def _build_sanitizer_pairs():
    pairs = {}
    for ch, entity in _EMOJI_ENTITIES.items():
        for variant in (ch + "\ufe0f", ch):
            pairs[variant] = entity
            for codec in ("cp1252", "latin-1"):
                moji = _make_mojibake(variant, codec)
                if moji != variant:
                    pairs[moji] = entity
    # lambay pattern pehle replace hon (variation selector wale)
    return sorted(pairs.items(), key=lambda kv: len(kv[0]), reverse=True)


_SANITIZER_PAIRS = _build_sanitizer_pairs()


def clean_mojibake_and_emojis(code: str) -> str:
    if not code:
        return code
    for pattern, entity in _SANITIZER_PAIRS:
        if pattern in code:
            code = code.replace(pattern, entity)
    return code


# ---------- request models ----------

class ChatFile(BaseModel):
    name: str
    content: str


class ChatImage(BaseModel):
    mime: str = "image/png"
    b64: str


class ChatTurn(BaseModel):
    role: str  # "user" | "ai"
    text: str


class ChatRequest(BaseModel):
    mcu: str = "ESP32"
    message: str
    files: List[ChatFile] = []
    images: List[ChatImage] = []
    history: List[ChatTurn] = []


MASTER_PROMPT = """
You are FIRMWARE STUDIO MASTER AI: a senior embedded engineer (ESP32, ESP8266,
Arduino, solar/inverter monitoring, Firebase, HTML-in-.ino dashboards).
You TALK with the developer like a human colleague, in the same language mix
they use (Roman Urdu / English). You understand, explain, ask, diagnose, resolve.

INPUT: user message, chat history, up to 4 .ino files (each with a name),
optional screenshots / compiler logs.

HOW YOU WORK
1. Read EVERY file fully before answering. Map functions, pins, protocol, HTML blocks.
2. If the user only asks a question ("ye function kya karta hai?", "kya ye sahi hai?"),
   ANSWER it in "reply" and return no patches.
3. If the user wants a change, explain in 2-5 short lines WHAT you found and WHAT you
   will change, then give surgical patches. Never rewrite whole files.
4. Multi-file: user may say "file A se function X copy karke file B me replace karo".
   Read X from file A exactly, then patch file B. Every patch names its target "file".
5. You can ADD a function (find = a unique anchor line, replace = anchor + new code),
   REPLACE a function (find = the whole old function, copied exactly), or DELETE one
   (replace = "").
6. If something is genuinely ambiguous, ask ONE short question in "reply" and return
   no patches. Otherwise decide and act.
7. Check compile-readiness: braces, semicolons, includes, duplicate definitions,
   missing prototypes, wrong pins for the MCU, blocking delay() in loop.
8. Zero Code Loss: untouched lines are never retyped. "find" must be copied
   character-for-character from the file (same spaces, same indentation) and must be
   UNIQUE in that file. If a line repeats, include a neighbouring line to make it unique.
9. ICONS: never output raw emoji. Use only: sun &#9728;&#65039; battery &#128267;
   antenna &#128225; sliders &#127899;&#65039; home &#127968; plug &#128268;
   pin &#128205; bolt &#9889; refresh &#128260; gear &#9881;&#65039; check &#10003;
   close &#10005; info &#8505;&#65039; inbox &#128229; bullet &#8226; degree &#176;
   trash &#128465;&#65039; left &#11013;&#65039;. Existing entities in the file are copied
   unchanged. Garbled sequences (mojibake, starting with characters like the Latin
   letters with accents seen in broken emoji) found in the input are a bug: fix them.
10. ESP32<->ESP8266 port rules: WiFi.h<->ESP8266WiFi.h, WebServer.h<->ESP8266WebServer.h,
   HTTPClient.h<->ESP8266HTTPClient.h, ESPmDNS.h<->ESP8266mDNS.h,
   Serial2(16,17)<->SoftwareSerial(3,1), MAX_HISTORY 288<->144,
   client.setBufferSizes(512,512) on 8266. Nothing else changes.

OUTPUT: ONLY valid JSON, no markdown fences:
{
 "reply": "human explanation / answer / question for the chat page",
 "patches": [ {"file": "name.ino", "find": "...", "replace": "...", "why": "short reason"} ],
 "report": {
    "summary": "one paragraph",
    "issues_found": [ {"file":"","line_hint":"","problem":"","fix":""} ],
    "compile_ready": true
 },
 "alerts": [ {"level": "error|warning|info", "file": "", "message": ""} ]
}
Always include all four keys (empty list / empty string if nothing).
"""


def _parse_ai_json(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            return json.loads(t[start:end + 1])
        raise


def _validate_patches(patches: Any, files: List[ChatFile]):
    by_name = {f.name: f.content for f in files}
    ok: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []

    if not isinstance(patches, list):
        return ok, alerts

    for p in patches:
        if not isinstance(p, dict):
            continue
        fname = safe_str(p.get("file"))
        find = p.get("find")
        src = by_name.get(fname)

        if src is None:
            alerts.append({"level": "error", "file": fname,
                           "message": "Patch target file nahi mili. Patch skip hua."})
            continue
        if not isinstance(find, str) or not find:
            alerts.append({"level": "error", "file": fname,
                           "message": "Patch ka 'find' khali hai. Patch skip hua."})
            continue

        count = src.count(find)
        if count != 1:
            alerts.append({"level": "error", "file": fname,
                           "message": f"'find' text file me {count} baar mila (sirf 1 baar chahiye). Patch skip hua."})
            continue

        ok.append(p)

    return ok, alerts


def _call_gemini(client, model: str, contents):
    from google.genai import types
    return client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=MASTER_PROMPT,
            temperature=0.2,
            max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
        ),
    )


# NOTE: plain "def" (async nahi) taake lambi AI call inverter live endpoints ko block na kare.
@app.post("/api/v1/chat")
@app.post("/api/chat")
def firmware_chat(req: ChatRequest):
    if len(req.files) > 4:
        raise HTTPException(status_code=400, detail="Ek waqt me maximum 4 files allowed hain.")
    if not req.message.strip() and not req.files and not req.images:
        raise HTTPException(status_code=400, detail="Message ya file dena zaruri hai.")

    client = get_gemini_client()

    from google.genai import types

    parts = []
    for h in req.history[-12:]:
        parts.append(types.Part.from_text(text=f"[{h.role.upper()}]: {h.text}"))

    for f in req.files:
        parts.append(types.Part.from_text(
            text=f"=== FILE: {f.name} ===\n{f.content}\n=== END FILE: {f.name} ==="
        ))

    for im in req.images:
        try:
            raw = base64.b64decode(im.b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Screenshot ka base64 data kharab hai.")
        parts.append(types.Part.from_bytes(data=raw, mime_type=im.mime or "image/png"))

    parts.append(types.Part.from_text(text=f"[TARGET MCU]: {req.mcu}\n[USER]: {req.message}"))
    contents = [types.Content(role="user", parts=parts)]

    used_model = GEMINI_MODEL
    try:
        try:
            resp = _call_gemini(client, used_model, contents)
        except Exception as primary_error:
            if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != used_model:
                logger.warning(f"MODEL {used_model} FAILED ({primary_error}); fallback {GEMINI_FALLBACK_MODEL}")
                used_model = GEMINI_FALLBACK_MODEL
                resp = _call_gemini(client, used_model, contents)
            else:
                raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("GEMINI CHAT ERROR")
        raise HTTPException(status_code=500, detail=f"AI engine failed: {e}")

    raw_text = ""
    try:
        raw_text = resp.text or ""
    except Exception:
        raw_text = ""

    parse_alerts: List[Dict[str, Any]] = []
    try:
        data = _parse_ai_json(raw_text)
        if not isinstance(data, dict):
            raise ValueError("AI response object nahi hai")
    except Exception:
        data = {"reply": raw_text or "AI ne khali jawab diya. Dobara try karo.",
                "patches": [], "report": {}, "alerts": []}
        parse_alerts.append({"level": "warning", "file": "",
                             "message": "AI ka jawab JSON format me nahi tha, isliye patches nahi bane."})

    patches, bad = _validate_patches(data.get("patches", []), req.files)
    for p in patches:
        p["replace"] = clean_mojibake_and_emojis(safe_str(p.get("replace"), "") if p.get("replace") is not None else "")
        p["why"] = safe_str(p.get("why"), "")

    ai_alerts = data.get("alerts", [])
    if not isinstance(ai_alerts, list):
        ai_alerts = []

    report = data.get("report", {})
    if not isinstance(report, dict):
        report = {}

    return {
        "status": "success",
        "model": used_model,
        "reply": safe_str(data.get("reply"), ""),
        "patches": patches,
        "report": report,
        "alerts": ai_alerts + bad + parse_alerts,
    }


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "detail": str(exc.detail),
            "status_code": exc.status_code
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.exception("UNHANDLED SERVER ERROR")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "detail": "Internal server error",
            "status_code": 500
        }
    )


# ============================================================
# SERVER ENTRY POINT
# ============================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
