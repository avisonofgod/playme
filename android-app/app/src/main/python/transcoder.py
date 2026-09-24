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
                res = resolver._run(a2, timeout=180, cancel=ev)
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
