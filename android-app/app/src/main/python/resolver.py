"""
Resolver: busca en YouTube y obtiene URLs de audio via yt-dlp.
Refactor: usa runner.run_command (inyectable) en lugar de subprocess directo,
permitiendo tests sin red ni YouTube.
"""
import json, logging, os, shutil, sys, tempfile, threading

from runner import run_command

logger = logging.getLogger(__name__)
# cookies.txt = fuente viva (TokenManager la mantiene con SAPISID).
# cookies_master.txt = respaldo (exportacion completa de navegador).
# PLAYME_COOKIES_FILE permita reubicarlos en Android (filesDir).
COOKIES_LIVE = os.environ.get("PLAYME_COOKIES_FILE") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")
COOKIES_BACKUP = os.environ.get("PLAYME_COOKIES_BACKUP") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies_master.txt")
COOKIES_TEMP = os.environ.get("PLAYME_COOKIES_TEMP") or os.path.join(tempfile.gettempdir(), "playme_cookies.txt")


class Resolver:
    def __init__(self, runner=None):
        # En Android no hay binario yt-dlp: se invoca como modulo de Python.
        if os.environ.get("PLAYME_YTDLP_PYMOD") == "1":
            self.ytdlp = [sys.executable, "-m", "yt_dlp"]
        else:
            self.ytdlp = "yt-dlp"
        # comando normalizado a lista (en Android yt-dlp es un modulo de Python)
        self.ytdlp_cmd = self.ytdlp if isinstance(self.ytdlp, list) else [self.ytdlp]
        self._sem = threading.Semaphore(1)  # solo un yt-dlp a la vez
        self.last_error = None  # causa real del ultimo fallo (para exponerla en /api/play)
        self._cookie_lock = threading.Lock()  # protege _sync_cookies de acceso concurrente
        self._runner = runner or run_command  # inyectable para tests
        self._sync_cookies()  # copia inicial

    def _run(self, args, timeout=None, check=False):
        """Ejecuta un comando via runner. Retorna CompletedProcess con .returncode,
        .stdout, .stderr. Lanza si runner lo permite y check=True."""
        return self._runner(args, timeout=timeout, check=check, capture_output=True)

    def _sync_cookies(self):
        """Copia cookies viva a temp para que yt-dlp no sobrescriba el original.
        Usa lock para evitar race si varios hilos (stream + conv) copian a la vez.
        Si TEMP y LIVE son el mismo archivo (tests), evita SameFileError."""
        with self._cookie_lock:
            src = COOKIES_LIVE
            if not (os.path.isfile(src) and os.path.getsize(src) > 0):
                src = COOKIES_BACKUP
            dst = COOKIES_TEMP
            if os.path.isfile(src) and os.path.getsize(src) > 0:
                if os.path.abspath(src) != os.path.abspath(dst):
                    try:
                        shutil.copy2(src, dst)
                    except OSError:
                        # Android/SELinux: copy2 falla al copiar xattrs; copiamos solo datos
                        shutil.copyfile(src, dst)
                # Reparacion: yt-dlp exige la cabecera Netscape; si el archivo de origen
                # viene sin ella (p.ej. capturado del WebView por una version previa de la
                # app), se anade aqui para que yt-dlp lo acepte.
                try:
                    with open(dst, "rb") as f:
                        head = f.read(64)
                    if not (head.startswith(b"# Netscape") or head.startswith(b"# HTTP Cookie")):
                        with open(dst, "rb") as f:
                            body = f.read()
                        with open(dst, "wb") as f:
                            f.write(b"# Netscape HTTP Cookie File\n")
                            f.write(body)
                        logger.warning("cookies: cabecera Netscape anadida a %s", dst)
                except Exception as e:
                    logger.warning("cookies: no se pudo verificar cabecera: %s", e)

    def _cookies_valid(self):
        """True si COOKIES_TEMP existe y tiene formato Netscape reconocible."""
        try:
            with open(COOKIES_TEMP, "rb") as f:
                head = f.read(64)
            return head.startswith(b"# Netscape") or head.startswith(b"# HTTP Cookie")
        except Exception:
            return False

    def _args(self, extra=None):
        self._sync_cookies()  # asegura copia fresca antes de cada comando
        a = list(self.ytdlp_cmd)
        if self._cookies_valid() and os.path.getsize(COOKIES_TEMP) > 0:
            a += ["--cookies", COOKIES_TEMP]
        else:
            logger.warning("cookies: archivo no valido, se omite --cookies")
        if extra:
            a += extra
        return a

    def _err_tail(self, err_bytes):
        """Ultima linea util del stderr de yt-dlp (sin WARNINGs)."""
        try:
            if isinstance(err_bytes, bytes):
                err_bytes = err_bytes.decode(errors="replace")
            lines = [l for l in err_bytes.splitlines() if l.strip()]
            for l in reversed(lines):
                if l.startswith("ERROR"):
                    return l
            return lines[-1] if lines else ""
        except Exception:
            return ""

    def search(self, query, limit=10, tipo="video"):
        """Busca en YouTube. tipo: video (default), channel, playlist."""
        try:
            if tipo == "playlist":
                if query.startswith("PL") or query.startswith("RD") or len(query) == 34:
                    try:
                        r = self._run(
                            self._args(["--flat-playlist", "-J", f"https://www.youtube.com/playlist?list={query}"]),
                            timeout=20, check=True,
                        )
                        data = json.loads(r.stdout)
                        return [{
                            "id": e.get("id", ""),
                            "title": e.get("title", "?"),
                            "duration": e.get("duration", 0),
                            "uploader": e.get("uploader", ""),
                            "thumbnail": e.get("thumbnail", "")
                        } for e in data.get("entries", [])]
                    except Exception as e:
                        logger.warning(f"playlist direct fail {query}: {e}")
                        query = query + " playlist"
                search_query = f"ytsearch{limit}:{query}"
            else:
                search_query = f"ytsearch{limit}:{query}"

            r = self._run(
                self._args(["--flat-playlist", "-J", search_query]),
                timeout=20, check=True,
            )
            data = json.loads(r.stdout)
            return [{
                "id": e.get("id", ""),
                "title": e.get("title", "?"),
                "duration": e.get("duration", 0),
                "uploader": e.get("uploader", ""),
                "thumbnail": e.get("thumbnail", "")
            } for e in data.get("entries", [])]
        except Exception as e:
            logger.warning(f"search error: {e}")
            return []

    def get_info(self, video_id):
        """Obtiene metadata completa. Intenta primero full -J (duration/thumbnail),
        y si falla, degrada a flat."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            r = self._run(self._args(["-J", "--format", "bestaudio/best", url]), timeout=8, check=True)
            return json.loads(r.stdout)
        except Exception:
            pass
        try:
            r = self._run(self._args(["--flat-playlist", "-J", url]), timeout=6, check=True)
            return json.loads(r.stdout)
        except Exception:
            return None

    def get_stream_url(self, video_id):
        """Obtiene URL directa de audio. Prueba varios clientes, loggea la causa real."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        self.last_error = None
        strategies = [
            {"f": "251/bestaudio/best", "e": None},
            {"f": "251/bestaudio/best", "e": "youtube:player_client=tv"},
            {"f": "251/bestaudio/best", "e": "youtube:player_client=web_embedded"},
            {"f": "bestaudio/best", "e": "youtube:player_client=mweb"},
            {"f": "bestaudio/best", "e": "youtube:player_client=ios"},
        ]
        for s in strategies:
            try:
                args = self._args(["--get-url", "--format", s["f"]])
                if s["e"]:
                    args += ["--extractor-args", s["e"]]
                args.append(url)
                r = self._run(args, timeout=10)
                if r.returncode == 0:
                    out = (r.stdout or b"").decode(errors="replace").strip()
                    if out:
                        line = out.splitlines()[-1]
                        if line.startswith("http"):
                            self.last_error = None
                            return line
                err = self._err_tail(r.stderr)
                if err:
                    self.last_error = err
            except Exception as e:
                self.last_error = str(e)
        logger.warning(f"no stream for {video_id}: {self.last_error}")
        return None
