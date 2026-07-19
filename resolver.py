"""
Resolver: busca en YouTube y obtiene URLs de audio via yt-dlp.
"""
import json, logging, os, subprocess, threading

logger = logging.getLogger(__name__)
COOKIES = os.path.join(os.path.dirname(__file__), "cookies.txt")

class Resolver:
    def __init__(self):
        self.ytdlp = "yt-dlp"
        self._sem = threading.Semaphore(1)  # solo un yt-dlp a la vez

    def _args(self, extra=None):
        a = [self.ytdlp, "--remote-components", "ejs:github"]
        if os.path.isfile(COOKIES) and os.path.getsize(COOKIES) > 0:
            a += ["--cookies", COOKIES]
        if extra:
            a += extra
        return a

    def search(self, query, limit=10):
        try:
            out = subprocess.check_output(
                self._args(["--flat-playlist", "-J", f"ytsearch{limit}:{query}"]),
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
