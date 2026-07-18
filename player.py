"""
PlayMe - Player
Gestiona cola de reproducción, estado, y coordina resolver + transcoder.
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

class Player:
    def __init__(self, resolver, transcoder):
        self.resolver = resolver
        self.transcoder = transcoder

        # Estado
        self.queue = []          # [{id, title, duration, uploader}]
        self.current_index = -1
        self.playing = False
        self.paused = False
        self.current_mode = None  # "proxy" | "file" | "pipe"
        self.stream_url = None
        self.cache_path = None
        self.current_info = None
        self._lock = threading.Lock()
        self._on_state_change = None
        logger.info("Player init")

    def set_on_state_change(self, callback):
        self._on_state_change = callback

    def search(self, query: str, limit: int = 10):
        """Busca canciones."""
        return self.resolver.search(query, limit)

    def play(self, video_id: str):
        """Reproduce un video por ID."""
        with self._lock:
            # Obtener info del video (fallback a info mínima si falla)
            info = self.resolver.get_info(video_id)
            if not info:
                logger.warning(f"No info for {video_id}, usando info mínima")
                info = {"id": video_id, "title": f"YouTube {video_id}",
                        "duration": 0, "uploader": ""}

            # Detener lo que sea que esté sonando
            self.transcoder.stop()
            self.playing = False
            self.paused = False
            self.current_mode = None
            self.stream_url = None
            self.cache_path = None

            title = info.get("title", "Sin título")
            duration = info.get("duration", 0)
            uploader = info.get("uploader", "")
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            self.current_info = info

            logger.info(f"Play: {title} ({video_id})")

            # ESTRATEGIA 1: ¿Ya está en cache?
            if self.transcoder.is_cached(video_id):
                self.cache_path = self.transcoder._get_cache_path(video_id)
                self.current_mode = "file"
                self.playing = True
                logger.info(f"Modo file (cache): {self.cache_path}")
            else:
                # Si get_info no dio título real (falló), probablemente 
                # el video está bloqueado. Intentar proxy rápido.
                if not uploader and duration == 0:
                    logger.warning("Video parece bloqueado, intento rápido")
                    stream_url = None
                    try:
                        stream_url = self.resolver.get_stream_url(video_id)
                    except Exception:
                        pass
                else:
                    # ESTRATEGIA 2: Intentar stream directo (proxy)
                    stream_url = self.resolver.get_stream_url(video_id)
                if stream_url:
                    self.stream_url = stream_url
                    self.current_mode = "proxy"
                    self.playing = True
                    logger.info("Modo proxy (stream directo)")

                    # En background: descargar a cache para futuras veces
                    def cache_background():
                        logger.info(f"Background cache de {video_id}")
                        self.transcoder.start_file(video_id, youtube_url)
                    threading.Thread(target=cache_background, daemon=True).start()
                else:
                    # ESTRATEGIA 3: mpv transcode completo a cache
                    logger.info("Modo file (mpv transcode a cache)")
                    cache_path = self.transcoder.start_file(
                        video_id, youtube_url)
                    if cache_path:
                        self.cache_path = cache_path
                        self.current_mode = "file"
                        self.playing = True
                        logger.info(f"Modo file OK: {cache_path}")
                    else:
                        logger.error("No se pudo iniciar reproducción")
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
                # Insertar como siguiente
                self.queue.insert(self.current_index + 1, track)
                self.current_index += 1

            self._notify_state()
            return True

    def play_next(self):
        """Siguiente en cola."""
        with self._lock:
            if self.current_index < len(self.queue) - 1:
                self.current_index += 1
                next_track = self.queue[self.current_index]
                logger.info(f"Next: {next_track['title']}")
            else:
                logger.info("No more in queue")
                self.stop()
                return False

        # Reproducir fuera del lock
        return self.play(self.queue[self.current_index]["id"])

    def play_prev(self):
        """Anterior en cola."""
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
        # Pause solo tiene sentido en modo file (mpv activo reproduciendo)
        # En modo proxy, el stream viene de Google y no podemos pausarlo
        if self.current_mode == "file" and self.playing:
            if not self.paused:
                self.paused = True
                logger.info("Paused (file mode)")
            else:
                self.paused = False
                logger.info("Resumed (file mode)")
        elif self.current_mode == "proxy":
            # En proxy no hay pause real, simulamos
            self.paused = not self.paused
            logger.info(f"Pause state: {self.paused} (proxy - no op)")
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
        """Añade a la cola (no reproduce)."""
        info = self.resolver.get_info(video_id)
        if info:
            track = {
                "id": video_id,
                "title": info.get("title", "Sin título"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "")
            }
            with self._lock:
                self.queue.append(track)
            self._notify_state()
            return True
        return False

    def remove_from_queue(self, index: int):
        """Quita de la cola por índice."""
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
        """Devuelve el estado completo para la API."""
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
            # Cache status
            if self.cache_path and os.path.isfile(self.cache_path):
                state["cache_bytes"] = os.path.getsize(self.cache_path)
            else:
                state["cache_bytes"] = 0
            return state

    def _notify_state(self):
        if self._on_state_change:
            self._on_state_change(self.get_state())
