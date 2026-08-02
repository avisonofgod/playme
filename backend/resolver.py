"""
Resolver: busca en YouTube y obtiene URLs de audio via yt-dlp.
"""
import json, logging, os, subprocess, threading, shutil, tempfile

logger = logging.getLogger(__name__)
COOKIES_MASTER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies_master.txt")
COOKIES_TEMP = os.path.join(tempfile.gettempdir(), "playme_cookies.txt")

class Resolver:
    def __init__(self):
        self.ytdlp = "yt-dlp"
        self._sem = threading.Semaphore(1)  # solo un yt-dlp a la vez
        self._sync_cookies()  # copia inicial

    def _sync_cookies(self):
        """Copia cookies_master a temp para que yt-dlp no sobrescriba el original."""
        if os.path.isfile(COOKIES_MASTER) and os.path.getsize(COOKIES_MASTER) > 0:
            shutil.copy2(COOKIES_MASTER, COOKIES_TEMP)

    def _args(self, extra=None):
        self._sync_cookies()  # asegura copia fresca antes de cada comando
        a = [self.ytdlp]
        if os.path.isfile(COOKIES_TEMP) and os.path.getsize(COOKIES_TEMP) > 0:
            a += ["--cookies", COOKIES_TEMP]
        if extra:
            a += extra
        return a

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
        """Obtiene URL directa de audio. Prueba 3 extractors."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        strategies = [
            {"f": "251/bestaudio", "e": "default"},
            {"f": "251/bestaudio", "e": "youtube:player_client=android_creativecommons"},
            {"f": "bestaudio", "e": "youtube:player_client=web_creativecommons"},
        ]
        for s in strategies:
            try:
                args = self._args(["--get-url", "--format", s["f"]])
                if s["e"] != "default":
                    args += ["--extractor-args", s["e"]]
                args.append(url)
                out = subprocess.check_output(args, stderr=subprocess.DEVNULL, timeout=10).decode().strip()
                if out:
                    line = out.split("\n")[-1]
                    if line.startswith("http"):
                        return line
            except:
                continue
        return None
