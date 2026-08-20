import requests
from fastapi.responses import StreamingResponse
from fastapi import Query, HTTPException

@app.get("/api/v1/stream")
def stream_media(url: str = Query(...), format_id: str = Query("best")):
    """
    Fetches real media URL via yt-dlp and proxies the stream reliably.
    """
    ydl_opts = {
        'format': format_id if format_id != 'best' else 'best[ext=mp4]/best',
        'quiet': True,
        'noplaylist': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url.strip(), download=False)
            target_url = info.get('url')
            
            # Agar format url list mein ho
            if not target_url and 'formats' in info:
                for f in reversed(info['formats']):
                    if f.get('url'):
                        target_url = f.get('url')
                        break

            if not target_url:
                raise HTTPException(status_code=404, detail="Direct stream URL not found")

            # Stream through backend with browser headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Referer': url.strip()
            }
            req = requests.get(target_url, headers=headers, stream=True, timeout=60)

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
