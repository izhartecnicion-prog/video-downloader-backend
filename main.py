from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
import yt_dlp
import requests
import logging
import tempfile
import os
import uuid
import re
import shutil
import threading
from typing import Optional, List, Dict, Any

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
# FASTAPI
# ============================================================
app = FastAPI(
    title="Universal Social Video Downloader API",
    version="3.1.0",
    description="Universal social media video/audio downloader"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
# HELPERS
# ============================================================
def clean_url(raw_url: str) -> str:
    """Clean, unwrap and strip heavy tracking parameters from URL"""
    if not raw_url:
        raise ValueError("URL is empty")

    url = raw_url.strip().strip("'").strip('"')

    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    # Remove bulky tracking query params for TikTok & IG while preserving short links
    if "tiktok.com" in url:
        # Match base tiktok photo or video URL
        match = re.search(r'(https?://(?:www\.|vt\.|vm\.)?tiktok\.com/(?:@[^/]+/(?:video|photo)/\d+|[A-Za-z0-9_-]+))', url)
        if match:
            url = match.group(1)

    return url

def safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(float(value)) if value else default
        return default
    except Exception:
        return default

def safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value).strip()
    except Exception:
        return default

def safe_filename(name: str) -> str:
    name = safe_str(name, "video")
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = name.strip()
    return (name if name else "video")[:150]

def cleanup_temp_dir(temp_dir: str, delay: float = 30):
    def delayed_cleanup():
        import time
        time.sleep(delay)
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                logger.info(f"CLEANUP: Removed temp dir {temp_dir}")
        except Exception as e:
            logger.error(f"CLEANUP ERROR: {e}")
    
    thread = threading.Thread(target=delayed_cleanup, daemon=True)
    thread.start()

# ============================================================
# YT-DLP OPTIONS
# ============================================================
def get_extract_options():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 60,
        "retries": 5,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

# ============================================================
# HEALTH CHECK
# ============================================================
@app.get("/")
def health_check():
    return {
        "status": "online",
        "message": "Universal Social Video Downloader API is active",
        "version": "3.1.0"
    }

# ============================================================
# EXTRACT ENDPOINT
# ============================================================
@app.post("/api/extract")
@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):
    try:
        original_url = clean_url(data.url)
        logger.info(f"🔍 EXTRACT REQUEST: {original_url}")

        opts = get_extract_options()
        opts["skip_download"] = True

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(original_url, download=False)

        if not info:
            raise HTTPException(status_code=400, detail="Unable to extract media information")

        title = safe_str(info.get("title"), "Social Media Content")
        duration = safe_int(info.get("duration"), 0)
        thumbnail = safe_str(info.get("thumbnail"), "")
        platform = safe_str(info.get("extractor_key") or info.get("extractor"), "Universal")

        formats_list = []
        formats = info.get("formats") or []

        for f in formats:
            try:
                format_id = safe_str(f.get("format_id"), "best")
                height = safe_int(f.get("height"), 0)
                ext = safe_str(f.get("ext"), "mp4").lower()
                vcodec = safe_str(f.get("vcodec"), "none")
                filesize = safe_int(f.get("filesize"), 0)
                is_audio = vcodec == "none" or ext in ["mp3", "m4a", "aac", "opus", "wav"]

                resolution = "Audio MP3" if is_audio else (f"{height}p" if height else "HD")
                filesize_mb = round(filesize / 1048576, 2) if filesize else None

                formats_list.append({
                    "id": format_id,
                    "format_id": format_id,
                    "resolution": resolution,
                    "height": height,
                    "width": safe_int(f.get("width"), 0),
                    "ext": ext,
                    "filesize": filesize,
                    "filesize_mb": filesize_mb,
                    "url": f.get("url"),
                    "is_audio": is_audio
                })
            except Exception:
                continue

        if not formats_list and info.get("url"):
            formats_list.append({
                "id": "best",
                "format_id": "best",
                "resolution": "Best Quality HD",
                "height": 720,
                "width": 1280,
                "ext": "mp4",
                "filesize": 0,
                "filesize_mb": None,
                "url": info.get("url"),
                "is_audio": False
            })

        if not formats_list:
            raise HTTPException(status_code=400, detail="No downloadable streams found for this post")

        return {
            "title": title,
            "thumbnail": thumbnail,
            "duration": duration,
            "platform": platform,
            "formats": formats_list
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("EXTRACTION ERROR")
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

# ============================================================
# STREAM / DOWNLOAD ENDPOINT
# ============================================================
@app.get("/api/download")
@app.get("/api/v1/download")
@app.get("/api/stream")
@app.get("/api/v1/stream")
def download_video(url: str = Query(...), format_id: str = Query("best")):
    try:
        original_url = clean_url(url)
        opts = {
            'format': 'bestvideo+bestaudio/best' if format_id == 'best' else format_id,
            'quiet': True,
            'noplaylist': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(original_url, download=False)
            stream_url = info.get('url')
            if not stream_url and info.get('formats'):
                for f in reversed(info['formats']):
                    if f.get('url'):
                        stream_url = f.get('url')
                        break

            if not stream_url:
                raise HTTPException(status_code=404, detail="Direct stream URL not found")

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Referer': original_url
            }
            req = requests.get(stream_url, headers=headers, stream=True, timeout=60)

            def iter_chunks():
                for chunk in req.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        yield chunk

            resp_headers = {
                "Content-Disposition": 'attachment; filename="video.mp4"',
                "Content-Type": req.headers.get('Content-Type', 'video/mp4')
            }
            if 'Content-Length' in req.headers:
                resp_headers['Content-Length'] = req.headers['Content-Length']

            return StreamingResponse(iter_chunks(), headers=resp_headers, media_type="video/mp4")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
