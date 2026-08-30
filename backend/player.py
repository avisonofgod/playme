"""Player: cola, reproduccion, estado."""
import logging, os, threading
logger = logging.getLogger(__name__)

class Player:
    def __init__(self, resolver, transcoder):
        self.res = resolver
        self.tr = transcoder
        self.queue = []
        self.idx = -1
        self.playing = False
        self.paused = False
        self.mode = None
        self.stream_url = None
        self.cache_path = None
        self.current = None
        self.last_error = None
        self._lock = threading.Lock()

    def get_state(self):
        with self._lock:
            s = {
                "playing": self.playing,
                "paused": self.paused,
                "mode": self.mode,
                "current_index": self.idx,
                "queue": list(self.queue),
                "current": dict(self.current) if self.current else None,
            }
            if self.current and self.current.get("id"):
                s["cache_bytes"] = self.tr.size(self.current["id"])
            else:
                s["cache_bytes"] = 0
            s["last_error"] = self.last_error
            return s

    def _resolve_and_set(self, video_id):
        info = self.res.get_info(video_id)
        if not info:
            info = {"id": video_id, "title": f"YouTube {video_id}", "duration": 0, "uploader": ""}
        cache_p = self.tr.path(video_id)
        if self.tr.is_cached(video_id):
            mode = "file"
            surl = None
        else:
            surl = self.res.get_stream_url(video_id)
            if not surl:
                self.last_error = self.res.last_error or f"no stream for {video_id}"
                logger.error(f"no stream for {video_id}: {self.last_error}")
                return False
            mode = "proxy"
            t = threading.Thread(target=self.tr.download_bg, args=(video_id, self.res), daemon=True)
            t.start()
        track = {
            "id": video_id,
            "title": info.get("title", f"YouTube {video_id}"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", ""),
            "thumbnail": info.get("thumbnail", ""),
        }
        with self._lock:
            self.last_error = None
            self.playing = True
            self.paused = False
            self.mode = mode
            self.stream_url = surl
            self.cache_path = cache_p if mode == "file" else None
            self.current = track
        return True

    def _play_current(self):
        if self.idx < 0 or self.idx >= len(self.queue):
            return False
        t = self.queue[self.idx]
        return self._resolve_and_set(t["id"])

    def play(self, video_id):
        if not self._resolve_and_set(video_id):
            return False
        track = dict(self.current)
        with self._lock:
            if not self.queue or self.idx < 0:
                self.queue = [track]
                self.idx = 0
            else:
                self.queue.insert(self.idx + 1, track)
                self.idx += 1
        return True

    def _stop_locked(self):
        """Detiene la reproduccion. DEBE llamarse con self._lock YA tomado."""
        self.playing = False
        self.paused = False
        self.mode = None
        self.stream_url = None
        self.cache_path = None
        self.current = None
        self.queue = []
        self.idx = -1
        self.last_error = None

    def stop(self):
        """Detiene la reproduccion y limpia la cola."""
        with self._lock:
            self._stop_locked()

    def next(self):
        with self._lock:
            if self.idx < len(self.queue) - 1:
                self.idx += 1
            else:
                # ultimo elemento: detener bajo el lock ya tomado (evita deadlock
                # por stop() que re-adquiriria self._lock).
                self._stop_locked()
                return False
        ok = self._play_current()
        if not ok:
            self._restore_after_fail(-1)
            return False
        return True

    def prev(self):
        with self._lock:
            if self.idx > 0:
                self.idx -= 1
            else:
                return False
        ok = self._play_current()
        if not ok:
            self._restore_after_fail(1)
            return False
        return True

    def _restore_after_fail(self, delta):
        """Si _play_current fallo, restaura idx y conserva el track anterior."""
        with self._lock:
            self.idx += delta  # revertir el desplazamiento

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused

    def add_queue(self, video_id, title=None, duration=0, uploader=""):
        track = {
            "id": video_id,
            "title": title or f"YouTube {video_id}",
            "duration": duration,
            "uploader": uploader or "",
            "thumbnail": "",
        }
        with self._lock:
            self.queue.append(track)

    def remove_queue(self, index):
        with self._lock:
            if 0 <= index < len(self.queue):
                self.queue.pop(index)
                if index < self.idx:
                    self.idx -= 1
                elif index == self.idx:
                    self.idx = -1
                    self.playing = False
                    self.paused = False
                    self.mode = None
                    self.stream_url = None
                    self.cache_path = None
                    self.current = None
                    self.last_error = None
                return True
        return False
