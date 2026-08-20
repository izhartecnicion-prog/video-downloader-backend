from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import yt_dlp
import subprocess

app = FastAPI(title="AI Universal Video & Audio Downloader API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str

@app.get("/")
def home():
    return {"status": "online", "message": "Universal Social Media Downloader API is running"}

@app.post("/api/v1/extract")
def extract_video_info(data: VideoRequest):
    """
    Extracts metadata, resolutions, and direct formats from ANY social media platform.
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
        'noplaylist': True,
        'extract_flat': False,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(data.url.strip(), download=False)
            
            formats_list = []
            if 'formats' in info:
                for f in info['formats']:
                    # Video ya Audio URL exist karti ho
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
            
            # Agar direct formats list empty ho to primary URL se format banayein
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
                "title": info.get("title") or "Social Media Video",
                "thumbnail": info.get("thumbnail") or "",
                "duration": info.get("duration") or 0,
                "platform": info.get("extractor_key") or info.get("extractor") or "Universal",
                "formats": formats_list
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/v1/stream")
def stream_media(url: str = Query(...), format_id: str = Query("best")):
    """
    Universal proxy stream: Bypasses 403 Forbidden and CDN token restrictions 
    for YouTube, TikTok, Instagram, Facebook, Twitter, and Reddit.
    """
    try:
        cmd = [
            "yt-dlp",
            "-f", format_id,
            "-o", "-",
            "--no-playlist",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            url.strip()
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        def iter_stream():
            try:
                while True:
                    chunk = process.stdout.read(1024 * 64)
                    if not chunk:
                        break
                    yield chunk
            finally:
                process.stdout.close()
                process.wait()

        headers = {
            "Content-Disposition": 'attachment; filename="media.mp4"',
            "Content-Type": "video/mp4"
        }

        return StreamingResponse(iter_stream(), headers=headers, media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
