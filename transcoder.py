"""
Transcoder: descarga audio a cache con yt-dlp.
Eliminado: no usa mpv, no tiene stop() bloqueante.
"""
import logging, os, subprocess, threading, time

logger = logging.getLogger(__name__)
CACHE = "/tmp/playme_cache"

class Transcoder:
    def __init__(self):
        os.makedirs(CACHE, exist_ok=True)

    def path(self, vid):
        return os.path.join(CACHE, f"{vid}.webm")

    def is_cached(self, vid):
        p = self.path(vid)
        return os.path.isfile(p) and os.path.getsize(p) > 0

    def size(self, vid):
        p = self.path(vid)
        return os.path.getsize(p) if os.path.isfile(p) else 0

    def download_bg(self, vid, resolver):
        """Descarga en background. No bloquea."""
        if self.is_cached(vid):
            return
        url = f"https://www.youtube.com/watch?v={vid}"
        args = resolver._args() + [
            "--format", "bestaudio[ext=webm]/bestaudio",
            "--output", self.path(vid),
            "--no-part", "--no-mtime", url
        ]
        try:
            subprocess.run(args, capture_output=True, text=True, timeout=60)
            logger.info(f"cache ok: {vid} ({self.size(vid)} bytes)")
        except Exception as e:
            logger.warning(f"cache fail {vid}: {e}")

    def cleanup(self, hours=24):
        now = time.time()
        for f in os.listdir(CACHE):
            p = os.path.join(CACHE, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p)) / 3600 > hours:
                os.unlink(p)
