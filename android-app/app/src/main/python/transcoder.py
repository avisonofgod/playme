"""
Transcoder: descarga audio a cache con yt-dlp.
Eliminado: no usa mpv, no tiene stop() bloqueante.
Bugfix: usa archivo .part para descarga -> evita tratar parcial como cached.
Refactor: usa runner.run_command inyectable.
"""
import logging, os, threading, time

from runner import run_command

logger = logging.getLogger(__name__)
CACHE = os.environ.get("PLAYME_CACHE_DIR", "/tmp/playme_cache")

# v1.3.0: la descarga sigue a la REPRODUCCION (no baja el archivo completo).
# Se mantiene en disco/red solo lo que hace falta: posicion + AHEAD_SECONDS.
AHEAD_SECONDS = float(os.environ.get("PLAYME_AHEAD_SECONDS", "5"))
AHEAD_MIN_BYTES = int(os.environ.get("PLAYME_AHEAD_MIN_BYTES", "131072"))   # piso ~10 s
AHEAD_RATE_DEFAULT = float(os.environ.get("PLAYME_AHEAD_RATE", "24000"))    # B/s si no se sabe el bitrate
PACED_TIMEOUT = float(os.environ.get("PLAYME_PACED_TIMEOUT", "7200"))       # tope de la descarga pausada
PACE_TICK = float(os.environ.get("PLAYME_PACE_TICK", "0.25"))


