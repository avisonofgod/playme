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

    def wait_partial(self, vid, timeout=25.0, min_bytes=262144):
        """Espera los PRIMEROS bytes del audio (o el final de la descarga).

        v1.3.0: el play ya no espera al 100%; con ~256 KB (unos segundos de
        audio) el <audio> puede empezar y el resto llega mientras suena."""
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
        threading.Thread(target=self._download_worker, args=(vid, resolver), daemon=True).start()

    def _download_worker(self, vid, resolver):
        try:
            self._download_sync(vid, resolver)
        finally:
            with self._active_lock:
                self._active.pop(vid, None)

    def _download_sync(self, vid, resolver):
        """Descarga a .part y renombra a final al completar.
        Asi, un archivo parcial (descarga abortada) NO se marca como cached."""
        if self.is_cached(vid):
            return
        url = f"https://www.youtube.com/watch?v={vid}"
        part = self._part(vid)
        args = resolver._args()
        base = [
            "--format", "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
            "--output", part,
            "--no-part", "--no-mtime", url
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
        try:
            rc = 1
            for base_args, client in attempts:
                a2 = base_args + (["--extractor-args", client] if client else []) + base
                res = resolver._run(a2, timeout=180)
                rc = getattr(res, "returncode", 0)
                if rc:
                    se = getattr(res, "stderr", b"") or b""
                    if isinstance(se, bytes): se = se.decode("utf-8", "replace")
                    logger.warning("dl rc=%s (%s): %s" % (rc, client, se.strip()[-200:]))
                if os.path.isfile(part) and os.path.getsize(part) > 0:
                    break
            if os.path.isfile(part) and os.path.getsize(part) > 0:
                os.rename(part, self.path(vid))
                logger.info(f"cache ok: {vid} ({self.size(vid)} bytes)")
            else:
                logger.warning(f"cache empty: {vid}")
                if os.path.isfile(part):
                    os.unlink(part)
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
