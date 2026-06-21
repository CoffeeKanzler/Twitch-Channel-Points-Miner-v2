"""
Tests for the /watching endpoint.

Business rules:
- Twitch limits watching to max 2 channels simultaneously
- There can be MORE than 2 channels online at once
- The endpoint must expose:
  - 'watching': the ≤2 channels currently being actively watched
  - 'online': ALL channels that are currently live (may exceed 2)
  - backward-compat fields: count, channels, channels_str
"""

import json
import pathlib
import unittest.mock as mock

import pytest

from TwitchChannelPointsMiner.classes.AnalyticsServer import AnalyticsServer
from TwitchChannelPointsMiner.classes.Settings import Settings

Settings.analytics_path = "/tmp/analytics_test"
pathlib.Path(Settings.analytics_path).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_streamer(username, is_online):
    s = mock.MagicMock()
    s.username = username
    s.is_online = is_online
    return s


def make_server(currently_watching, streamers=None, watch_streak_maintainer=None):
    """Build an AnalyticsServer (Flask app only; run() is never called)."""
    server = AnalyticsServer(
        host="127.0.0.1",
        port=5099,
        currently_watching=currently_watching,
        all_streamers=streamers if streamers is not None else [],
        watch_streak_maintainer=watch_streak_maintainer,
    )
    return server


def make_streak_streamer(username, is_online, watch_streak_missing=True,
                         minute_watched=0.0, vod_recovery=True, watch_streak=True):
    """Streamer stub with the concrete (JSON-serializable) fields /streaks reads."""
    s = mock.MagicMock()
    s.username = username
    s.is_online = is_online
    s.settings.watch_streak = watch_streak
    s.settings.watch_streak_vod_recovery = vod_recovery
    s.stream.watch_streak_missing = watch_streak_missing
    s.stream.minute_watched = minute_watched
    return s


class TestStreaksEndpoint:
    def test_streaks_data_status_codes(self):
        streamers = [
            make_streak_streamer("live_earned", True, watch_streak_missing=False, minute_watched=7.2),
            make_streak_streamer("live_watched", True, watch_streak_missing=True, minute_watched=7.0),
            make_streak_streamer("live_waiting", True, watch_streak_missing=True, minute_watched=1.0),
            make_streak_streamer("live_earning", True, watch_streak_missing=True, minute_watched=2.7),
            make_streak_streamer("off", False),
        ]
        # live_earning is in the 2 active watch slots
        data = json.loads(
            make_server(["live_earning"], streamers).app.test_client().get("/streaks/data").data
        )
        by = {r["username"]: r for r in data["streamers"]}
        assert by["live_earned"]["status"] == "earned"
        # Watched >= 7 min but no event -> "watched", NOT stuck on "waiting"
        assert by["live_watched"]["status"] == "watched"
        assert by["live_waiting"]["status"] == "waiting"
        assert by["live_earning"]["status"] == "earning"
        assert by["live_earning"]["watching"] is True
        assert by["off"]["status"] == "offline"
        assert by["live_earned"]["minute_watched"] == 7.2
        assert by["live_waiting"]["vod_recovery"] is True

    def test_streaks_data_activity_empty_without_maintainer(self):
        data = json.loads(
            make_server([], []).app.test_client().get("/streaks/data").data
        )
        assert data["activity"] == []

    def test_streaks_data_activity_from_maintainer(self):
        maint = mock.MagicMock()
        maint.recovery_history.return_value = [
            {"ts": 1.0, "username": "agurin", "status": "sent", "detail": "VOD 1 - sent 8/8"}
        ]
        server = make_server([], [], watch_streak_maintainer=maint)
        data = json.loads(server.app.test_client().get("/streaks/data").data)
        assert data["activity"][0]["username"] == "agurin"
        assert data["activity"][0]["status"] == "sent"

    def test_streaks_page_renders(self):
        assert make_server([], []).app.test_client().get("/streaks").status_code == 200


# ---------------------------------------------------------------------------
# /watching response structure tests
# ---------------------------------------------------------------------------

class TestWatchingEndpointStructure:
    """Response must always contain all required keys."""

    def test_returns_200(self):
        assert make_server([]).app.test_client().get("/watching").status_code == 200

    def test_has_watching_key(self):
        data = json.loads(make_server([]).app.test_client().get("/watching").data)
        assert "watching" in data

    def test_has_online_key(self):
        data = json.loads(make_server([]).app.test_client().get("/watching").data)
        assert "online" in data

    def test_has_count_online_key(self):
        data = json.loads(make_server([]).app.test_client().get("/watching").data)
        assert "count_online" in data

    def test_has_count_watching_key(self):
        data = json.loads(make_server([]).app.test_client().get("/watching").data)
        assert "count_watching" in data

    # backward-compat
    def test_has_count_key(self):
        data = json.loads(make_server([]).app.test_client().get("/watching").data)
        assert "count" in data

    def test_has_channels_key(self):
        data = json.loads(make_server([]).app.test_client().get("/watching").data)
        assert "channels" in data

    def test_has_channels_str_key(self):
        data = json.loads(make_server([]).app.test_client().get("/watching").data)
        assert "channels_str" in data


