"""
PlayMe - Player
Gestiona cola de reproduccion, estado, y coordina resolver + transcoder.
Flujo hibrido:
  1. Responde inmediato: modo proxy (yt-dlp --get-url)
  2. Background: descarga a cache con yt-dlp directo
  3. Proximo play usa cache si existe
El player_lock solo protege estado, no bloquea durante descargas.
"""
import logging
import os
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

CACHE_DIR = "/tmp/playme_cache"


class Player:
    def __init__(self, resolver, transcoder):
        self.resolver = resolver
        self.transcoder = transcoder

        self.queue = []
        self.current_index = -1
        self.playing = False
        self.paused = False
        self.current_mode = None  # "proxy" | "file"
        self.stream_url = None
        self.cache_path = None
        self.current_info = None
        self._lock = threading.Lock()
        self._on_state_change = None
        logger.info("Player init")

    def set_on_state_change(self, callback):
        self._on_state_change = callback

    def search(self, query: str, limit: int = 10):
        return self.resolver.search(query, limit)

    def _download_cache_bg(self, video_id: str):
        """Descarga a cache en thread separado. No bloquea el play."""
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(CACHE_DIR, f"{video_id}.webm")

        if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
            logger.info(f"Cache ya existe: {cache_path}")
            return cache_path

        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"Background cache de {video_id}")

        args = self.resolver._base_args() + [
            "--format", "bestaudio[ext=webm]/bestaudio",
            "--output", cache_path,
            "--no-part",
            "--no-mtime",
            url
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=180
            )
            if result.returncode == 0 and os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
                size = os.path.getsize(cache_path)
                logger.info(f"Cache OK: {cache_path} ({size} bytes)")
                return cache_path
            else:
                logger.warning(f"Cache download failed: {result.stderr[:200]}")
                return None
        except subprocess.TimeoutExpired:
            logger.error("Cache download timeout")
            return None
        except Exception as e:
            logger.error(f"Cache download error: {e}")
            return None

    def play(self, video_id: str):
        """Reproduce un video por ID.
        
        Flujo:
        1. Si existe en cache -> modo file (instantaneo)
        2. Si no -> modo proxy (yt-dlp --get-url, rapido)
           + background download a cache
        """
        with self._lock:
            info = self.resolver.get_info(video_id)
            if not info:
                logger.warning(f"No info for {video_id}")
                info = {"id": video_id, "title": f"YouTube {video_id}",
                        "duration": 0, "uploader": ""}

            self.transcoder.stop()
            self.playing = False
            self.paused = False
            self.current_mode = None
            self.stream_url = None
            self.cache_path = None

            title = info.get("title", "Sin titulo")
            duration = info.get("duration", 0)
            uploader = info.get("uploader", "")
            self.current_info = info

            logger.info(f"Play: {title} ({video_id})")

            # 1) Cache existente?
            cache_path = os.path.join(CACHE_DIR, f"{video_id}.webm")
            if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
                self.cache_path = cache_path
                self.current_mode = "file"
                self.playing = True
                logger.info(f"Modo file desde cache: {cache_path}")
            else:
                # 2) Proxy streaming (rapido, responde en segundos)
                stream_url = self.resolver.get_stream_url(video_id)
                if stream_url:
                    self.stream_url = stream_url
                    self.current_mode = "proxy"
                    self.playing = True
                    logger.info("Modo proxy (stream directo)")

                    # Background: descargar a cache para proxima vez
                    t = threading.Thread(
                        target=self._download_cache_bg,
                        args=(video_id,),
                        daemon=True
                    )
                    t.start()
                else:
                    logger.error("No se pudo obtener stream URL")
                    return False

            # Actualizar cola
            track = {
                "id": video_id,
                "title": title,
                "duration": duration,
                "uploader": uploader
            }
            if not self.queue or self.current_index < 0:
                self.queue = [track]
                self.current_index = 0
            else:
                self.queue.insert(self.current_index + 1, track)
                self.current_index += 1

            self._notify_state()
            return True

    def play_next(self):
        with self._lock:
            if self.current_index < len(self.queue) - 1:
                self.current_index += 1
                next_track = self.queue[self.current_index]
                logger.info(f"Next: {next_track['title']}")
            else:
                logger.info("No more in queue")
                self.stop()
                return False
        return self.play(self.queue[self.current_index]["id"])

    def play_prev(self):
        with self._lock:
            if self.current_index > 0:
                self.current_index -= 1
                prev_track = self.queue[self.current_index]
                logger.info(f"Prev: {prev_track['title']}")
            else:
                logger.info("Already at start")
                return False
        return self.play(self.queue[self.current_index]["id"])

    def toggle_pause(self):
        self.paused = not self.paused
        logger.info(f"Pause state: {self.paused}")
        self._notify_state()
        return self.paused

    def stop(self):
        with self._lock:
            self.transcoder.stop()
            self.playing = False
            self.paused = False
            self.current_mode = None
            self.stream_url = None
            self.cache_path = None
            self.current_info = None
            self.queue = []
            self.current_index = -1
            logger.info("Stopped")
        self._notify_state()

    def add_to_queue(self, video_id: str):
        info = self.resolver.get_info(video_id)
        if info:
            track = {
                "id": video_id,
                "title": info.get("title", "Sin titulo"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "")
            }
            with self._lock:
                self.queue.append(track)
            self._notify_state()
            return True
        return False

    def remove_from_queue(self, index: int):
        with self._lock:
            if 0 <= index < len(self.queue):
                removed = self.queue.pop(index)
                if index < self.current_index:
                    self.current_index -= 1
                elif index == self.current_index:
                    self.current_index = -1
                logger.info(f"Removed from queue: {removed['title']}")
                self._notify_state()
                return True
        return False

    def get_state(self):
        with self._lock:
            state = {
                "playing": self.playing,
                "paused": self.paused,
                "mode": self.current_mode,
                "current_index": self.current_index,
                "queue": self.queue,
                "current": None,
            }
            if self.current_info:
                state["current"] = {
                    "id": self.current_info.get("id"),
                    "title": self.current_info.get("title"),
                    "duration": self.current_info.get("duration"),
                    "uploader": self.current_info.get("uploader"),
                    "thumbnail": self.current_info.get("thumbnail"),
                }
            cache_path = os.path.join(CACHE_DIR, f"{self.current_info.get('id')}.webm") if self.current_info else None
            if cache_path and os.path.isfile(cache_path):
                state["cache_bytes"] = os.path.getsize(cache_path)
            else:
                state["cache_bytes"] = 0
            return state

    def _notify_state(self):
        if self._on_state_change:
            self._on_state_change(self.get_state())
