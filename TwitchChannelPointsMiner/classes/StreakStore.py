import logging
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)


class StreakStore:
    """SQLite-backed append-only log of watch-streak events for the /streaks
    dashboard. Survives restarts (unlike the in-memory maintainer history).

    This intentionally stores only *events that happened* (a log), never the
    live per-stream `watch_streak_missing` flag — that flag is correctly
    ephemeral (reset on every set_online) and persisting it would risk a stale
    "earned" surviving into a new stream.

    Event types:
      earned     - live WATCH_STREAK points event received
      missed     - streamer went offline before earning -> queued for recovery
      recovering - VOD recovery started
      sent       - VOD watch events sent (Twitch confirms via a separate
                   milestone, so this is "attempted")
      recovered  - recovery confirmed (normal streak event arrived)
      no_vod     - no VOD available to recover with
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS streak_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts REAL NOT NULL, "
            "channel TEXT NOT NULL, "
            "event_type TEXT NOT NULL, "
            "detail TEXT DEFAULT '')"
        )
        self._conn.commit()

    def record(self, channel, event_type, detail=""):
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO streak_events (ts, channel, event_type, detail) "
                    "VALUES (?, ?, ?, ?)",
                    (time.time(), channel, event_type, detail),
                )
                self._conn.commit()
        except Exception:
            logger.error("Failed to record streak event", exc_info=True)

    def recent(self, limit=50):
        """Return the most recent events, newest first, shaped for the feed."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, channel, event_type, detail FROM streak_events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"ts": r[0], "username": r[1], "status": r[2], "detail": r[3]}
            for r in rows
        ]

    def tally(self, since_ts):
        """Aggregate counts since `since_ts` (epoch seconds)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_type, COUNT(*) FROM streak_events "
                "WHERE ts >= ? GROUP BY event_type",
                (since_ts,),
            ).fetchall()
        counts = {r[0]: r[1] for r in rows}
        return {
            "earned": counts.get("earned", 0),
            "missed": counts.get("missed", 0),
            # a recovery "counts" once it actually sent events or was confirmed
            "recovered": counts.get("recovered", 0) + counts.get("sent", 0),
        }

    def close(self):
        with self._lock:
            self._conn.close()
