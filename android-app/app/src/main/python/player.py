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
        self._resolving = False  # resolucion async de stream en curso
        self._cancel_resolve = False
        self._pending_id = None  # pista pedida mientras se resolvia otra

    def get_state(self):
        with self._lock:
            s = {
                "playing": self.playing,
                "paused": self.paused,
                "mode": self.mode,
                "resolving": self._resolving,
                "current_index": self.idx,
                "queue": list(self.queue),
                "current": dict(self.current) if self.current else None,
                "last_error": self.last_error,
            }
            cur = self.current["id"] if (self.current and self.current.get("id")) else None
        # el stat va FUERA del lock: /api/state se consulta cada 2 s y no debe
        # bloquear play/pausa/next/stream
        s["cache_bytes"] = self.tr.size(cur) if cur else 0
        # v1.3.0: modo file mientras el archivo aun se esta bajando -> el UI indica
        # "en vivo" y usa la duracion conocida para la barra.
        try:
            s["streaming"] = bool(cur and s.get("mode") == "file" and not self.tr.is_cached(cur))
        except Exception:
            s["streaming"] = False
        return s

    def _resolve_and_set(self, video_id):
        # v1.3.0: el tema anterior (si es otro) se corta del todo, descarga incluida;
        # asi yt-dlp queda libre y este tema se resuelve/descarga en seguida.
        with self._lock:
            prev = self.current.get("id") if self.current else None
        if prev and prev != video_id:
            _c = getattr(self.tr, "cancel", None)
            if _c:
                try:
                    _c(prev)
                except Exception as e:
                    logger.warning("cancel %s: %s" % (prev, e))
        info = self.res.get_info(video_id)
        if not info:
            info = {"id": video_id, "title": f"YouTube {video_id}", "duration": 0, "uploader": ""}
        cache_p = self.tr.path(video_id)
        # En Android conviene el modo "file": YouTube sirve muchos audios por SABR
        # (sin URL directa --get-url) y la descarga siempre funciona; ademas deja
        # el audio en cache (offline).
        # v1.3.0: la descarga arranca en background y se reproduce con los PRIMEROS
        # KB (antes download_bg bloqueaba y el audio sonaba solo al 100%).
        if not self.tr.is_cached(video_id) and os.environ.get("PLAYME_PREFER_FILE") == "1":
            logger.info("modo file: descargando audio de %s" % video_id)
            self.tr.download_bg(video_id, self.res)
            waiter = getattr(self.tr, "wait_partial", None)
            if waiter:
                try:
                    waiter(video_id,
                           timeout=float(os.environ.get("PLAYME_PARTIAL_WAIT", "25")),
                           min_bytes=int(os.environ.get("PLAYME_PARTIAL_MIN_BYTES", "81920")))
                except Exception as e:
                    logger.warning("wait_partial %s: %s" % (video_id, e))
        avail = cache_p if self.tr.is_cached(video_id) else None
        if avail is None:
            getp = getattr(self.tr, "available_path", None)
            if getp:
                try:
                    avail = getp(video_id)
                except Exception as e:
                    logger.warning("available_path %s: %s" % (video_id, e))
                    avail = None
        if avail:
            mode = "file"
            surl = None
            cache_p = avail
        else:
            surl = self.res.get_stream_url(video_id)
            if not surl:
                # YouTube sirve muchos audios por SABR (sin URL directa a googlevideo):
                # se descarga el audio al cache y se reproduce en modo "file".
                logger.warning("sin URL directa (%s); se descarga el audio" % (self.res.last_error or "?"))
                self.tr.download_bg(video_id, self.res)
                waiter = getattr(self.tr, "wait_done", None)
                if waiter:
                    try:
                        waiter(video_id, timeout=180.0)
                    except Exception as e:
                        logger.warning("wait_done %s: %s" % (video_id, e))
                if not self.tr.is_cached(video_id):
                    self.last_error = self.res.last_error or f"no stream for {video_id}"
                    logger.error(f"no stream for {video_id}: {self.last_error}")
                    return False
                mode = "file"
                surl = None
                cache_p = self.tr.path(video_id)
            else:
                mode = "proxy"
                if os.environ.get("PLAYME_NO_BG_DOWNLOAD") != "1":
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
        """Play ASINCRONO: responde inmediato y resuelve el stream en un thread
        (la resolucion de yt-dlp tarda ~20s; esperarla en el handler dejaba la
        UI en 'Cargando...' 20 segundos). El state expone resolving=true hasta
        que el stream este listo.

        v1.3.0: pedir OTRO tema corta el actual DE INMEDIATO (antes seguia
        sonando 'encima' mientras se resolvia el nuevo). El nuevo arranca en
        cuanto tiene sus primeros bytes."""
        with self._lock:
            cur = self.current.get("id") if self.current else None
            if cur and cur != video_id:
                self.playing = False
                self.paused = False
                self.mode = None
                self.stream_url = None
                self.cache_path = None
                self.current = None
                # v1.3.0: cortar tambien su DESCARGA (no solo el audio): libera
                # yt-dlp para que el tema nuevo se resuelva en seguida.
                _c = getattr(self.tr, "cancel", None)
                if _c:
                    try:
                        _c(cur)
                    except Exception as e:
                        logger.warning("cancel %s: %s" % (cur, e))
            if self._resolving:
                # si se pide otra cancion mientras resuelve, se recuerda para
                # reproducirla al terminar (antes se ignoraba en silencio)
                if video_id != self._pending_id:
                    self._pending_id = video_id
                return True
            self._resolving = True
            self._cancel_resolve = False
            self._pending_id = None
            self.last_error = None
        threading.Thread(target=self._play_async, args=(video_id,), daemon=True).start()
        return True

    def _play_async(self, video_id):
        ok = self._resolve_and_set(video_id)
        with self._lock:
            if self._cancel_resolve:
                # el usuario hizo stop/next durante la resolucion: no resucitar
                self._resolving = False
                self._stop_locked()
                return
            self._resolving = False
            if not ok:
                return
            if self._pending_id:
                # el usuario pidio otra cancion mientras resolvia: se reproduce
                pend = self._pending_id
                self._pending_id = None
                self._resolving = True
                self._cancel_resolve = False
                threading.Thread(target=self._play_async, args=(pend,), daemon=True).start()
                return
            track = dict(self.current)
            if not self.queue or self.idx < 0:
                self.queue = [track]
                self.idx = 0
            else:
                # dedupe: al reproducir varias veces el mismo video no hay que
                # llenar la cola de copias (antes insertaba siempre)
                keep = [q for i, q in enumerate(self.queue) if q.get("id") != track["id"] or i >= self.idx]
                self.idx = max(0, min(self.idx, len(keep) - 1))
                self.queue = keep
                self.queue.insert(self.idx + 1, track)
                self.idx += 1

    def _stop_locked(self):
        """Detiene la reproduccion. DEBE llamarse con self._lock YA tomado."""
        cur = self.current.get("id") if self.current else None
        if cur:
            # v1.3.0: stop corta tambien la descarga en curso
            _c = getattr(self.tr, "cancel", None)
            if _c:
                try:
                    _c(cur)
                except Exception:
                    pass
        self.playing = False
        self.paused = False
        self.mode = None
        self.stream_url = None
        self.cache_path = None
        self.current = None
        self.queue = []
        self.idx = -1
        self.last_error = None
        self._resolving = False
        self._cancel_resolve = True

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
        with self._lock:
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
                    # limpieza completa (marca la cancelacion) conservando el resto
                    rest = list(self.queue)
                    self.current = None
                    self._stop_locked()
                    self.queue = rest
                    self.idx = -1
                return True
        return False
