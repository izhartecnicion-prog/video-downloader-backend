from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import yt_dlp
import requests

app = FastAPI(title="Universal Social Video Downloader API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str

def clean_url(raw_url: str) -> str:
    url = raw_url.strip().strip("'").strip('"')
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url

@app.get("/")
def home():
    return {"status": "online", "message": "Downloader API is active"}

@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):
    target_url = clean_url(data.url)
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
        'noplaylist': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
            
            formats_list = []
            if 'formats' in info:
                for f in info['formats']:
                    if f.get('url'):
                        ext = (f.get('ext') or 'mp4').lower()
                        is_audio = ext in ['mp3', 'm4a', 'aac', 'opus', 'wav'] or f.get('vcodec') == 'none'
                        
                        formats_list.append({
                            "format_id": str(f.get("format_id", "default")),
                            "resolution": f.get("resolution") or (f"{f.get('height')}p" if f.get('height') else ("Audio MP3" if is_audio else "HD")),
                            "ext": ext,
                            "filesize_mb": round(f.get("filesize", 0) / (1024 * 1024), 2) if f.get("filesize") else "N/A",
                            "download_url": f.get("url"),
                            "is_audio": is_audio
                        })
            
            if not formats_list and info.get('url'):
                formats_list.append({
                    "format_id": "best",
                    "resolution": f"{info.get('height', '720')}p HD",
                    "ext": info.get("ext", "mp4"),
                    "filesize_mb": round(info.get("filesize", 0) / (1024 * 1024), 2) if info.get("filesize") else "N/A",
                    "download_url": info.get("url"),
                    "is_audio": False
                })

            return {
                "title": info.get("title") or "Social Video",
                "thumbnail": info.get("thumbnail") or "",
                "duration": info.get("duration") or 0,
                "platform": info.get("extractor_key") or info.get("extractor") or "Universal",
                "formats": formats_list
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/v1/stream")
def stream_media(url: str = Query(...), format_id: str = Query("best")):
    target_url = clean_url(url)
    ydl_opts = {
        'format': format_id if format_id != 'best' else 'best[ext=mp4]/best',
        'quiet': True,
        'noplaylist': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
            stream_link = info.get('url')
            
            if not stream_link and 'formats' in info:
                for f in reversed(info['formats']):
                    if f.get('url'):
                        stream_link = f.get('url')
                        break

            if not stream_link:
                raise HTTPException(status_code=404, detail="Direct media URL not found")

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Referer': target_url
            }
            req = requests.get(stream_link, headers=headers, stream=True, timeout=60)

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
