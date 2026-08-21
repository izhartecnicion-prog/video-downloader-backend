from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp
import requests
import logging
import re
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
# FASTAPI APP
# ============================================================
app = FastAPI(
    title="Universal Social Video Downloader API",
    version="3.3.0",
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
    """Clean, unwrap and fix TikTok Photo/Video URLs"""
    if not raw_url:
        raise ValueError("URL is empty")

    url = raw_url.strip().strip("'").strip('"')

    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    # Remove query tracking
    if "?" in url:
        url = url.split("?")[0]

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
    return (name if name else "video")[:100]

def extract_tiktok_fallback(url: str) -> Optional[Dict[str, Any]]:
    """Fallback extractor using TikWM API for TikTok Photo/Video posts"""
    try:
        api_url = "https://www.tikwm.com/api/"
        resp = requests.post(api_url, data={"url": url, "count": 12, "cursor": 0, "web": 1, "hd": 1}, timeout=15)
        if resp.status_code == 200:
            res = resp.json()
            if res.get("code") == 0 and "data" in res:
                data = res["data"]
                title = data.get("title") or "TikTok Post"
                cover = data.get("cover") or ""
                duration = data.get("duration") or 0
                formats_list = []

                # Video / HD Video
                if data.get("play"):
                    formats_list.append({
                        "id": "hd",
                        "format_id": "hd",
                        "resolution": "HD No Watermark",
                        "height": 1080,
                        "width": 1920,
                        "ext": "mp4",
                        "filesize": data.get("size") or 0,
                        "filesize_mb": round((data.get("size") or 0) / 1048576, 2) if data.get("size") else None,
                        "url": "https://www.tikwm.com" + data.get("play") if str(data.get("play")).startswith("/") else data.get("play"),
                        "is_audio": False
                    })

                # Audio / Music
                if data.get("music"):
                    formats_list.append({
                        "id": "audio",
                        "format_id": "audio",
                        "resolution": "Audio MP3",
                        "height": 0,
                        "width": 0,
                        "ext": "mp3",
                        "filesize": 0,
                        "filesize_mb": None,
                        "url": "https://www.tikwm.com" + data.get("music") if str(data.get("music")).startswith("/") else data.get("music"),
                        "is_audio": True
                    })

                # If Photo Slides
                if data.get("images"):
                    for idx, img in enumerate(data.get("images")):
                        formats_list.append({
                            "id": f"photo_{idx+1}",
                            "format_id": f"photo_{idx+1}",
                            "resolution": f"Photo Slide {idx+1}",
                            "height": 1080,
                            "width": 1080,
                            "ext": "jpg",
                            "filesize": 0,
                            "filesize_mb": None,
                            "url": img,
                            "is_audio": False
                        })

                if formats_list:
                    return {
                        "title": title,
                        "thumbnail": cover,
                        "duration": duration,
                        "platform": "TikTok",
                        "formats": formats_list
                    }
    except Exception as e:
        logger.warning(f"TikWM Fallback failed: {e}")
    return None

# ============================================================
# ENDPOINTS
# ============================================================
@app.get("/")
def health_check():
    return {
        "status": "online",
        "message": "Universal Social Video Downloader API is active",
        "version": "3.3.0"
    }

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "video-downloader-backend",
        "version": "3.3.0",
        "yt_dlp": yt_dlp.version.__version__
    }

@app.post("/api/extract", response_model=ExtractResponse)
@app.post("/api/v1/extract", response_model=ExtractResponse)
def extract_video_info(data: VideoRequest):
    try:
        target_url = clean_url(data.url)
        logger.info(f"🔍 EXTRACT REQUEST: {target_url}")

        # If it's a TikTok Photo/Slide or TikTok standard link, try TikWM first for 100% success
        if "tiktok.com" in target_url:
            tiktok_data = extract_tiktok_fallback(target_url)
            if tiktok_data:
                logger.info(f"✅ TikTok Extracted via TikWM: {tiktok_data['title']}")
                return tiktok_data

        # Standard yt-dlp Extraction for YouTube, FB, Insta, etc.
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 60,
            "retries": 5,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "skip_download": True
        }

        # Convert /photo/ to /video/ if still going to yt-dlp
        yt_target_url = target_url.replace("/photo/", "/video/")

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(yt_target_url, download=False)

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
                if not f.get("url"):
                    continue

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
            raise HTTPException(status_code=400, detail="No downloadable streams found")

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

@app.get("/api/download")
@app.get("/api/v1/download")
@app.get("/api/stream")
@app.get("/api/v1/stream")
def download_video(url: str = Query(...), format_id: str = Query("best")):
    try:
        target_url = clean_url(url)
        
        # Check if direct media url passed
        if target_url.endswith(".mp4") or target_url.endswith(".mp3") or "tikwm.com" in target_url:
            stream_url = target_url
            download_name = "media.mp4" if not target_url.endswith(".mp3") else "audio.mp3"
            req = requests.get(stream_url, stream=True, timeout=90)
        else:
            yt_target_url = target_url.replace("/photo/", "/video/")
            opts = {
                'format': 'bestvideo+bestaudio/best' if format_id == 'best' else format_id,
                'quiet': True,
                'noplaylist': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(yt_target_url, download=False)
                stream_url = info.get('url')

                if not stream_url and info.get('formats'):
                    for f in reversed(info['formats']):
                        if f.get('url'):
                            stream_url = f.get('url')
                            break

                if not stream_url:
                    raise HTTPException(status_code=404, detail="Direct stream URL not found")

                title = safe_filename(info.get("title", "video"))
                download_name = f"{title}.mp4"

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    'Referer': target_url
                }
                if info.get('http_headers'):
                    headers.update(info.get('http_headers'))

                req = requests.get(stream_url, headers=headers, stream=True, timeout=90)

        def iter_chunks():
            for chunk in req.iter_content(chunk_size=1024 * 64):
                if chunk:
                    yield chunk

        resp_headers = {
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "Content-Type": req.headers.get('Content-Type', 'video/mp4')
        }
        if 'Content-Length' in req.headers:
            resp_headers['Content-Length'] = req.headers['Content-Length']

        return StreamingResponse(iter_chunks(), headers=resp_headers, media_type=resp_headers["Content-Type"])

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("STREAM ERROR")
        raise HTTPException(status_code=500, detail=f"Streaming failed: {str(e)}")

# ============================================================
# ERROR HANDLERS
# ============================================================
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "detail": str(exc.detail), "status_code": exc.status_code}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.exception("UNHANDLED SERVER ERROR")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": "Internal server error", "status_code": 500}
    )

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