def _strip_cookies(args):
    """Copia los args de yt-dlp sin el par --cookies <archivo> (el cliente ios
    no soporta cookies y yt-dlp lo descarta si se le pasan)."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--cookies":
            skip = True
            continue
        out.append(a)
    return out

class Transcoder:
    def __init__(self, runner=None):
        self._runner = runner or run_command
        os.makedirs(CACHE, exist_ok=True)
        self._active = {}          # vid -> True mientras la descarga esta en curso
        self._cancel = {}          # vid -> threading.Event de la descarga en curso
        self._active_lock = threading.Lock()
        self._paced = {}           # vid -> {dur, size, rate}: descarga al ritmo de reproduccion
        self._pos = {}             # vid -> posicion (seg) que reporta la UI
        self._pos_t = {}           # vid -> cuando se reporto esa posicion

    # ── ritmo de descarga (v1.3.0) ─────────────────────────────────────────
    def enable_pace(self, vid, duration=0, filesize=0):
        """Marca esta descarga como 'sigue a la reproduccion': no baja el archivo
        completo, solo posicion + PLAYME_AHEAD_SECONDS.

        duration (seg) y filesize (bytes) del tema permiten calcular el bitrate y
        asi saber cuantos bytes son '5 segundos mas'."""
        with self._active_lock:
            self._paced[vid] = {"dur": float(duration or 0), "size": int(filesize or 0),
                                "rate": 0.0}

    def disable_pace(self, vid):
        """Descarga completa (p.ej. el boton Descargar audio, que quiere el archivo)."""
        with self._active_lock:
            self._paced.pop(vid, None)

    def position(self, vid, seconds):
        """La UI reporta donde va la reproduccion (segundos). Con esto la descarga
        se pausa cuando ya hay mas audio del necesario (posicion + 5 s)."""
        if not vid:
            return
        try:
            s = max(0.0, float(seconds or 0))
        except (TypeError, ValueError):
            return
        with self._active_lock:
            self._pos[vid] = s
            self._pos_t[vid] = time.time()

    def is_paced(self, vid):
        with self._active_lock:
            return bool(self._paced.get(vid))

    def pace_bytes(self, vid, downloaded=0, total=0, duration=0):
        """Bytes/segundo de audio (bitrate) para decidir cuanto adelantar."""
        with self._active_lock:
            st = self._paced.get(vid) or {}
            dur = float(duration or st.get("dur") or 0)
            size = float(total or st.get("size") or 0)
            r = st.get("rate") or 0.0
            if size > 0 and dur > 0:
                r = size / dur
                if st:
                    st["rate"] = r
                    st["dur"] = dur
        return r or AHEAD_RATE_DEFAULT

    def _pace_for(self, vid):
        """Callback del progress hook de yt-dlp: BLOQUEA (pausa la descarga)
        mientras lo descargado pase de posicion + AHEAD_SECONDS.

        Si la UI no reporta posicion (o dejo de reportarla) devuelve de inmediato:
        se mantiene el comportamiento anterior (descarga completa)."""
        logged = {"v": False}

        def _fn(downloaded, total=0, duration=0):
            while True:
                with self._active_lock:
                    paced = self._paced.get(vid)
                    pos = self._pos.get(vid, 0.0)
                    seen = self._pos_t.get(vid)
                    ev = self._cancel.get(vid)
                if not paced:
                    return                            # descarga completa pedida (Descargar)
                if ev is not None and ev.is_set():
                    return
                if not seen or (time.time() - seen) > 30.0:
                    return                            # sin datos de la UI: descarga normal
                rate = self.pace_bytes(vid, downloaded, total, duration)
                limit = pos * rate + max(AHEAD_MIN_BYTES, AHEAD_SECONDS * rate)
                if (downloaded or 0) <= limit:
                    return
                if not logged["v"]:
                    logged["v"] = True
                    logger.info("descarga al ritmo de la reproduccion: %s pausa en %d bytes "
                                "(posicion %.0fs, %.0f B/s de audio)"
                                % (vid, downloaded or 0, pos, rate))
                time.sleep(PACE_TICK)
        return _fn

    def path(self, vid):
        return os.path.join(CACHE, f"{vid}.webm")

    def _part(self, vid):
        return self.path(vid) + ".part"

    def is_cached(self, vid):
        p = self.path(vid)
        return os.path.isfile(p) and os.path.getsize(p) > 0

    def part_path(self, vid):
        return self._part(vid)

    def available_path(self, vid):
        """Archivo COMPLETO si existe; si no, el .part con datos (reproduccion
        progresiva: v1.3.0 permite empezar a sonar antes del 100%)."""
        p = self.path(vid)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
        pp = self._part(vid)
        if os.path.isfile(pp) and os.path.getsize(pp) > 0:
            return pp
        return None

    def is_downloading(self, vid):
        with self._active_lock:
            return bool(self._active.get(vid))

    def size(self, vid):
        """Bytes disponibles ya (final o .part en curso)."""
        for p in (self.path(vid), self._part(vid)):
            if os.path.isfile(p):
                try:
                    n = os.path.getsize(p)
                except OSError:
                    n = 0
                if n > 0:
                    return n
        return 0

    def wait_partial(self, vid, timeout=25.0, min_bytes=81920):
        """Espera los PRIMEROS bytes del audio (o el final de la descarga).

        v1.3.0: el play ya no espera al 100%; con ~80 KB (unos 5 s de audio) el
        <audio> puede empezar y el resto llega mientras suena."""
        end = time.time() + max(0.0, timeout)
        while True:
            p = self.available_path(vid)
            if self.is_cached(vid):
                return self.path(vid)
            try:
                ok = p and os.path.getsize(p) >= min_bytes
            except OSError:
                ok = False
            if ok:
                return p
            if not self.is_downloading(vid):
                return p
            if time.time() >= end:
                return p
            time.sleep(0.2)

    def wait_done(self, vid, timeout=600.0):
        """Espera a que la descarga termine (usado por la cola de Descargas)."""
        end = time.time() + max(0.0, timeout)
        while True:
            if self.is_cached(vid):
                return True
            if not self.is_downloading(vid):
                return self.is_cached(vid)
            if time.time() >= end:
                return self.is_cached(vid)
            time.sleep(0.3)

    def download_bg(self, vid, resolver):
        """Arranca la descarga en background y RETORNA YA (idempotente).

        Antes bloqueaba hasta el 100%: con PLAYME_PREFER_FILE el play no
        empezaba hasta que el archivo estaba completo (v1.2.x)."""
        if self.is_cached(vid):
            return
        with self._active_lock:
            if self._active.get(vid):
                return
            self._active[vid] = True
            self._cancel[vid] = threading.Event()
        threading.Thread(target=self._download_worker, args=(vid, resolver), daemon=True).start()

    def cancel(self, vid):
        """Aborta la descarga en curso de `vid` (el usuario cambio de tema o stop).

        v1.3.0: al cortarse el tema tambien se corta su descarga; ademas libera el
        yt-dlp (una sola instancia) para que el tema NUEVO pueda resolverse en
        seguida en vez de esperar a que termine la descarga anterior."""
        with self._active_lock:
            ev = self._cancel.get(vid)
            active = bool(self._active.get(vid))
            if ev is not None:
                ev.set()
        if active:
            logger.info("descarga cancelada por el usuario: %s" % vid)
        return active

    def forget(self, vid):
        """Descarta el cache de un tema que se ABANDONA (play de otro/next/prev/stop).

        v1.3.0: al cambiar de tema solo debe quedar en cache el tema ACTUAL;
        el audio del anterior se borra del disco (antes se acumulaba y al
        volver con prev se reusaba el audio viejo)."""
        if not vid:
            return 0
        self.cancel(vid)
        removed = 0
        for p in (self.path(vid), self._part(vid)):
            try:
                if os.path.isfile(p):
                    removed += os.path.getsize(p)
                    os.unlink(p)
            except OSError as e:
                logger.warning("forget %s: %s" % (vid, e))
        # el worker puede reescribir el .part justo despues del unlink
        p = self._part(vid)
        if os.path.isfile(p):
            try:
                os.unlink(p)
            except OSError:
                pass
        if removed:
            logger.info("cache borrado: %s (%d bytes)" % (vid, removed))
        return removed

    def forget_except(self, keep=None):
        """Deja en cache SOLO `keep` (el tema actual): borra los audios de otros
        temas ya descargados. No toca temporales ni descargas explicitas."""
        keep = keep or ""
        removed = 0
        try:
            names = os.listdir(CACHE)
        except OSError:
            return 0
        for name in names:
            vid = None
            if name.endswith(".webm"):
                vid = name[:-5]
            elif name.endswith(".webm.part"):
                vid = name[:-10]
            if not vid or vid == keep:
                continue
            if not (len(vid) == 11 and vid.replace("-", "").replace("_", "").isalnum()):
                continue
            p = os.path.join(CACHE, name)
            try:
                if os.path.isfile(p):
                    removed += os.path.getsize(p)
                    os.unlink(p)
            except OSError:
                pass
        return removed

    def is_cancelled(self, vid):
        ev = self._cancel.get(vid)
        return bool(ev is not None and ev.is_set())

    def _cancel_event(self, vid):
        with self._active_lock:
            ev = self._cancel.get(vid)
            if ev is None:
                ev = threading.Event()
                self._cancel[vid] = ev
            return ev

    def _download_worker(self, vid, resolver):
        try:
            self._download_sync(vid, resolver)
        finally:
            with self._active_lock:
                self._active.pop(vid, None)
                self._cancel.pop(vid, None)

    def _download_sync(self, vid, resolver):
        """Descarga a .part y renombra a final al completar.
        Asi, un archivo parcial (descarga abortada) NO se marca como cached."""
        if self.is_cached(vid):
            return
        ev = self._cancel_event(vid)
        part = self._part(vid)
        # v1.3.0: si la descarga sigue a la reproduccion, el timeout es largo (puede
        # durar toda la cancion) y el progreso lo marca el hook con `pace`.
        pace = self._pace_for(vid) if self.is_paced(vid) else None
        tmo = PACED_TIMEOUT if pace else 180.0
        args = resolver._args()
        base = [
            "--format", "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
            "--output", part,
            "--no-part", "--no-mtime", f"https://www.youtube.com/watch?v={vid}"
        ]
        # Intentos: el cliente POR DEFECTO es el que funciona en el PC (mismo
        # yt-dlp y misma salida a Internet). Los demas van de respaldo: "ios"
        # descarga sin runtime JS pero lo descarta si hay cookies; android_vr
        # da URL y 403 al bajar. Se comparan midiendo cual deja el archivo.
        attempts = [(args, None), (_strip_cookies(args), None)]
        if "youtube:player_client=ios" not in " ".join(args):
            attempts.append((_strip_cookies(args), "youtube:player_client=ios"))
            attempts.append((args, "youtube:player_client=android_vr"))
            attempts.append((_strip_cookies(args), "youtube:player_client=android_vr"))
        cancelled = False
        try:
            rc = 1
            for base_args, client in attempts:
                if ev.is_set():          # v1.3.0: el usuario corto este tema
                    cancelled = True
                    break
                a2 = base_args + (["--extractor-args", client] if client else []) + base
                res = resolver._run(a2, timeout=tmo, cancel=ev, pace=pace)
                rc = getattr(res, "returncode", 0)
                if getattr(res, "cancelled", False) or ev.is_set():
                    cancelled = True
                    break
                if rc:
                    se = getattr(res, "stderr", b"") or b""
                    if isinstance(se, bytes): se = se.decode("utf-8", "replace")
                    logger.warning("dl rc=%s (%s): %s" % (rc, client, se.strip()[-200:]))
                if os.path.isfile(part) and os.path.getsize(part) > 0:
                    break
            if cancelled:
                logger.info("descarga cortada: %s" % vid)
            elif os.path.isfile(part) and os.path.getsize(part) > 0:
                if pace is not None and rc == 124:
                    # ritmo de reproduccion: se acabo el tiempo con la descarga
                    # pausada; se conserva lo bajado (el audio sigue sonando)
                    logger.info("descarga pausada al ritmo de reproduccion: %s (%d bytes)"
                                % (vid, os.path.getsize(part)))
                    return
                os.rename(part, self.path(vid))
                logger.info(f"cache ok: {vid} ({self.size(vid)} bytes)")
                return
            else:
                logger.warning(f"cache empty: {vid}")
            # cancelada o vacia: no dejar .part a medias en el cache
            if os.path.isfile(part):
                try:
                    os.unlink(part)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"cache fail {vid}: {e}")
            try:
                if os.path.isfile(part):
                    os.unlink(part)
            except: pass

    def cleanup(self, hours=24, max_bytes=500 * 1024 * 1024):
        """Borra lo viejo (>hours) y, si el cache pasa de max_bytes, lo mas
        antiguo primero (LRU). Antes no habia tope: el cache crecia sin fin."""
        now = time.time()
        files = []
        try:
            names = os.listdir(CACHE)
        except OSError:
            return
        for f in names:
            p = os.path.join(CACHE, f)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            if (now - st.st_mtime) / 3600 > hours:
                try:
                    os.unlink(p)
                except OSError:
                    pass
                continue
            files.append((st.st_mtime, st.st_size, p))
        total = sum(s for _, s, _ in files)
        for _, s, p in sorted(files):
            if total <= max_bytes:
                break
            try:
                os.unlink(p)
                total -= s
            except OSError:
                pass
