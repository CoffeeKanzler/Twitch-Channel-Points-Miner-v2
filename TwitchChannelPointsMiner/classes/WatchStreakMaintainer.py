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
        stream = streamer.stream
        # Verbose decision trace (DEBUG -> captured in the log file) so we can
        # see exactly why a streamer was or wasn't queued after going offline.
        logger.debug(
            f"[streak-recovery] evaluate {streamer}: "
            f"watch_streak={settings.watch_streak} "
            f"vod_recovery={settings.watch_streak_vod_recovery} "
            f"watch_streak_missing={getattr(stream, 'watch_streak_missing', None)} "
            f"minute_watched={getattr(stream, 'minute_watched', None)} "
            f"threshold={self.streak_watch_minutes}"
        )
        if not settings.watch_streak or not settings.watch_streak_vod_recovery:
            logger.debug(f"[streak-recovery] skip {streamer}: recovery not enabled")
            return False
        if stream is None or stream.watch_streak_missing is not True:
            logger.debug(
                f"[streak-recovery] skip {streamer}: streak already earned/started this stream"
            )
            return False
        if stream.minute_watched >= self.streak_watch_minutes:
            logger.debug(
                f"[streak-recovery] skip {streamer}: already watched "
                f"{stream.minute_watched}m live (>= {self.streak_watch_minutes})"
            )
            return False

        with self._lock:
            if streamer.channel_id in self._queued:
                logger.debug(f"[streak-recovery] skip {streamer}: already queued")
                return False
            self._queued.add(streamer.channel_id)
        self.queue.put(streamer)
        logger.info(
            f"[streak-recovery] Queued {streamer} for VOD watch-streak recovery "
            f"(missed live streak; queue size now {self.queue.qsize()})"
        )
        return True

    def _recover(self, streamer):
        try:
            logger.info(f"[streak-recovery] Processing {streamer}: looking up recent VOD")
            vod_id = self.twitch.get_recent_vod_id(streamer)
            if vod_id is None:
                logger.info(
                    f"[streak-recovery] No VOD available for {streamer}; cannot recover streak"
                )
                return
            logger.info(f"[streak-recovery] Recovering {streamer} via VOD {vod_id}")
            self.twitch.watch_vod_for_streak(
                streamer, vod_id, max_minutes=self.streak_watch_minutes + 1
            )
            logger.info(
                f"[streak-recovery] Finished VOD watch for {streamer}; watch the next "
                "minute for a PubSub points-earned/milestone message to confirm the save"
            )
        finally:
            with self._lock:
                self._queued.discard(streamer.channel_id)

    def run(self):
        while self.running:
            try:
                streamer = self.queue.get(timeout=1)
            except Empty:
                continue
            try:
                self._recover(streamer)
            except Exception:
                logger.error(
                    "Exception in WatchStreakMaintainer", exc_info=True
                )

    def stop(self):
        self.running = False
