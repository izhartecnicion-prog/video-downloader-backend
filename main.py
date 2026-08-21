from fastapi import FastAPI, HTTPException, Query
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
    version="3.0.0",
    description="Universal social media video/audio downloader with TikTok support"
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
    """Clean and normalize URL"""
    
    if not raw_url:
        raise ValueError("URL is empty")

    url = raw_url.strip()
    url = url.strip("'").strip('"')

    if url.startswith("//"):
        url = "https:" + url

    elif not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    return url


def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert value to integer"""
    
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
    """Safely convert value to string"""
    
    try:

        if value is None:
            return default

        return str(value).strip()

    except Exception:
        return default


def safe_filename(name: str) -> str:
    """Sanitize filename for OS compatibility"""
    
    name = safe_str(name, "video")

    name = re.sub(
        r'[\\/*?:"<>|]',
        "_",
        name
    )

    name = name.strip()

    if not name:
        name = "video"

    return name[:150]


def cleanup_temp_dir(temp_dir: str, delay: float = 30):
    """Cleanup temp directory after delay"""
    
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
    """Get yt-dlp options for extraction"""
    
    return {

        "quiet": True,

        "no_warnings": True,

        "noplaylist": True,

        "socket_timeout": 60,

        "retries": 5,

        "fragment_retries": 5,

        "file_access_retries": 5,

        "concurrent_fragment_downloads": 4,

        "http_chunk_size": 10485760,

        "user_agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36",

        "http_headers": {

            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36",

            "Accept":
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8",

            "Accept-Language":
                "en-US,en;q=0.9",

        },

    }


def get_download_options(temp_dir: str, job_id: str):
    """Get yt-dlp options for downloading"""
    
    return {

        "quiet": False,

        "no_warnings": True,

        "noplaylist": True,

        "socket_timeout": 60,

        "retries": 5,

        "fragment_retries": 5,

        "concurrent_fragment_downloads": 4,

        "http_chunk_size": 10485760,

        "outtmpl": os.path.join(
            temp_dir,
            f"{job_id}.%(ext)s"
        ),

        "merge_output_format": "mp4",

        "overwrites": True,

        "continuedl": False,

        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "prefixes": ["best"],
                "format": "mp4",
            }
        ],

        "user_agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36",

        "http_headers": {

            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36",

        },

    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
def health_check():
    """Root health check"""
    
    return {

        "status": "online",

        "message":
            "Universal Social Video Downloader API is active",

        "version": "3.0.0"

    }


# ============================================================
# HEALTH / PING
# ============================================================

@app.get("/api/health")
def health():
    """Health status with version info"""
    
    return {

        "status": "ok",

        "service": "video-downloader-backend",

        "version": "3.0.0",

        "yt_dlp": yt_dlp.version.__version__

    }


# ============================================================
# EXTRACT - V3.0 WITH TIKTOK SUPPORT
# ============================================================

@app.post("/api/extract")
@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):
    """Extract video information and available formats"""
    
    try:

        original_url = clean_url(data.url)

        logger.info(
            f"🔍 EXTRACT REQUEST: {original_url}"
        )

        # Detect platform
        is_tiktok = (
            "tiktok.com" in original_url or
            "vm.tiktok" in original_url or
            "vt.tiktok" in original_url
        )

        opts = get_extract_options()
        opts["skip_download"] = True

        # TikTok specific options
        if is_tiktok:
            opts["socket_timeout"] = 90
            opts["retries"] = 10

        with yt_dlp.YoutubeDL(opts) as ydl:

            info = ydl.extract_info(
                original_url,
                download=False
            )

        if not info:

            raise HTTPException(
                status_code=400,
                detail="Unable to extract video information"
            )

        title = safe_str(
            info.get("title"),
            "Social Media Video"
        )

        duration = safe_int(
            info.get("duration"),
            0
        )

        thumbnail = safe_str(
            info.get("thumbnail"),
            ""
        )

        platform = safe_str(
            info.get("extractor_key")
            or info.get("extractor"),
            "Unknown"
        )

        formats_list = []

        formats = info.get("formats") or []

        for f in formats:

            try:

                format_id = safe_str(
                    f.get("format_id"),
                    "best"
                )

                height = safe_int(
                    f.get("height"),
                    0
                )

                width = safe_int(
                    f.get("width"),
                    0
                )

                ext = safe_str(
                    f.get("ext"),
                    "mp4"
                )

                vcodec = safe_str(
                    f.get("vcodec"),
                    "none"
                )

                acodec = safe_str(
                    f.get("acodec"),
                    "none"
                )

                filesize = safe_int(
                    f.get("filesize"),
                    0
                )

                fps = safe_int(
                    f.get("fps"),
                    0
                )

                abr = safe_int(
                    f.get("abr"),
                    0
                )

                vbr = safe_int(
                    f.get("vbr"),
                    0
                )

                bitrate = abr or vbr

                is_audio = (
                    vcodec == "none"
                    or ext.lower()
                    in [
                        "mp3",
                        "m4a",
                        "aac",
                        "opus",
                        "wav"
                    ]
                )

                if is_audio:

                    resolution = (
                        f"{bitrate}kbps"
                        if bitrate
                        else "Audio"
                    )

                elif height:

                    resolution = f"{height}p"

                elif width:

                    resolution = (
                        f"{width}x{height}"
                    )

                else:

                    resolution = "Auto"

                formats_list.append({

                    "id": format_id,

                    "format_id": format_id,

                    "resolution": resolution,

                    "height": height,

                    "width": width,

                    "ext": ext,

                    "filesize": filesize,

                    "filesize_mb":
                        round(
                            filesize / 1048576,
                            2
                        )
                        if filesize
                        else None,

                    # V3.0: NO CDN URL
                    # Backend generates fresh one each download
                    "url": None,

                    "is_audio": is_audio,

                    "vcodec": vcodec,

                    "acodec": acodec,

                    "bitrate": bitrate,

                    "fps": fps

                })

            except Exception:

                continue

        # ====================================================
        # FALLBACK IF NO FORMATS
        # ====================================================

        if not formats_list:

            formats_list.append({

                "id": "best",

                "format_id": "best",

                "resolution": "Best Quality",

                "height":
                    safe_int(
                        info.get("height"),
                        0
                    ),

                "width":
                    safe_int(
                        info.get("width"),
                        0
                    ),

                "ext": "mp4",

                "filesize": 0,

                "filesize_mb": None,

                "url": None,

                "is_audio": False,

                "vcodec": "unknown",

                "acodec": "unknown",

                "bitrate": 0,

                "fps": 0

            })

        logger.info(
            f"✅ EXTRACTION SUCCESS: {title} "
            f"({len(formats_list)} formats) | "
            f"Platform: {platform}"
        )

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

        logger.exception(
            "EXTRACTION ERROR"
        )

        raise HTTPException(

            status_code=400,

            detail=f"Extraction failed: {str(e)}"

        )


# ============================================================
# DOWNLOAD - V3.0 WITH AUTO MP4 MERGING
# ============================================================

@app.get("/api/download")
@app.get("/api/v1/download")
@app.get("/api/stream")
@app.get("/api/v1/stream")
def download_video(

    url: str = Query(...),

    format_id: str = Query("best")

):
    """Download video/audio with automatic MP4 merging"""

    temp_dir = tempfile.mkdtemp(
        prefix="video_dl_"
    )

    job_id = uuid.uuid4().hex

    try:

        original_url = clean_url(url)

        logger.info(
            f"📥 DOWNLOAD REQUEST | JOB={job_id}"
        )

        logger.info(
            f"URL: {original_url}"
        )

        logger.info(
            f"FORMAT: {format_id}"
        )

        # ====================================================
        # FORMAT SELECTION
        # ====================================================

        if (
            not format_id
            or format_id.lower() == "best"
        ):

            # Best video + audio combination
            # yt-dlp automatically merges if needed
            format_selector = (
                "bv*+ba/b"
            )

        else:

            # User selected format
            # Add fallbacks for reliability
            format_selector = (
                f"{format_id}+ba/"
                f"{format_id}/"
                "bv*+ba/b"
            )

        logger.info(
            f"FORMAT SELECTOR: {format_selector}"
        )

        # ====================================================
        # YT-DLP DOWNLOAD + MERGE
        # ====================================================

        opts = get_download_options(
            temp_dir,
            job_id
        )

        opts["format"] = format_selector

        logger.info(
            f"🚀 Starting download | JOB={job_id}"
        )

        with yt_dlp.YoutubeDL(opts) as ydl:

            info = ydl.extract_info(

                original_url,

                download=True

            )

            downloaded_file = (
                ydl.prepare_filename(info)
            )

        # ====================================================
        # FIND FINAL MP4
        # ====================================================

        possible_files = []

        for filename in os.listdir(temp_dir):

            full_path = os.path.join(
                temp_dir,
                filename
            )

            if os.path.isfile(full_path):

                possible_files.append(
                    full_path
                )

        if not possible_files:

            raise HTTPException(

                status_code=500,

                detail=
                "Download completed but file was not created"

            )

        # Prefer MP4
        mp4_files = [

            f for f in possible_files

            if f.lower().endswith(".mp4")

        ]

        if mp4_files:

            final_file = mp4_files[0]

        else:

            final_file = possible_files[0]

        if not os.path.exists(final_file):

            raise HTTPException(

                status_code=500,

                detail="Downloaded file not found"

            )

        file_size = os.path.getsize(
            final_file
        )

        file_size_mb = round(
            file_size / (1024 * 1024),
            2
        )

        logger.info(
            f"✅ DOWNLOAD COMPLETE | "
            f"JOB={job_id} | "
            f"SIZE: {file_size_mb}MB"
        )

        title = safe_filename(
            info.get(
                "title",
                "video"
            )
        )

        download_name = (
            f"{title}.mp4"
        )

        # Schedule cleanup after 60 seconds
        cleanup_temp_dir(temp_dir, delay=60)

        return FileResponse(

            path=final_file,

            media_type="video/mp4",

            filename=download_name,

            background=None

        )

    except HTTPException:

        raise

    except Exception as e:

        logger.exception(
            f"DOWNLOAD ERROR | JOB={job_id}"
        )

        # Cleanup on error immediately
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
        except:
            pass

        raise HTTPException(

            status_code=500,

            detail=
            f"Video download failed: {str(e)}"

        )


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(
    request,
    exc
):
    """Handle HTTP exceptions"""

    return JSONResponse(

        status_code=exc.status_code,

        content={

            "status": "error",

            "detail": str(exc.detail),

            "status_code":
                exc.status_code

        }

    )


@app.exception_handler(Exception)
async def general_exception_handler(
    request,
    exc
):
    """Handle unhandled exceptions"""

    logger.exception(
        "UNHANDLED SERVER ERROR"
    )

    return JSONResponse(

        status_code=500,

        content={

            "status": "error",

            "detail":
                "Internal server error",

            "status_code": 500

        }

    )


# ============================================================
# LOCAL SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            8000
        )
    )

    uvicorn.run(

        app,

        host="0.0.0.0",

        port=port

    )
