from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp
import requests
import logging
from typing import Optional, List, Dict, Any
import asyncio

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
def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert any value to integer"""
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

def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert any value to float"""
    try:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return float(value) if value else default
        return default
    except (ValueError, TypeError):
        return default

def safe_str(value: Any, default: str = "Unknown") -> str:
    """Safely convert any value to string"""
    try:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return default
        return str(value).strip()
    except:
        return default

def get_best_format(formats: List[Dict]) -> Optional[Dict]:
    """Get best video format from list"""
    if not formats:
        return None
    
    # Prefer video with audio
    for f in sorted(formats, key=lambda x: safe_int(x.get('height'), 0), reverse=True):
        if f.get('url') and f.get('vcodec') != 'none':
            return f
    
    # If no video, try best video only
    for f in sorted(formats, key=lambda x: safe_int(x.get('height'), 0), reverse=True):
        if f.get('url'):
            return f
    
    return formats[0] if formats else None

# ============================================================================
# ENDPOINT 1: Health Check
# ============================================================================
@app.get("/")
def health_check():
    """Health check endpoint"""
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
    """
    Extract video information and available formats from URL
    Supports: YouTube, TikTok, Instagram, Facebook, Twitter, etc.
    """
    try:
        url = data.url.strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL cannot be empty")
        
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
            
            # Extract title safely
            title = safe_str(info.get('title'), 'Social Media Content')
            
            # Extract duration safely
            duration = safe_int(info.get('duration'), 0)
            
            # Extract thumbnail
            thumbnail = safe_str(info.get('thumbnail'), '')
            
            # Extract platform
            platform = safe_str(
                info.get('extractor_key') or info.get('extractor'),
                'Unknown'
            )
            
            # Process formats
            formats_list = []
            
            if 'formats' in info and info['formats']:
                for f in info['formats']:
                    try:
                        # Skip formats without URL
                        if not f.get('url'):
                            continue
                        
                        # Safe type conversions
                        format_id = safe_str(f.get('format_id'), 'default')
                        ext = safe_str(f.get('ext'), 'mp4').lower()
                        height = safe_int(f.get('height'), 0)
                        width = safe_int(f.get('width'), 0)
                        filesize = safe_int(f.get('filesize'), 0)
                        bitrate = safe_int(f.get('abr') or f.get('vbr'), 0)
                        fps = safe_int(f.get('fps'), 0)
                        
                        # Determine if audio
                        vcodec = safe_str(f.get('vcodec'), 'unknown')
                        acodec = safe_str(f.get('acodec'), 'unknown')
                        is_audio = vcodec == 'none' or ext in ['mp3', 'm4a', 'aac', 'opus', 'wav']
                        
                        # Format resolution label
                        if is_audio:
                            resolution_label = f"{bitrate}kbps MP3" if bitrate else "Audio"
                        elif height > 0:
                            resolution_label = f"{height}p"
                        elif width > 0:
                            resolution_label = f"{width}x{height or '?'}"
                        else:
                            resolution_label = "HD"
                        
                        # Calculate filesize in MB
                        filesize_mb = round(filesize / (1024 * 1024), 2) if filesize > 0 else None
                        
                        format_dict = {
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
                        }
                        
                        formats_list.append(format_dict)
                    except Exception as e:
                        logger.warning(f"⚠️ Error processing format: {e}")
                        continue
            
            # Fallback: if no formats found, use best option
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
                raise HTTPException(status_code=400, detail="No formats available")
            
            logger.info(f"✅ Found {len(formats_list)} formats for: {title}")
            
            return {
                "title": title,
                "thumbnail": thumbnail,
                "duration": duration,
                "platform": platform,
                "formats": formats_list
            }
    
    except HTTPException as e:
        logger.error(f"❌ HTTP Error: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"❌ Extraction Error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

# ============================================================================
# ENDPOINT 3: Direct Download/Stream
# ============================================================================
@app.get("/api/download")
@app.get("/api/v1/download")
def download_media(url: str = Query(...), format_id: str = Query("best")):
    """
    Download or stream media directly
    Returns file as streaming response
    """
    try:
        url = url.strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL required")
        
        logger.info(f"📥 Downloading: {url} | Format: {format_id}")
        
        ydl_opts = {
            'format': format_id if format_id != 'best' else 'best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'socket_timeout': 30,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Get download URL
            download_url = info.get('url')
            
            if not download_url and 'formats' in info:
                best = get_best_format(info['formats'])
                if best:
                    download_url = best.get('url')
            
            if not download_url:
                raise HTTPException(status_code=404, detail="No downloadable URL found")
            
            # Get title for filename
            title = safe_str(info.get('title'), 'media')
            ext = safe_str(info.get('ext'), 'mp4')
            filename = f"{title[:50]}.{ext}".replace('/', '_')
            
            # Make request with headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Referer': url,
                'Range': 'bytes=0-'
            }
            
            req = requests.get(
                download_url,
                headers=headers,
                stream=True,
                timeout=60,
                allow_redirects=True
            )
            
            if req.status_code >= 400:
                logger.error(f"❌ Download failed: {req.status_code}")
                raise HTTPException(
                    status_code=req.status_code,
                    detail=f"Server error: {req.status_code}"
                )
            
            def iter_chunks():
                try:
                    for chunk in req.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            yield chunk
                except Exception as e:
                    logger.error(f"❌ Stream error: {e}")
                    raise
            
            content_type = req.headers.get('Content-Type', 'application/octet-stream')
            content_length = req.headers.get('Content-Length')
            
            response_headers = {
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': content_type,
                'Accept-Ranges': 'bytes',
            }
            
            if content_length:
                response_headers['Content-Length'] = content_length
            
            logger.info(f"✅ Streaming: {filename}")
            
            return StreamingResponse(
                iter_chunks(),
                headers=response_headers,
                media_type=content_type
            )
    
    except HTTPException as e:
        logger.error(f"❌ HTTP Error: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"❌ Download Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

# ============================================================================
# ENDPOINT 4: Stream with Format Selection
# ============================================================================
@app.get("/api/stream")
@app.get("/api/v1/stream")
def stream_media(url: str = Query(...), format_id: str = Query("best")):
    """Stream media with format selection"""
    return download_media(url=url, format_id=format_id)

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
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
