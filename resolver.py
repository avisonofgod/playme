import subprocess

class Resolver:
    def __init__(self):
        self.ytdlp = "yt-dlp"
        self.audio_fmt = "bestaudio"
        self.video_fmt = "bestvideo+bestaudio"

    def set_quality(self, audio_fmt=None, video_fmt=None):
        if audio_fmt:
            self.audio_fmt = audio_fmt
        if video_fmt:
            self.video_fmt = video_fmt

    def resolve(self, query: str, video: bool):
        fmt = self.video_fmt if video else self.audio_fmt

        try:
            output = subprocess.check_output(
                [
                    self.ytdlp,
                    "-f", fmt,
                    "-g",
                    f"ytsearch1:{query}"
                ],
                stderr=subprocess.DEVNULL
            ).decode().strip()

        except subprocess.CalledProcessError:
            return None, None
        except FileNotFoundError:
            return None, None

        lines = output.split("\n")

        if video:
            if len(lines) >= 2:
                return lines[0], lines[1]
            return None, None

        return lines[0], None
