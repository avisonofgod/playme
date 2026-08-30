"""
Transcoder: descarga audio a cache con yt-dlp.
Eliminado: no usa mpv, no tiene stop() bloqueante.
Bugfix: usa archivo .part para descarga -> evita tratar parcial como cached.
Refactor: usa runner.run_command inyectable.
"""
import logging, os, threading, time

from runner import run_command

logger = logging.getLogger(__name__)
CACHE = "/tmp/playme_cache"

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
        args = resolver._args() + [
            "--format", "bestaudio[ext=webm]/bestaudio",
            "--output", part,
            "--no-part", "--no-mtime", url
        ]
        try:
            self._runner(args, timeout=120)
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

    def cleanup(self, hours=24):
        now = time.time()
        for f in os.listdir(CACHE):
            p = os.path.join(CACHE, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p)) / 3600 > hours:
                os.unlink(p)
