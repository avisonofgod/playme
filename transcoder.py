"""
PlayMe - Transcoder
Wrapper de mpv para descarga raw de streams de YouTube.
El cache guarda el stream RAW (webm/opus) que el navegador reproduce nativamente.
"""
import os
import signal
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

CACHE_DIR = "/tmp/playme_cache"

class Transcoder:
    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.mpv_process = None
        self.current_video_id = None
        self._lock = threading.Lock()
        self._running = False
        logger.info("Transcoder init")

    def _get_cache_path(self, video_id: str):
        return os.path.join(CACHE_DIR, f"{video_id}.webm")

    def is_cached(self, video_id: str):
        path = self._get_cache_path(video_id)
        return os.path.isfile(path) and os.path.getsize(path) > 0

    def get_cache_size(self, video_id: str):
        path = self._get_cache_path(video_id)
        if os.path.isfile(path):
            return os.path.getsize(path)
        return 0

    def start_file(self, video_id: str, youtube_url: str):
        """
        Descarga stream RAW via mpv --stream-record (webm/opus).
        El navegador reproduce webm nativamente con Range support.
        Bloqueante hasta que termina o falla.
        """
        cache_path = self._get_cache_path(video_id)
        logger.info(f"Cache descarga raw: {cache_path}")

        if self.is_cached(video_id):
            logger.info(f"Cache hit: {cache_path}")
            return cache_path

        args = [
            "mpv", "--no-video", "--vo=null", "--ao=null",
            "--ytdl-format=bestaudio",
            "--stream-record", cache_path,
            "--no-terminal", "--msg-level=all=no",
            youtube_url
        ]
        try:
            subprocess.run(args, check=True,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           timeout=120)
            if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
                logger.info(f"Cache OK: {cache_path} "
                           f"({os.path.getsize(cache_path)} bytes)")
                return cache_path
        except subprocess.TimeoutExpired:
            logger.error("Cache timeout")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Cache error: {e}")
        return None

    def stop(self):
        with self._lock:
            if self.mpv_process and self.mpv_process.poll() is None:
                logger.info("Stop mpv")
                self.mpv_process.terminate()
                try:
                    self.mpv_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.mpv_process.kill()
                    self.mpv_process.wait(timeout=2)
            self.mpv_process = None
            self._running = False

    def cleanup_old_cache(self, max_age_hours=24):
        try:
            import time
            now = time.time()
            for f in os.listdir(CACHE_DIR):
                path = os.path.join(CACHE_DIR, f)
                if os.path.isfile(path) and not f.startswith("pipe_"):
                    age_h = (now - os.path.getmtime(path)) / 3600
                    if age_h > max_age_hours:
                        os.unlink(path)
                        logger.info(f"Cache purged: {f}")
        except Exception as e:
            logger.warning(f"Cache cleanup error: {e}")

    @property
    def is_playing(self):
        return self._running
