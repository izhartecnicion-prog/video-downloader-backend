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
import glob

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
    title="Universal Social Video Downloader API",
    version="3.0.0",
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

    # Remove surrounding quotes
    url = url.strip("'").strip('"').strip()

    # Protocol-relative URL
    if url.startswith("//"):
        url = "https:" + url

    # Missing protocol
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Remove accidental trailing punctuation
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

    name = re.sub(
        r'[\\/*?:"<>|]',
        "_",
        name
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    )

    name = name.strip(" .")

    if not name:
        name = "video"

    return name[:150]


def format_size(size: int) -> Optional[float]:

    if size <= 0:
        return None

    return round(
        size / 1048576,
        2
    )


def is_audio_format(f: Dict[str, Any]) -> bool:

    ext = safe_str(
        f.get("ext"),
        ""
    ).lower()

    vcodec = safe_str(
        f.get("vcodec"),
        "none"
    ).lower()

    return (
        vcodec == "none"
        or ext in AUDIO_EXTENSIONS
    )


def is_video_format(f: Dict[str, Any]) -> bool:

    ext = safe_str(
        f.get("ext"),
        ""
    ).lower()

    vcodec = safe_str(
        f.get("vcodec"),
        "none"
    ).lower()

    return (
        vcodec != "none"
        and ext not in AUDIO_EXTENSIONS
    )


def get_ffmpeg_path() -> Optional[str]:

    # First check PATH
    path = shutil.which("ffmpeg")

    if path:
        return path

    # Common locations
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


def get_extract_options() -> Dict[str, Any]:

    opts = get_base_options()

    opts.update({

        "skip_download": True,

    })

    return opts


# ============================================================
# SMART FORMAT SELECTION
# ============================================================

def get_format_selector(
    format_id: str
) -> str:

    requested = safe_str(
        format_id,
        "best"
    )

    # BEST
    if requested.lower() in {
        "",
        "best",
        "auto",
        "default"
    }:

        # Prefer MP4 video + M4A audio.
        #
        # bv* = best video
        # ba  = best audio
        #
        # /b = fallback to combined stream
        #
        return (
            "bv*[ext=mp4]+ba[ext=m4a]/"
            "bv*+ba/"
            "b[ext=mp4]/"
            "b"
        )

    # Audio requested
    if requested.lower() in {
        "audio",
        "bestaudio"
    }:

        return (
            "ba[ext=m4a]/"
            "ba/"
            "bestaudio"
        )

    # Specific video format.
    #
    # Important:
    # Try exact video + audio first.
    # Then exact format alone.
    # Then generic best fallback.
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
# HEALTH
# ============================================================

@app.get("/")
def root():

    ffmpeg = get_ffmpeg_path()

    return {

        "status": "online",

        "service":
            "Universal Social Video Downloader API",

        "version":
            "3.0.0",

        "yt_dlp":
            yt_dlp.version.__version__,

        "ffmpeg":
            bool(ffmpeg),

        "ffmpeg_path":
            ffmpeg,

        "endpoints": [

            "POST /api/extract",
            "POST /api/v1/extract",

            "GET /api/download",
            "GET /api/v1/download",

            "GET /api/stream",
            "GET /api/v1/stream",

            "GET /api/health"

        ]

    }


@app.get("/api/health")
def health():

    ffmpeg = get_ffmpeg_path()

    return {

        "status": "ok",

        "service":
            "video-downloader-backend",

        "version":
            "3.0.0",

        "yt_dlp":
            yt_dlp.version.__version__,

        "ffmpeg":
            bool(ffmpeg)

    }


# ============================================================
# EXTRACT
# ============================================================

