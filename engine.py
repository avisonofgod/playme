import subprocess
import signal
import threading
import logging

logger = logging.getLogger(__name__)

class MPVEngine:
    
    def __init__(self):
        self.process = None
        self.on_finish = None
        logger.info("MPVEngine initialized - audio only, no IPC")

    def set_on_finish(self, callback):
        self.on_finish = callback

    def _monitor_playback(self):
        """Monitor when mpv finishes playing"""
        if not self.process:
            return
        self.process.wait()  # Wait for mpv to exit
        logger.info("Playback finished, triggering next")
        if self.on_finish:
            self.on_finish()

    # ---------- PUBLIC API ----------
    def play(self, youtube_url):
        """Play YouTube URL (mpv will use built-in ytdl to stream audio)"""
        logger.info(f"Playing YouTube URL with mpv")
        self.stop()

        args = [
            "mpv",
            "--no-video",
            "--ytdl-format=bestaudio",
            youtube_url
        ]
        logger.debug(f"YouTube URL: {youtube_url[:80]}...")

        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info(f"mpv process started with PID: {self.process.pid}")

            # Start monitor thread for when playback ends
            monitor = threading.Thread(target=self._monitor_playback, daemon=True)
            monitor.start()

        except FileNotFoundError:
            logger.error("mpv not found in system PATH")
            self.process = None
        except Exception as e:
            logger.exception(f"Failed to start mpv: {e}")
            self.process = None

    def stop(self):
        if self.process and self.process.poll() is None:
            logger.info("Stopping mpv process")
            self.process.terminate()
            self.process.wait(timeout=5)
        else:
            logger.debug("No running mpv process to stop")
        self.process = None

    def set_pause(self, pause: bool):
        if not self.process or self.process.poll() is not None:
            logger.warning("Cannot set pause, mpv not running")
            return
        logger.info(f"Setting pause: {pause}")
        if pause:
            self.process.send_signal(signal.SIGSTOP)  # Stop process
        else:
            self.process.send_signal(signal.SIGCONT)  # Continue process
