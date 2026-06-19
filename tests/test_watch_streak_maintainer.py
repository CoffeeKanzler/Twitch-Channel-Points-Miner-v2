import unittest.mock as mock

from TwitchChannelPointsMiner.classes.entities.Streamer import StreamerSettings


def test_streamer_settings_has_vod_recovery_default_off():
    s = StreamerSettings()
    s.default()
    assert s.watch_streak_vod_recovery is False


from TwitchChannelPointsMiner.classes.WatchStreakMaintainer import WatchStreakMaintainer


def _streamer(channel_id="1", watch_streak=True, vod_recovery=True,
              missing=True, minute_watched=0.0):
    s = mock.MagicMock()
    s.channel_id = channel_id
    s.settings.watch_streak = watch_streak
    s.settings.watch_streak_vod_recovery = vod_recovery
    s.stream.watch_streak_missing = missing
    s.stream.minute_watched = minute_watched
    return s


def _maintainer():
    return WatchStreakMaintainer(twitch=mock.MagicMock(), streak_watch_minutes=7)


def test_enqueue_missed_streak():
    m = _maintainer()
    assert m.maybe_enqueue(_streamer()) is True
    assert m.queue.qsize() == 1


def test_skip_when_recovery_disabled():
    m = _maintainer()
    assert m.maybe_enqueue(_streamer(vod_recovery=False)) is False
    assert m.queue.qsize() == 0


def test_skip_when_watch_streak_disabled():
    m = _maintainer()
    assert m.maybe_enqueue(_streamer(watch_streak=False)) is False


def test_skip_when_streak_already_earned():
    m = _maintainer()
    assert m.maybe_enqueue(_streamer(missing=False)) is False


def test_skip_when_already_watched_enough():
    m = _maintainer()
    assert m.maybe_enqueue(_streamer(minute_watched=7.0)) is False


def test_dedup_same_channel():
    m = _maintainer()
    assert m.maybe_enqueue(_streamer(channel_id="9")) is True
    assert m.maybe_enqueue(_streamer(channel_id="9")) is False
    assert m.queue.qsize() == 1


from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer
from TwitchChannelPointsMiner.classes.Settings import Settings

# Settings.logger must be initialised before any Streamer.__str__ call (used
# inside set_offline's logger.info).
_mock_logger = mock.MagicMock()
_mock_logger.less = False
Settings.logger = _mock_logger


def test_set_offline_enqueues_when_online():
    s = Streamer("teststreamer")
    s.channel_id = "5"
    s.is_online = True
    s.settings.watch_streak = True
    s.settings.watch_streak_vod_recovery = True
    s.stream.watch_streak_missing = True
    s.stream.minute_watched = 0.0
    maint = mock.MagicMock()
    s.watch_streak_maintainer = maint
    s.set_offline()
    maint.maybe_enqueue.assert_called_once_with(s)


def test_set_offline_no_enqueue_when_already_offline():
    s = Streamer("teststreamer")
    s.is_online = False
    maint = mock.MagicMock()
    s.watch_streak_maintainer = maint
    s.set_offline()
    maint.maybe_enqueue.assert_not_called()


def test_set_offline_without_maintainer_does_not_crash():
    s = Streamer("teststreamer")
    s.is_online = True
    s.set_offline()  # watch_streak_maintainer defaults to None