@app.post("/api/extract")
@app.post("/api/v1/extract")
def extract_video_info(
    data: VideoRequest
):

    try:

        original_url = clean_url(
            data.url
        )

        logger.info(
            f"EXTRACT REQUEST: {original_url}"
        )

        opts = get_extract_options()

        with yt_dlp.YoutubeDL(opts) as ydl:

            info = ydl.extract_info(
                original_url,
                download=False
            )

        if not info:

            raise HTTPException(
                status_code=400,
                detail=
                "Unable to extract video information."
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

        uploader = safe_str(
            info.get("uploader")
            or info.get("creator")
            or info.get("channel"),
            ""
        )

        formats_list = []

        formats = info.get(
            "formats"
        ) or []

        # ====================================================
        # FORMATS
        # ====================================================

        for f in formats:

            try:

                direct_url = f.get("url")

                # We deliberately do not expose CDN URLs
                # to Flutter.
                _ = direct_url

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
                ).lower()

                vcodec = safe_str(
                    f.get("vcodec"),
                    "none"
                )

                acodec = safe_str(
                    f.get("acodec"),
                    "none"
                )

                filesize = safe_int(
                    f.get("filesize")
                    or f.get("filesize_approx"),
                    0
                )

                fps = safe_int(
                    f.get("fps"),
                    0
                )

                abr = safe_float(
                    f.get("abr"),
                    0
                )

                vbr = safe_float(
                    f.get("vbr"),
                    0
                )

                bitrate = abr or vbr

                audio = is_audio_format(f)

                video = is_video_format(f)

                # Skip completely unusable formats
                if not audio and not video:
                    continue

                # =================================================
                # LABEL
                # =================================================

                if audio:

                    if bitrate > 0:

                        resolution = (
                            f"{int(bitrate)} kbps"
                        )

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

                    resolution = (
                        f"{width}x{height}"
                    )

                else:

                    resolution = "Auto"

                formats_list.append({

                    "id":
                        format_id,

                    "format_id":
                        format_id,

                    "resolution":
                        resolution,

                    "quality_label":
                        resolution,

                    "height":
                        height,

                    "width":
                        width,

                    "ext":
                        ext,

                    "extension":
                        ext,

                    "filesize":
                        filesize,

                    "filesize_bytes":
                        filesize,

                    "filesize_mb":
                        format_size(filesize),

                    # NEVER expose CDN URL
                    "url":
                        None,

                    "direct_url":
                        None,

                    "is_audio":
                        audio,

                    "is_video":
                        video,

                    "vcodec":
                        vcodec,

                    "acodec":
                        acodec,

                    "bitrate":
                        int(bitrate),

                    "fps":
                        fps

                })

            except Exception as format_error:

                logger.warning(
                    f"FORMAT SKIPPED: {format_error}"
                )

                continue

        # ====================================================
        # FALLBACK
        # ====================================================

        if not formats_list:

            formats_list.append({

                "id":
                    "best",

                "format_id":
                    "best",

                "resolution":
                    "Best Quality",

                "quality_label":
                    "Best Quality",

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

                "ext":
                    "mp4",

                "extension":
                    "mp4",

                "filesize":
                    0,

                "filesize_bytes":
                    0,

                "filesize_mb":
                    None,

                "url":
                    None,

                "direct_url":
                    None,

                "is_audio":
                    False,

                "is_video":
                    True,

                "vcodec":
                    "unknown",

                "acodec":
                    "unknown",

                "bitrate":
                    0,

                "fps":
                    0

            })

        # ====================================================
        # REMOVE DUPLICATE FORMAT IDs
        # ====================================================

        unique_formats = {}

        for item in formats_list:

            key = (
                item["format_id"],
                item["is_audio"]
            )

            if key not in unique_formats:

                unique_formats[key] = item

        formats_list = list(
            unique_formats.values()
        )

        # ====================================================
        # SORT
        # ====================================================

        formats_list.sort(
            key=lambda x: (
                0 if not x.get("is_audio") else 1,
                -safe_int(
                    x.get("height"),
                    0
                ),
                -safe_int(
                    x.get("bitrate"),
                    0
                )
            )
        )

        logger.info(
            f"EXTRACTION SUCCESS | "
            f"{title} | "
            f"{platform} | "
            f"{len(formats_list)} formats"
        )

        return {

            "title":
                title,

            "thumbnail":
                thumbnail,

            "duration":
                duration,

            "duration_seconds":
                duration,

            "duration_formatted":
                (
                    f"{duration // 60:02d}:"
                    f"{duration % 60:02d}"
                    if duration > 0
                    else "00:00"
                ),

            "platform":
                platform,

            "author":
                uploader,

            "uploader":
                uploader,

            "original_url":
                original_url,

            "url":
                original_url,

            "formats":
                formats_list,

            "video_formats":
                [
                    x for x in formats_list
                    if x.get("is_video")
                ],

            "audio_formats":
                [
                    x for x in formats_list
                    if x.get("is_audio")
                ]

        }

    except HTTPException:

        raise

    except Exception as e:

        logger.exception(
            "EXTRACTION ERROR"
        )

        message = str(e)

        # Make common errors easier for Flutter
        if "login" in message.lower():

            message = (
                "This media requires login "
                "or is not publicly accessible."
            )

        elif (
            "sign in" in message.lower()
            or "bot" in message.lower()
        ):

            message = (
                "Platform protection blocked "
                "this request. Please try another URL."
            )

        raise HTTPException(
            status_code=400,
            detail=f"Extraction failed: {message}"
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

        original_url = clean_url(
            url
        )

        requested_format = safe_str(
            format_id,
            "best"
        )

        logger.info(
            f"DOWNLOAD REQUEST | "
            f"JOB={job_id}"
        )

        logger.info(
            f"URL={original_url}"
        )

        logger.info(
            f"FORMAT={requested_format}"
        )

        # ====================================================
        # FFMPEG CHECK
        # ====================================================

        ffmpeg_path = get_ffmpeg_path()

        logger.info(
            f"FFMPEG: {ffmpeg_path}"
        )

        # ====================================================
        # FORMAT SELECTOR
        # ====================================================

        format_selector = get_format_selector(
            requested_format
        )

        logger.info(
            f"FORMAT SELECTOR: "
            f"{format_selector}"
        )

        # ====================================================
        # OUTPUT
        # ====================================================

        output_template = os.path.join(
            temp_dir,
            f"{job_id}.%(ext)s"
        )

        opts = get_base_options()

        opts.update({

            "format":
                format_selector,

            "outtmpl":
                output_template,

            "noplaylist":
                True,

            "merge_output_format":
                "mp4",

            "retries":
                10,

            "fragment_retries":
                10,

            "extractor_retries":
                5,

            "file_access_retries":
                5,

        })

        # ====================================================
        # FFMPEG
        # ====================================================

        if ffmpeg_path:

            opts["ffmpeg_location"] = (
                os.path.dirname(ffmpeg_path)
            )

        else:

            # If no ffmpeg, try single-file formats.
            #
            # But for best quality videos where video
            # and audio are separate, merging is impossible.
            #
            # We don't immediately fail because some
            # platforms provide combined streams.
            logger.warning(
                "FFmpeg NOT FOUND. "
                "Only single-file streams may work."
            )

            # Force combined stream as fallback
            if requested_format.lower() in {
                "best",
                "auto",
                "default",
                ""
            }:

                opts["format"] = (
                    "b[ext=mp4]/"
                    "b/"
                    "best"
                )

        # ====================================================
        # DOWNLOAD
        # ====================================================

        logger.info(
            f"YT-DLP START | JOB={job_id}"
        )

        with yt_dlp.YoutubeDL(opts) as ydl:

            info = ydl.extract_info(
                original_url,
                download=True
            )

        if not info:

            raise HTTPException(
                status_code=404,
                detail=
                "Could not extract media from this URL."
            )

        # ====================================================
        # FIND DOWNLOADED FILES
        # ====================================================

        possible_files = []

        for root, dirs, files in os.walk(
            temp_dir
        ):

            for filename in files:

                full_path = os.path.join(
                    root,
                    filename
                )

                if os.path.isfile(
                    full_path
                ):

                    possible_files.append(
                        full_path
                    )

        # Remove tiny/invalid files
        possible_files = [
            f for f in possible_files
            if os.path.getsize(f) > 1024
        ]

        if not possible_files:

            raise HTTPException(
                status_code=500,
                detail=
                "yt-dlp completed but no media file was created."
            )

        # ====================================================
        # SELECT FINAL FILE
        # ====================================================

        mp4_files = [
            f for f in possible_files
            if f.lower().endswith(".mp4")
        ]

        if mp4_files:

            # Largest MP4
            final_file = max(
                mp4_files,
                key=os.path.getsize
            )

        else:

            # Largest available media
            final_file = max(
                possible_files,
                key=os.path.getsize
            )

        # ====================================================
        # FILE CHECK
        # ====================================================

        if not os.path.exists(
            final_file
        ):

            raise HTTPException(
                status_code=500,
                detail=
                "Downloaded media file not found."
            )

        file_size = os.path.getsize(
            final_file
        )

        if file_size < 1024:

            raise HTTPException(
                status_code=500,
                detail=
                "Downloaded file is empty or invalid."
            )

        # ====================================================
        # EXTENSION
        # ====================================================

        actual_ext = (
            os.path.splitext(
                final_file
            )[1]
            .lower()
            .replace(".", "")
        )

        if not actual_ext:

            actual_ext = "mp4"

        # ====================================================
        # TITLE
        # ====================================================

        title = safe_filename(
            info.get(
                "title",
                "video"
            )
        )

        # Avoid duplicate extension
        title = re.sub(
            r"\.(mp4|webm|mkv|mov|avi|m4a|mp3)$",
            "",
            title,
            flags=re.IGNORECASE
        )

        # ====================================================
        # MIME
        # ====================================================

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

        download_name = (
            f"{title}.{actual_ext}"
        )

        logger.info(
            f"DOWNLOAD COMPLETE | "
            f"JOB={job_id} | "
            f"SIZE={file_size} | "
            f"FILE={final_file}"
        )

        # ====================================================
        # RESPONSE
        # ====================================================

        return FileResponse(

            path=final_file,

            media_type=media_type,

            filename=download_name

        )

    except HTTPException:

        # Cleanup
        try:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )
        except Exception:
            pass

        raise

    except Exception as e:

        logger.exception(
            f"DOWNLOAD ERROR | JOB={job_id}"
        )

        message = str(e)

        # ====================================================
        # FRIENDLY ERRORS
        # ====================================================

        if (
            "ffmpeg" in message.lower()
            or "merging" in message.lower()
        ):

            message = (
                "This video requires FFmpeg "
                "to merge video and audio streams. "
                "Please install FFmpeg on the server."
            )

        elif (
            "requested format" in message.lower()
            or "format is not available" in message.lower()
        ):

            message = (
                "The selected quality is not available "
                "for this video. Please select another quality."
            )

        elif (
            "sign in" in message.lower()
            or "login" in message.lower()
        ):

            message = (
                "This video requires login "
                "or is not publicly accessible."
            )

        elif (
            "bot" in message.lower()
            or "confirm" in message.lower()
        ):

            message = (
                "The platform blocked this request. "
                "Please try another video."
            )

        raise HTTPException(
            status_code=500,
            detail=
            f"Video download failed: {message}"
        )

    # NOTE:
    # FileResponse finishes reading the file after returning.
    # Therefore temp directory should not be deleted here.


# ============================================================
# ERROR HANDLER
# ============================================================

@app.exception_handler(
    HTTPException
)
async def http_exception_handler(
    request,
    exc
):

    return JSONResponse(

        status_code=exc.status_code,

        content={

            "status":
                "error",

            "detail":
                str(exc.detail),

            "status_code":
                exc.status_code

        }

    )


@app.exception_handler(
    Exception
)
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

            "status":
                "error",

            "detail":
                "Internal server error",

            "status_code":
                500

        }

    )


# ============================================================
# SERVER
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

        port=port,

        log_level="info"

    )
