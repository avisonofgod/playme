import threading
import logging
from resolver import Resolver
from engine import MPVEngine

logger = logging.getLogger(__name__)

class Player:
    def __init__(self):
        self.resolver = Resolver()
        self.engine = MPVEngine()

        self.playing = False
        self.paused = False

        self.on_play = None
        self.on_error = None
        self.on_queue_update = None

        self.queue = []
        self.current_index = -1

        logger.info("Player initialized - audio only mode")

    def get_queue(self):
        return self.queue

    def _resolve_and_play(self, query):
        logger.info(f"Resolving audio query: {query}")
        youtube_url = self.resolver.resolve(query)

        if not youtube_url:
            logger.error(f"Failed to resolve: {query}")
            if self.on_error:
                self.on_error("No results found or yt-dlp failed")
            return

        logger.info(f"YouTube URL resolved: {youtube_url[:50]}...")
        logger.info(f"Starting playback...")
        self.engine.play(youtube_url)

        self.playing = True
        self.paused = False
        if self.on_play:
            self.on_play()

        if query not in self.queue:
            self.queue.append(query)
            logger.debug(f"Queue updated, {len(self.queue)} items")
            if self.on_queue_update:
                self.on_queue_update()

    def play(self, query: str):
        self.queue = [query]
        self.current_index = 0

        # Play FIRST song IMMEDIATELY (don't wait for related)
        logger.info(f"Playing first result immediately: {query}")
        self.engine.set_on_finish(self.play_next)
        self._resolve_and_play(query)

        # Search related music in BACKGROUND thread (non-blocking)
        def search_background():
            logger.info("Searching related music in background...")
            similar = self.resolver.search_related(query, limit=5)
            if similar:
                self.queue.extend([q for q in similar if q not in self.queue])
                logger.info(f"Queue updated with {len(self.queue)} music items")
                if self.on_queue_update:
                    self.on_queue_update()

        threading.Thread(target=search_background, daemon=True).start()

    def play_next(self):
        if self.current_index < len(self.queue) - 1:
            self.current_index += 1
            next_query = self.queue[self.current_index]
            logger.info(f"Playing next in queue: {next_query}")
            self._resolve_and_play(next_query)
        else:
            logger.info("End of queue reached")
            self.playing = False
            if self.on_queue_update:
                self.on_queue_update()

    def stop(self):
        logger.info("User pressed stop")
        self.engine.stop()
        self.playing = False
        self.paused = False
        self.queue = []
        self.current_index = -1
        if self.on_queue_update:
            self.on_queue_update()

    def toggle_pause(self):
        if not self.playing:
            return
        self.paused = not self.paused
        self.engine.set_pause(self.paused)
