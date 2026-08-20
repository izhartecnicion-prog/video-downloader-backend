from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp
import requests
import logging
from typing import Optional, List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Universal Social Video Downloader API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str

def safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(float(value.strip())) if value.strip() else default
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

def safe_mb(size_bytes: Any) -> str:
    b = safe_int(size_bytes, 0)
    if b <= 0:
        return "N/A"
    mb = b / (1024 * 1024)
    return f"{mb:.2f} MB"

def sanitize_url(raw_url: str) -> str:
    url = raw_url.strip()
    while url.startswith(("'", '"')):
        url = url[1:].strip()
    while url.endswith(("'", '"')):
        url = url[:-1].strip()
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url

@app.get("/")
def health():
    return {
        "status": "online",
        "version": "2.0.0",
        "service": "Universal Social Video Downloader API",
        "endpoints": [
            "POST /api/extract",
            "POST /api/v1/extract",
            "GET /api/download",
            "GET /api/v1/download",
            "GET /api/stream",
            "GET /api/v1/stream",
        ],
    }

@app.get("/health")
@app.get("/api/health")
def health_check():
    return {"status": "healthy"}

# ============================================================================
# MAIN EXTRACT ENDPOINT - WITH TIKTOK & UNIVERSAL SUPPORT
# ============================================================================
@app.post("/api/extract")
@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):
    """Extract video info with TikTok and universal platform support"""
    try:
        url = sanitize_url(data.url)
        if not url:
            raise HTTPException(status_code=400, detail="URL cannot be empty")
        
        logger.info(f"🔍 Extracting: {url}")
        
        url_lower = url.lower()
        if 'tiktok.com' in url_lower or 'douyin' in url_lower or 'vm.tiktok' in url_lower or 'vt.tiktok' in url_lower:
            return _extract_tiktok_video(url)
        
        return _extract_default(url)
    
    except HTTPException as e:
        logger.error(f"❌ HTTP Error: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"❌ Error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

# ============================================================================
# TIKTOK-SPECIFIC EXTRACTOR
# ============================================================================
def _extract_tiktok_video(url: str) -> Dict:
    """Special handler for TikTok videos"""
    logger.info("🎵 TikTok video detected - using specialized extractor")
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.tiktok.com/',
        },
        'extractor_args': {
            'tiktok': {
                'api_hostname': 'api22-normal-c-alisg.tiktokv.com',
            }
        },
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise HTTPException(status_code=404, detail="Could not extract TikTok video information.")
            
            title = safe_str(info.get('title'), 'TikTok Video')
            duration = safe_int(info.get('duration'), 0)
            thumbnail = safe_str(info.get('thumbnail') or info.get('cover') or '', '')
            uploader = safe_str(info.get('uploader') or info.get('creator') or info.get('channel') or 'TikTok Creator')
            
            formats_list = []
            video_formats = []
            audio_formats = []
            
            if 'formats' in info and info['formats']:
                for f in info['formats']:
                    direct_url = f.get('url')
                    if not direct_url:
                        continue
                    
                    format_id = safe_str(f.get('format_id'), 'best')
                    ext = safe_str(f.get('ext'), 'mp4').lower()
                    height = safe_int(f.get('height'), 0)
                    width = safe_int(f.get('width'), 0)
                    vcodec = safe_str(f.get('vcodec'), 'unknown')
                    acodec = safe_str(f.get('acodec'), 'unknown')
                    filesize = safe_int(f.get('filesize') or f.get('filesize_approx'), 0)
                    tbr = safe_int(f.get('tbr') or f.get('abr'), 0)
                    
                    is_audio = vcodec == 'none' or ext in ['mp3', 'm4a', 'aac', 'wav', 'flac', 'opus']
                    
                    if is_audio:
                        resolution_label = f'{tbr} kbps Audio' if tbr > 0 else 'Audio (MP3)'
                    elif height > 0:
                        resolution_label = f'{height}p Full HD' if height >= 1080 else (f'{height}p HD' if height >= 720 else f'{height}p')
                    else:
                        resolution_label = 'HD Video'
                    
                    format_item = {
                        'id': format_id,
                        'format_id': format_id,
                        'resolution': resolution_label,
                        'quality_label': resolution_label,
                        'height': height,
                        'width': width,
                        'ext': ext,
                        'extension': ext,
                        'filesize_bytes': filesize,
                        'filesize': filesize,
                        'filesize_mb': safe_mb(filesize),
                        'filesize_formatted': safe_mb(filesize),
                        'url': direct_url,
                        'direct_url': direct_url,
                        'is_audio': is_audio,
                        'vcodec': vcodec,
                        'acodec': acodec,
                    }
                    
                    formats_list.append(format_item)
                    if is_audio:
                        audio_formats.append(format_item)
                    else:
                        video_formats.append(format_item)
            
            if not video_formats and info.get('url'):
                item = {
                    'id': 'best',
                    'format_id': 'best',
                    'resolution': '1080p HD (No Watermark)',
                    'quality_label': '1080p HD (No Watermark)',
                    'height': 1080,
                    'width': 0,
                    'ext': 'mp4',
                    'extension': 'mp4',
                    'filesize_bytes': 0,
                    'filesize': 0,
                    'filesize_mb': 'HD Video',
                    'filesize_formatted': 'HD Video',
                    'url': info.get('url'),
                    'direct_url': info.get('url'),
                    'is_audio': False,
                    'vcodec': 'h264',
                    'acodec': 'aac',
                }
                video_formats.append(item)
                formats_list.append(item)
            
            if not audio_formats:
                audio_formats.append({
                    'id': 'bestaudio',
                    'format_id': 'bestaudio',
                    'resolution': '320 kbps MP3',
                    'quality_label': '320 kbps MP3',
                    'height': 0,
                    'width': 0,
                    'ext': 'mp3',
                    'extension': 'mp3',
                    'filesize_bytes': 0,
                    'filesize': 0,
                    'filesize_mb': 'HQ Audio',
                    'filesize_formatted': 'HQ Audio',
                    'url': info.get('url'),
                    'direct_url': info.get('url'),
                    'is_audio': True,
                    'vcodec': 'none',
                    'acodec': 'mp3',
                })
            
            logger.info(f"✅ TikTok extraction success: {title} ({len(formats_list)} formats)")
            
            return {
                'title': title,
                'thumbnail': thumbnail,
                'duration': duration,
                'duration_seconds': duration,
                'duration_formatted': f"{duration // 60:02d}:{duration % 60:02d}" if duration > 0 else "00:30",
                'author': uploader,
                'uploader': uploader,
                'platform': 'TikTok',
                'original_url': url,
                'url': url,
                'formats': formats_list,
                'video_formats': video_formats,
                'audio_formats': audio_formats,
            }
    
    except Exception as e:
        logger.warning(f"⚠️ TikTok specialized failed, trying standard: {str(e)}")
        return _extract_default(url)

