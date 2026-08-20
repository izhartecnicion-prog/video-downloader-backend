from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp
import requests
import logging
from typing import Optional, List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 1. FastAPI App Initialization
app = FastAPI(
    title="Universal Social Video Downloader API",
    version="1.0.0",
    description="Extract and stream videos from all social platforms"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str

class ExtractResponse(BaseModel):
    title: str
    thumbnail: Optional[str]
    duration: int
    platform: str
    formats: List[Dict[str, Any]]

# Helper Functions
def clean_url(raw_url: str) -> str:
    """Sanitize incoming URL strings from mobile app"""
    url = raw_url.strip().strip("'").strip('"')
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
    except (ValueError, TypeError):
        return default

def safe_str(value: Any, default: str = "Unknown") -> str:
    try:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return default
        return str(value).strip()
    except Exception:
        return default

def get_best_format(formats: List[Dict]) -> Optional[Dict]:
    if not formats:
        return None
    for f in sorted(formats, key=lambda x: safe_int(x.get('height'), 0), reverse=True):
        if f.get('url') and f.get('vcodec') != 'none':
            return f
    for f in sorted(formats, key=lambda x: safe_int(x.get('height'), 0), reverse=True):
        if f.get('url'):
            return f
    return formats[0] if formats else None

# ============================================================================
# ENDPOINT 1: Health Check
# ============================================================================
@app.get("/")
def health_check():
    return {
        "status": "online",
        "message": "Universal Social Video Downloader API is active",
        "version": "1.0.0"
    }

# ============================================================================
# ENDPOINT 2: Extract Video Metadata & Formats
# ============================================================================
@app.post("/api/extract", response_model=ExtractResponse)
@app.post("/api/v1/extract", response_model=ExtractResponse)
def extract_video_info(data: VideoRequest):
    try:
        url = clean_url(data.url)
        logger.info(f"🔍 Extracting: {url}")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'socket_timeout': 30,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            title = safe_str(info.get('title'), 'Social Media Content')
            duration = safe_int(info.get('duration'), 0)
            thumbnail = safe_str(info.get('thumbnail'), '')
            platform = safe_str(info.get('extractor_key') or info.get('extractor'), 'Unknown')
            
            formats_list = []
            if 'formats' in info and info['formats']:
                for f in info['formats']:
                    try:
                        if not f.get('url'):
                            continue
                        
                        format_id = safe_str(f.get('format_id'), 'default')
                        ext = safe_str(f.get('ext'), 'mp4').lower()
                        height = safe_int(f.get('height'), 0)
                        width = safe_int(f.get('width'), 0)
                        filesize = safe_int(f.get('filesize'), 0)
                        bitrate = safe_int(f.get('abr') or f.get('vbr'), 0)
                        fps = safe_int(f.get('fps'), 0)
                        
                        vcodec = safe_str(f.get('vcodec'), 'unknown')
                        acodec = safe_str(f.get('acodec'), 'unknown')
                        is_audio = vcodec == 'none' or ext in ['mp3', 'm4a', 'aac', 'opus', 'wav']
                        
                        if is_audio:
                            resolution_label = f"{bitrate}kbps MP3" if bitrate else "Audio MP3"
                        elif height > 0:
                            resolution_label = f"{height}p"
                        elif width > 0:
                            resolution_label = f"{width}x{height or '?'}"
                        else:
                            resolution_label = "HD"
                        
                        filesize_mb = round(filesize / (1024 * 1024), 2) if filesize > 0 else None
                        
                        formats_list.append({
                            "id": format_id,
                            "format_id": format_id,
                            "resolution": resolution_label,
                            "height": height,
                            "width": width,
                            "ext": ext,
                            "filesize": filesize,
                            "filesize_mb": filesize_mb,
                            "url": f.get('url'),
                            "is_audio": is_audio,
                            "vcodec": vcodec,
                            "acodec": acodec,
                            "bitrate": bitrate,
                            "fps": fps
                        })
                    except Exception:
                        continue
            
            if not formats_list and info.get('url'):
                formats_list.append({
                    "id": "best",
                    "format_id": "best",
                    "resolution": f"{safe_int(info.get('height'), 720)}p HD",
                    "height": safe_int(info.get('height'), 720),
                    "width": safe_int(info.get('width'), 1280),
                    "ext": safe_str(info.get('ext'), 'mp4'),
                    "filesize": safe_int(info.get('filesize'), 0),
                    "filesize_mb": None,
                    "url": info.get('url'),
                    "is_audio": False,
                    "vcodec": "unknown",
                    "acodec": "unknown",
                    "bitrate": 0,
                    "fps": 0
                })
            
            if not formats_list:
                raise HTTPException(status_code=400, detail="No downloadable formats available")
            
            return {
                "title": title,
                "thumbnail": thumbnail,
                "duration": duration,
                "platform": platform,
                "formats": formats_list
            }
    
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Extraction Error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

# ============================================================================
# ENDPOINT 3: Direct Streaming Proxy
# ============================================================================
@app.get("/api/download")
@app.get("/api/v1/download")
@app.get("/api/stream")
@app.get("/api/v1/stream")
def stream_media(url: str = Query(...), format_id: str = Query("best")):
    try:
        clean_target_url = clean_url(url)
        logger.info(f"📥 Streaming request for: {clean_target_url} | Format: {format_id}")
        
        target_format = format_id if format_id != 'best' else 'best[ext=mp4]/best'
        ydl_opts = {
            'format': target_format,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'socket_timeout': 30,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_target_url, download=False)
            stream_link = info.get('url')
            
            if not stream_link and 'formats' in info:
                best = get_best_format(info['formats'])
                if best:
                    stream_link = best.get('url')
            
            if not stream_link:
                raise HTTPException(status_code=404, detail="Direct media stream URL not found")
            
            # Extract request headers required by TikTok/IG CDN
            req_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Referer': clean_target_url
            }
            if info.get('http_headers'):
                req_headers.update(info.get('http_headers'))
            
            req = requests.get(stream_link, headers=req_headers, stream=True, timeout=90)
            
            if req.status_code >= 400:
                raise HTTPException(status_code=req.status_code, detail=f"Source server error: {req.status_code}")
            
            def iter_chunks():
                for chunk in req.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        yield chunk
            
            resp_headers = {
                "Content-Disposition": 'attachment; filename="media.mp4"',
                "Content-Type": req.headers.get('Content-Type', 'video/mp4')
            }
            if 'Content-Length' in req.headers:
                resp_headers['Content-Length'] = req.headers['Content-Length']
            
            return StreamingResponse(iter_chunks(), headers=resp_headers, media_type="video/mp4")
            
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Stream Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Streaming failed: {str(e)}")

# ============================================================================
# Error Handlers
# ============================================================================
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "detail": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"❌ Unhandled error: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "detail": "Internal server error",
            "status_code": 500
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
