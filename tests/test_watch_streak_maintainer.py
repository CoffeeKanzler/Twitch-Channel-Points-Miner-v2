import unittest.mock as mock

from TwitchChannelPointsMiner.classes.entities.Streamer import StreamerSettings


def test_streamer_settings_has_vod_recovery_default_off():
    s = StreamerSettings()
    s.default()
    assert s.watch_streak_vod_recovery is False