# ---------------------------------------------------------------------------
# Core logic: online vs watching
# ---------------------------------------------------------------------------

class TestWatchingVsOnline:
    """The critical fix: online must list ALL live streamers."""

    def test_more_than_two_online_all_appear_in_online(self):
        """4 streamers live, only 2 watched — online must expose all 4."""
        streamers = [make_streamer(n, True) for n in
                     ["streamer_a", "streamer_b", "streamer_c", "streamer_d"]]
        data = json.loads(
            make_server(["streamer_a", "streamer_b"], streamers)
            .app.test_client().get("/watching").data
        )
        assert set(data["online"]) == {"streamer_a", "streamer_b", "streamer_c", "streamer_d"}

    def test_more_than_two_online_watching_capped_at_two(self):
        streamers = [make_streamer(n, True) for n in
                     ["streamer_a", "streamer_b", "streamer_c", "streamer_d"]]
        data = json.loads(
            make_server(["streamer_a", "streamer_b"], streamers)
            .app.test_client().get("/watching").data
        )
        assert set(data["watching"]) == {"streamer_a", "streamer_b"}
        assert len(data["watching"]) <= 2

    def test_count_online_reflects_all_live_streamers(self):
        streamers = [
            make_streamer("streamer_a", True),
            make_streamer("streamer_b", True),
            make_streamer("streamer_c", True),
        ]
        data = json.loads(
            make_server(["streamer_a", "streamer_b"], streamers)
            .app.test_client().get("/watching").data
        )
        assert data["count_online"] == 3

    def test_count_watching_reflects_actively_watched(self):
        streamers = [
            make_streamer("streamer_a", True),
            make_streamer("streamer_b", True),
            make_streamer("streamer_c", True),
        ]
        data = json.loads(
            make_server(["streamer_a", "streamer_b"], streamers)
            .app.test_client().get("/watching").data
        )
        assert data["count_watching"] == 2

    def test_offline_streamers_not_in_online(self):
        streamers = [
            make_streamer("streamer_a", True),
            make_streamer("streamer_b", False),
            make_streamer("streamer_c", False),
        ]
        data = json.loads(
            make_server(["streamer_a"], streamers)
            .app.test_client().get("/watching").data
        )
        assert "streamer_b" not in data["online"]
        assert "streamer_c" not in data["online"]

    def test_only_live_streamer_appears_in_online(self):
        streamers = [
            make_streamer("streamer_a", True),
            make_streamer("streamer_b", False),
        ]
        data = json.loads(
            make_server(["streamer_a"], streamers)
            .app.test_client().get("/watching").data
        )
        assert data["online"] == ["streamer_a"]
        assert data["count_online"] == 1

    def test_no_streamers_online(self):
        streamers = [
            make_streamer("streamer_a", False),
            make_streamer("streamer_b", False),
        ]
        data = json.loads(
            make_server([], streamers)
            .app.test_client().get("/watching").data
        )
        assert data["online"] == []
        assert data["watching"] == []
        assert data["count_online"] == 0
        assert data["count_watching"] == 0

    def test_empty_streamers_list(self):
        data = json.loads(
            make_server([]).app.test_client().get("/watching").data
        )
        assert data["online"] == []
        assert data["watching"] == []


# ---------------------------------------------------------------------------
# Backward-compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Legacy fields must still work so existing integrations don't break."""

    def test_count_equals_count_watching(self):
        streamers = [
            make_streamer("streamer_a", True),
            make_streamer("streamer_b", True),
            make_streamer("streamer_c", True),
        ]
        data = json.loads(
            make_server(["streamer_a", "streamer_b"], streamers)
            .app.test_client().get("/watching").data
        )
        assert data["count"] == data["count_watching"]

    def test_channels_equals_watching(self):
        streamers = [make_streamer("streamer_a", True)]
        data = json.loads(
            make_server(["streamer_a"], streamers)
            .app.test_client().get("/watching").data
        )
        assert data["channels"] == data["watching"]

    def test_channels_str_when_watching(self):
        streamers = [
            make_streamer("streamer_a", True),
            make_streamer("streamer_b", True),
        ]
        data = json.loads(
            make_server(["streamer_a", "streamer_b"], streamers)
            .app.test_client().get("/watching").data
        )
        assert data["channels_str"] == "streamer_a, streamer_b"

    def test_channels_str_when_not_watching(self):
        data = json.loads(
            make_server([]).app.test_client().get("/watching").data
        )
        assert data["channels_str"] == "none"

    def test_no_streamers_kwarg_defaults_gracefully(self):
        """AnalyticsServer without all_streamers= should still work (no crash)."""
        server = AnalyticsServer(
            host="127.0.0.1",
            port=5098,
            currently_watching=["streamer_a"],
        )
        data = json.loads(server.app.test_client().get("/watching").data)
        assert data["online"] == []
        assert data["watching"] == ["streamer_a"]
