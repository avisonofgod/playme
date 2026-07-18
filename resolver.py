"""
PlayMe - Resolver
yt-dlp wrapper: busca en YouTube y resuelve URLs de audio.
Soporta cookies para bypass de bloqueo CGNAT.
"""
import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# Usa el mismo cookies.txt que TokenManager
COOKIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cookies.txt")

class Resolver:
    def __init__(self):
        self.ytdlp = "yt-dlp"
        self.format = "bestaudio"
        logger.info("Resolver init")

    def _base_args(self):
        """Argumentos base incluyendo cookies y EJS challenge solver."""
        args = [self.ytdlp, "--remote-components", "ejs:github"]
        if os.path.isfile(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0:
            args.extend(["--cookies", COOKIES_PATH])
            logger.debug("Usando cookies.txt")
        return args

    def has_cookies(self):
        return os.path.isfile(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0

    def search(self, query: str, limit: int = 10):
        """Busca en YouTube, devuelve lista de resultados."""
        logger.info(f"Search: {query}")
        try:
            args = self._base_args() + [
                "--flat-playlist", "-J",
                f"ytsearch{limit}:{query}"
            ]
            out = subprocess.check_output(
                args, stderr=subprocess.DEVNULL, timeout=20
            ).decode()
            data = json.loads(out)
            results = []
            for e in data.get("entries", []):
                results.append({
                    "id": e.get("id", ""),
                    "title": e.get("title", "Sin título"),
                    "duration": e.get("duration", 0),
                    "uploader": e.get("uploader", ""),
                    "thumbnail": e.get("thumbnail", "")
                })
            return results
        except subprocess.TimeoutExpired:
            logger.error("Search timeout")
            return []
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

    def get_stream_url(self, video_id: str):
        """Obtiene URL directa de audio via yt-dlp --get-url.
        Prueba múltiples extractors para maximizar compatibilidad.
        Timeout rápido por estrategia."""
        url = f"https://www.youtube.com/watch?v={video_id}"

        strategies = [
            {"format": self.format, "extractor": "default"},
            {"format": self.format,
             "extractor": "youtube:player_client=android_creativecommons"},
            {"format": self.format,
             "extractor": "youtube:player_client=web_creativecommons"},
        ]

        for s in strategies:
            try:
                args = self._base_args() + [
                    "--get-url", "--format", s["format"]
                ]
                if s["extractor"] != "default":
                    args.extend(["--extractor-args", s["extractor"]])
                args.append(url)

                out = subprocess.check_output(
                    args, stderr=subprocess.DEVNULL, timeout=10
                ).decode().strip()
                if out:
                    lines = out.split("\n")
                    stream_url = lines[-1]
                    if stream_url.startswith("http"):
                        logger.info(f"Stream URL via {s['extractor']} "
                                   f"({len(stream_url)} chars)")
                        return stream_url
            except subprocess.TimeoutExpired:
                logger.warning(f"get_stream_url {s['extractor']}: timeout")
                continue
            except subprocess.CalledProcessError:
                continue
            except Exception as e:
                logger.warning(f"get_stream_url {s['extractor']}: {e}")
                continue

        logger.warning("get_stream_url: todas las estrategias fallaron")
        return None

    def get_info(self, video_id: str):
        """Obtiene metadata del video.
        Primero flat-playlist, luego extract completo."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            args = self._base_args() + ["--flat-playlist", "-J", url]
            out = subprocess.check_output(
                args, stderr=subprocess.DEVNULL, timeout=8
            ).decode()
            return json.loads(out)
        except Exception as e:
            logger.warning(f"get_info flat failed: {e}")

        try:
            args = self._base_args() + ["-J", "--format", self.format, url]
            out = subprocess.check_output(
                args, stderr=subprocess.DEVNULL, timeout=8
            ).decode()
            return json.loads(out)
        except Exception as e:
            logger.warning(f"get_info extract failed: {e}")
            return None
