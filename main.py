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
    version="2.0.0",
    description="Universal social media video downloader"
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


def safe_str(value: Any, default: str = ""):

    try:

        if value is None:
            return default

        return str(value).strip()

    except Exception:
        return default


def safe_filename(name: str):

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


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
def health_check():

    return {

        "status": "online",

        "message":
            "Universal Social Video Downloader API is active",

        "version": "2.0.0"

    }


# ============================================================
# HEALTH / PING
# ============================================================

@app.get("/api/health")
def health():

    return {

        "status": "ok",

        "service": "video-downloader-backend",

        "yt_dlp": yt_dlp.version.__version__

    }


# ============================================================
# EXTRACT
# ============================================================

@app.post("/api/extract")
@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):

    try:

        original_url = clean_url(data.url)

        logger.info(
            f"EXTRACT REQUEST: {original_url}"
        )

        opts = get_extract_options()

        opts["skip_download"] = True

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

                    # IMPORTANT:
                    # DO NOT SEND TIKTOK CDN URL
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
        # FALLBACK
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
# DOWNLOAD
# ============================================================

@app.get("/api/download")
@app.get("/api/v1/download")
@app.get("/api/stream")
@app.get("/api/v1/stream")
def download_video(

    url: str = Query(...),

    format_id: str = Query("best")

):

    temp_dir = tempfile.mkdtemp(
        prefix="video_dl_"
    )

    job_id = uuid.uuid4().hex

    try:

        original_url = clean_url(url)

        logger.info(
            f"DOWNLOAD REQUEST | JOB={job_id}"
        )

        logger.info(
            f"URL={original_url}"
        )

        logger.info(
            f"FORMAT={format_id}"
        )

        # ====================================================
        # FORMAT SELECTION
        # ====================================================

        if (
            not format_id
            or format_id.lower() == "best"
        ):

            # Best video + audio.
            # If source provides combined stream,
            # yt-dlp will use it.
            format_selector = (
                "bv*+ba/b"
            )

        else:

            # User selected a format.
            # Add audio fallback.
            format_selector = (
                f"{format_id}+ba/"
                f"{format_id}/"
                "bv*+ba/b"
            )

        output_template = os.path.join(

            temp_dir,

            f"{job_id}.%(ext)s"

        )

        opts = get_extract_options()

        opts.update({

            "format":
                format_selector,

            "outtmpl":
                output_template,

            "merge_output_format":
                "mp4",

            "noplaylist":
                True,

            "overwrites":
                True,

            "continuedl":
                False,

            "retries":
                5,

            "fragment_retries":
                5,

        })

        # ====================================================
        # DOWNLOAD WITH YT-DLP
        # ====================================================

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

        logger.info(
            f"DOWNLOAD COMPLETE | "
            f"JOB={job_id} | "
            f"SIZE={file_size}"
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