# ============================================================================
# DEFAULT YT-DLP EXTRACTOR
# ============================================================================
def _extract_default(url: str) -> Dict:
    """Universal extractor for Instagram, YouTube, Facebook, Twitter, etc."""
    logger.info(f"🌐 Using standard extractor: {url}")
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise HTTPException(status_code=404, detail="Could not extract video information.")
        
        title = safe_str(info.get('title'), 'Video')
        duration = safe_int(info.get('duration'), 0)
        thumbnail = safe_str(info.get('thumbnail') or info.get('cover') or '', '')
        platform = safe_str(info.get('extractor') or info.get('extractor_key'), 'Social Media')
        uploader = safe_str(info.get('uploader') or info.get('creator') or info.get('channel') or 'Creator')
        
        formats_list = []
        video_formats = []
        audio_formats = []
        
        if 'formats' in info and info['formats']:
            for f in info['formats']:
                direct_url = f.get('url')
                if not direct_url:
                    continue
                
                format_id = safe_str(f.get('format_id'), 'best')
                ext = safe_str(f.get('ext'), 'mp4').lower()
                height = safe_int(f.get('height'), 0)
                width = safe_int(f.get('width'), 0)
                filesize = safe_int(f.get('filesize') or f.get('filesize_approx'), 0)
                tbr = safe_int(f.get('tbr') or f.get('abr'), 0)
                vcodec = safe_str(f.get('vcodec'), 'unknown')
                acodec = safe_str(f.get('acodec'), 'unknown')
                
                is_audio = vcodec == 'none' or ext in ['mp3', 'm4a', 'aac', 'wav', 'flac', 'opus']
                
                if is_audio:
                    resolution_label = f'{tbr} kbps Audio' if tbr > 0 else 'Audio (MP3)'
                elif height > 0:
                    resolution_label = f'{height}p Full HD' if height >= 1080 else (f'{height}p HD' if height >= 720 else f'{height}p')
                else:
                    resolution_label = 'HD Video'
                
                format_item = {
                    'id': format_id,
                    'format_id': format_id,
                    'resolution': resolution_label,
                    'quality_label': resolution_label,
                    'height': height,
                    'width': width,
                    'ext': ext,
                    'extension': ext,
                    'filesize_bytes': filesize,
                    'filesize': filesize,
                    'filesize_mb': safe_mb(filesize),
                    'filesize_formatted': safe_mb(filesize),
                    'url': direct_url,
                    'direct_url': direct_url,
                    'is_audio': is_audio,
                    'vcodec': vcodec,
                    'acodec': acodec,
                }
                
                formats_list.append(format_item)
                if is_audio:
                    audio_formats.append(format_item)
                else:
                    video_formats.append(format_item)
        
        if not video_formats and info.get('url'):
            h = safe_int(info.get('height'), 720)
            item = {
                'id': 'best',
                'format_id': 'best',
                'resolution': f"{h}p HD",
                'quality_label': f"{h}p HD",
                'height': h,
                'width': 0,
                'ext': safe_str(info.get('ext'), 'mp4'),
                'extension': safe_str(info.get('ext'), 'mp4'),
                'filesize_bytes': 0,
                'filesize': 0,
                'filesize_mb': 'HD Video',
                'filesize_formatted': 'HD Video',
                'url': info.get('url'),
                'direct_url': info.get('url'),
                'is_audio': False,
                'vcodec': 'h264',
                'acodec': 'aac',
            }
            video_formats.append(item)
            formats_list.append(item)
        
        if not formats_list:
            raise HTTPException(status_code=400, detail="No formats available")
        
        logger.info(f"✅ Extraction success: {title} ({len(formats_list)} formats)")
        
        return {
            'title': title,
            'thumbnail': thumbnail,
            'duration': duration,
            'duration_seconds': duration,
            'duration_formatted': f"{duration // 60:02d}:{duration % 60:02d}" if duration > 0 else "00:30",
            'author': uploader,
            'uploader': uploader,
            'platform': platform,
            'original_url': url,
            'url': url,
            'formats': formats_list,
            'video_formats': video_formats,
            'audio_formats': audio_formats,
        }

