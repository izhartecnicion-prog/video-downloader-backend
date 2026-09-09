from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import yt_dlp
import logging
import tempfile
import os
import uuid
import re
import shutil
import glob
import time
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
    version="3.5.0",
    description="Universal social media video downloader with Admin-Guarded Multi-Device Inverter IoT Gateway, Midnight Archive & Reset-All"
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

ADMIN_MASTER_KEY = "SAJJAD_ADMIN_786"

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
    if admin_key != ADMIN_MASTER_KEY:
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
    if admin_key != ADMIN_MASTER_KEY:
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
# FIRMWARE AUTO-UPDATE CHECK ENDPOINT
# ============================================================

@app.get("/api/firmware-check")
def check_firmware_update():
    return {
        "latest_version": "v1.19",  # Jab bhi naya update release karna ho, yahan version change kar dein
        "firmware_url": "https://raw.githubusercontent.com/your-username/your-repo/main/firmware.bin" # GitHub raw .bin link
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
        "version": "3.5.0",
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
            "GET /api/firmware-check"
        ]
    }


@app.get("/api/health")
def health():
    ffmpeg = get_ffmpeg_path()
    return {
        "status": "ok",
        "service": "video-downloader-backend",
        "version": "3.5.0",
        "yt_dlp": yt_dlp.version.__version__,
        "ffmpeg": bool(ffmpeg),
        "active_devices": list(devices_live_data.keys())
    }


# ============================================================
# EXTRACT & DOWNLOAD (UNCHANGED)
# ============================================================

@app.post("/api/extract")
@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):
    try:
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
        original_url = clean_url(url)
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
            filename=download_name
        )

    except HTTPException:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        raise

    except Exception as e:
        logger.exception(f"DOWNLOAD ERROR | JOB={job_id}")
        message = str(e)
        raise HTTPException(status_code=500, detail=f"Video download failed: {message}")


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
