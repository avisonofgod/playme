"""
Resolver: busca en YouTube y obtiene URLs de audio via yt-dlp.
"""
import json, logging, os, subprocess, threading, shutil, tempfile

logger = logging.getLogger(__name__)
# cookies.txt = fuente viva (TokenManager la mantiene con SAPISID).
# cookies_master.txt = respaldo (exportacion completa de navegador).
COOKIES_LIVE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")
COOKIES_BACKUP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies_master.txt")
COOKIES_TEMP = os.path.join(tempfile.gettempdir(), "playme_cookies.txt")

class Resolver:
    def __init__(self):
        self.ytdlp = "yt-dlp"
        self._sem = threading.Semaphore(1)  # solo un yt-dlp a la vez
        self.last_error = None  # causa real del ultimo fallo (para exponerla en /api/play)
        self._sync_cookies()  # copia inicial

    def _sync_cookies(self):
        """Copia cookies viva a temp para que yt-dlp no sobrescriba el original."""
        src = COOKIES_LIVE
        if not (os.path.isfile(src) and os.path.getsize(src) > 0):
            src = COOKIES_BACKUP
        if os.path.isfile(src) and os.path.getsize(src) > 0:
            shutil.copy2(src, COOKIES_TEMP)

    def _args(self, extra=None):
        self._sync_cookies()  # asegura copia fresca antes de cada comando
        a = [self.ytdlp]
        if os.path.isfile(COOKIES_TEMP) and os.path.getsize(COOKIES_TEMP) > 0:
            a += ["--cookies", COOKIES_TEMP]
        if extra:
            a += extra
        return a

    def _err_tail(self, err_bytes):
        """Ultima linea util del stderr de yt-dlp (sin WARNINGs)."""
        try:
            lines = [l for l in err_bytes.decode(errors="replace").splitlines() if l.strip()]
            for l in reversed(lines):
                if l.startswith("ERROR"):
                    return l
            return lines[-1] if lines else ""
        except Exception:
            return ""

    def search(self, query, limit=10, tipo="video"):
        """Busca en YouTube. tipo: video (default), channel, playlist"""
        try:
            if tipo == "channel":
                # Buscar canal y listar sus videos
                search_query = f"ytsearch{limit}:{query}"
            elif tipo == "playlist":
                # Si query es un ID de playlist, listarla directamente
                if query.startswith("PL") or query.startswith("RD") or len(query) == 34:
                    out = subprocess.check_output(
                        self._args(["--flat-playlist", "-J", f"https://www.youtube.com/playlist?list={query}"]),
                        stderr=subprocess.DEVNULL, timeout=20
                    ).decode()
                    data = json.loads(out)
                    return [{
                        "id": e.get("id", ""),
                        "title": e.get("title", "?"),
                        "duration": e.get("duration", 0),
                        "uploader": e.get("uploader", ""),
                        "thumbnail": e.get("thumbnail", "")
                    } for e in data.get("entries", [])]
                else:
                    # Buscar playlist por nombre
                    search_query = f"ytsearch{limit}:{query}"
            else:
                search_query = f"ytsearch{limit}:{query}"

            out = subprocess.check_output(
                self._args(["--flat-playlist", "-J", search_query]),
                stderr=subprocess.DEVNULL, timeout=20
            ).decode()
            data = json.loads(out)
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
        """Obtiene metadata. Timeout 8s total."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            out = subprocess.check_output(
                self._args(["--flat-playlist", "-J", url]),
                stderr=subprocess.DEVNULL, timeout=6
            ).decode()
            return json.loads(out)
        except:
            pass
        try:
            out = subprocess.check_output(
                self._args(["-J", "--format", "bestaudio", url]),
                stderr=subprocess.DEVNULL, timeout=6
            ).decode()
            return json.loads(out)
        except:
            return None

    def get_stream_url(self, video_id):
        """Obtiene URL directa de audio. Prueba varios clientes, loggea la causa real."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        self.last_error = None
        strategies = [
            {"f": "251/bestaudio", "e": None},  # default (ANDROID_VR en yt-dlp moderno)
            {"f": "251/bestaudio", "e": "youtube:player_client=tv"},
            {"f": "251/bestaudio", "e": "youtube:player_client=web_embedded"},
            {"f": "bestaudio", "e": "youtube:player_client=mweb"},
            {"f": "bestaudio", "e": "youtube:player_client=ios"},
        ]
        for s in strategies:
            try:
                args = self._args(["--get-url", "--format", s["f"]])
                if s["e"]:
                    args += ["--extractor-args", s["e"]]
                args.append(url)
                r = subprocess.run(args, capture_output=True, timeout=10)
                if r.returncode == 0:
                    out = r.stdout.decode(errors="replace").strip()
                    if out:
                        line = out.split("\n")[-1]
                        if line.startswith("http"):
                            self.last_error = None
                            return line
                # guardar la causa del fallo (ultima linea ERROR)
                err = self._err_tail(r.stderr)
                if err:
                    self.last_error = err
            except subprocess.TimeoutExpired:
                self.last_error = f"yt-dlp timeout (10s) para {video_id}"
            except Exception as e:
                self.last_error = str(e)
        # todas las estrategias fallaron: loggear la causa real para diagnostico
        logger.warning(f"no stream for {video_id}: {self.last_error}")
        return None