# ============================================================================
# DOWNLOAD & STREAM ENDPOINTS (UNIVERSAL PROXY STREAMER)
# ============================================================================
@app.get("/api/download")
@app.get("/api/v1/download")
@app.get("/api/stream")
@app.get("/api/v1/stream")
def download_media(url: str = Query(...), format_id: str = Query("best")):
    """Download and stream media with auto format selection and spoofed headers"""
    
    try:
        clean_url = sanitize_url(url)
        if not clean_url:
            raise HTTPException(status_code=400, detail="URL required")
        
        logger.info(f"📥 Downloading/Streaming: {clean_url} | Format: {format_id}")
        
        url_lower = clean_url.lower()
        is_tiktok = 'tiktok.com' in url_lower or 'vm.tiktok' in url_lower or 'vt.tiktok' in url_lower or 'douyin' in url_lower
        
        if is_tiktok:
            if format_id == 'bestaudio' or format_id == 'audio':
                ydl_format = 'bestaudio/best'
            else:
                ydl_format = 'bestvideo+bestaudio/best'
        else:
            if format_id == 'bestaudio' or format_id == 'audio':
                ydl_format = 'bestaudio/best'
            elif format_id != 'best' and not format_id.endswith('p'):
                ydl_format = f"{format_id}/best[ext=mp4]/best"
            else:
                ydl_format = 'best[ext=mp4]/best/bestvideo+bestaudio'
        
        ydl_opts = {
            'format': ydl_format,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'socket_timeout': 30,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
        
        if is_tiktok:
            ydl_opts['extractor_args'] = {
                'tiktok': {
                    'api_hostname': 'api22-normal-c-alisg.tiktokv.com',
                }
            }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            if not info:
                raise HTTPException(status_code=404, detail="Could not extract media info")
            
            download_url = info.get('url')
            
            if not download_url and 'formats' in info:
                for f in reversed(info['formats']):
                    if f.get('url'):
                        download_url = f.get('url')
                        break
            
            if not download_url:
                raise HTTPException(status_code=404, detail="No downloadable URL found")
            
            title = safe_str(info.get('title'), 'media')[:50]
            ext = safe_str(info.get('ext'), 'mp4')
            clean_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-')).rstrip()
            clean_filename = clean_filename.replace(' ', '_') or 'media'
            filename = f"{clean_filename}.{ext}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept': '*/*',
            }
            if is_tiktok:
                headers['Referer'] = 'https://www.tiktok.com/'
            elif 'instagram.com' in url_lower:
                headers['Referer'] = 'https://www.instagram.com/'
            elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
                headers['Referer'] = 'https://www.facebook.com/'
            
            req = requests.get(
                download_url,
                headers=headers,
                stream=True,
                timeout=60,
                allow_redirects=True
            )
            
            if req.status_code >= 400:
                logger.error(f"❌ Remote stream failed: {req.status_code}")
                raise HTTPException(status_code=req.status_code, detail=f"CDN returned {req.status_code}")
            
            def iter_chunks():
                for chunk in req.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        yield chunk
            
            logger.info(f"✅ Streaming: {filename}")
            
            content_type = req.headers.get('Content-Type') or ('audio/mpeg' if ext == 'mp3' else 'video/mp4')
            content_length = req.headers.get('Content-Length')
            
            resp_headers = {
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': content_type,
                'Accept-Ranges': 'bytes',
            }
            if content_length:
                resp_headers['Content-Length'] = content_length
            
            return StreamingResponse(
                iter_chunks(),
                status_code=req.status_code,
                media_type=content_type,
                headers=resp_headers,
            )
    
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Download error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

# ============================================================================
# ERROR HANDLER
# ============================================================================
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "detail": exc.detail, "status_code": exc.status_code}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
