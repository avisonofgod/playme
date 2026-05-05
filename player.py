import threading
from resolver import Resolver
from engine import MPVEngine


class Player:
    def __init__(self):
        self.resolver = Resolver()
        self.engine = MPVEngine()

        self.playing = False
        self.paused = False
        self.video = False

        self.on_play = None
        self.on_error = None

        self.history = []
        self.max_history = 20

    def get_history(self):
        return self.history

    def _resolve_and_play(self, query):
        video_url, audio_url = self.resolver.resolve(query, self.video)

        if not video_url:
            if self.on_error:
                self.on_error("No results found or yt-dlp failed")
            return

        self.engine.play(video_url, audio_url, self.video)

        self.playing = True
        self.paused = False
        if self.on_play:
            self.on_play()

        if query not in self.history:
            self.history.append(query)
            if len(self.history) > self.max_history:
                self.history.pop(0)
    # ---------- STATE QUERIES ----------
    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def video_enabled(self):
        return self.video

    # ---------- CONTROL ----------
    def toggle_video(self):
        self.video = not self.video

    def play(self, query: str):
        if not query:
            return

        threading.Thread(
            target=self._resolve_and_play,
            args=(query,),
            daemon=True
        ).start()
        

    def _resolve_and_play(self, query):
        video_url, audio_url = self.resolver.resolve(query, self.video)

        if not video_url:
            if self.on_error:
                self.on_error("No results found or yt-dlp failed")
            return

        self.engine.play(video_url, audio_url, self.video)

        self.playing = True
        self.paused = False
        if self.on_play:
            self.on_play()

    def stop(self):
        self.engine.stop()
        self.playing = False
        self.paused = False

    def toggle_pause(self):
        if not self.playing:
            return

        self.paused = not self.paused
        self.engine.set_pause(self.paused)