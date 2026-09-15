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

    def path(self, vid):
        return os.path.join(CACHE, f"{vid}.webm")

    def _part(self, vid):
        return self.path(vid) + ".part"

    def is_cached(self, vid):
        p = self.path(vid)
        return os.path.isfile(p) and os.path.getsize(p) > 0

    def size(self, vid):
        p = self.path(vid)
        return os.path.getsize(p) if os.path.isfile(p) else 0

    def download_bg(self, vid, resolver):
        """Descarga en background a .part y renombra a final al completar.
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
        # Intentos (verificado 2026-09): el cliente "ios" descarga sin runtime JS
        # ni PO token, pero yt-dlp lo DESCARTA si hay cookies ("does not support
        # cookies") -> primero sin cookies; android_vr da URL pero 403 al bajar.
        attempts = []
        if "youtube:player_client=ios" not in " ".join(args):
            attempts.append((_strip_cookies(args), "youtube:player_client=ios"))
            attempts.append((args, "youtube:player_client=android_vr"))
            attempts.append((_strip_cookies(args), "youtube:player_client=android_vr"))
        try:
            rc = 1
            for base_args, client in attempts:
                a2 = base_args + ["--extractor-args", client] + base
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
