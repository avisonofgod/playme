import subprocess
import logging

logger = logging.getLogger(__name__)

class Resolver:
    def __init__(self):
        self.ytdlp = "yt-dlp"
        self.audio_fmt = "bestaudio"
        logger.info("Resolver initialized - audio only mode (no YTMusicAPI)")

    def set_quality(self, audio_fmt=None):
        if audio_fmt:
            self.audio_fmt = audio_fmt
            logger.info(f"Audio format set to: {audio_fmt}")

    def resolve(self, query: str):
        """Get YouTube watch URL (not media URL to avoid expiration)"""
        logger.debug(f"Resolving YouTube URL for: {query}")
        
        try:
            # Get video ID using yt-dlp
            output = subprocess.check_output(
                [
                    self.ytdlp,
                    "--get-id",
                    f"ytsearch1:{query} music"
                ],
                stderr=subprocess.DEVNULL
            ).decode().strip()

        except subprocess.CalledProcessError as e:
            logger.error(f"yt-dlp failed for query '{query}': {e}")
            return None
        except FileNotFoundError:
            logger.error("yt-dlp not found in system PATH")
            return None

        if output:
            video_id = output.split("\n")[0]
            url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"YouTube URL resolved: {url}")
            return url
        return None

    def search_related(self, query: str, limit: int = 5):
        """Search for related music using yt-dlp (no YouTube API)"""
        logger.info(f"Searching related music for: {query}")
        try:
            output = subprocess.check_output(
                [
                    self.ytdlp,
                    "--flat-playlist",
                    "-J",
                    f"ytsearch{limit+1}:{query} music similar"
                ],
                stderr=subprocess.DEVNULL
            ).decode().strip()

            import json
            data = json.loads(output)
            results = []
            if "entries" in data:
                for entry in data["entries"][1:]:  # Skip first (original)
                    if "title" in entry:
                        results.append(entry["title"])
            logger.info(f"Found {len(results)} related music results via yt-dlp")
            return results
        except Exception as e:
            logger.error(f"Failed to search related music: {e}")
            return []
