import os
import time

from TwitchChannelPointsMiner.classes.StreakStore import StreakStore


def _store(tmp_path):
    return StreakStore(os.path.join(str(tmp_path), "streaks.sqlite3"))


def test_record_and_recent_newest_first(tmp_path):
    s = _store(tmp_path)
    s.record("agurin", "missed")
    s.record("agurin", "recovering", "VOD 42")
    s.record("agurin", "sent", "VOD 42 - sent 8/8")
    feed = s.recent()
    assert [e["status"] for e in feed] == ["sent", "recovering", "missed"]
    assert feed[0]["username"] == "agurin"
    assert feed[0]["detail"] == "VOD 42 - sent 8/8"
    s.close()


def test_recent_limit(tmp_path):
    s = _store(tmp_path)
    for i in range(10):
        s.record("foo", "earned", str(i))
    assert len(s.recent(limit=3)) == 3
    s.close()


def test_tally_counts_and_window(tmp_path):
    s = _store(tmp_path)
    s.record("a", "earned")
    s.record("b", "earned")
    s.record("c", "missed")
    s.record("c", "sent", "VOD 1")   # counts toward recovered
    s.record("d", "recovered")
    t = s.tally(since_ts=0)
    assert t["earned"] == 2
    assert t["missed"] == 1
    assert t["recovered"] == 2  # sent + recovered
    # window excludes old rows
    future = time.time() + 60
    empty = s.tally(since_ts=future)
    assert empty == {"earned": 0, "missed": 0, "recovered": 0}
    s.close()


def test_persists_across_reopen(tmp_path):
    path = os.path.join(str(tmp_path), "streaks.sqlite3")
    s1 = StreakStore(path)
    s1.record("agurin", "earned", "+300")
    s1.close()
    s2 = StreakStore(path)  # simulate a restart: same file, new connection
    feed = s2.recent()
    assert len(feed) == 1
    assert feed[0]["username"] == "agurin"
    assert s2.tally(since_ts=0)["earned"] == 1
    s2.close()
