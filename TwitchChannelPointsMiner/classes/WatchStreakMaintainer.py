import logging
import threading
from queue import Empty, Queue

logger = logging.getLogger(__name__)


class WatchStreakMaintainer(threading.Thread):
    """Recovers missed watch streaks by watching a recent VOD of the channel.

    Detection (`maybe_enqueue`) runs on the offline transition in the main
    thread; recovery runs here, one streamer at a time, so it never competes
    with the live watch slots.
    """

    def __init__(self, twitch, streak_watch_minutes=7):
        super().__init__()
        self.name = "Watch streak maintainer"
        self.daemon = True
        self.twitch = twitch
        self.streak_watch_minutes = streak_watch_minutes
        self.running = True
        self.queue = Queue()
        self._queued = set()
        self._lock = threading.Lock()

    def maybe_enqueue(self, streamer) -> bool:
        """Queue the streamer for VOD recovery if its streak was missed.

        Returns True if it was queued, False otherwise. Safe to call on every
        online->offline transition.
        """
        settings = streamer.settings
        if not settings.watch_streak or not settings.watch_streak_vod_recovery:
            return False
        stream = streamer.stream
        if stream is None or stream.watch_streak_missing is not True:
            return False
        if stream.minute_watched >= self.streak_watch_minutes:
            return False

        with self._lock:
            if streamer.channel_id in self._queued:
                return False
            self._queued.add(streamer.channel_id)
        self.queue.put(streamer)
        logger.info(f"Queued {streamer} for VOD watch-streak recovery")
        return True
