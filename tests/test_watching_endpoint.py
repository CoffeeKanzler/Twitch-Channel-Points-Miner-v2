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


def make_server(currently_watching, streamers=None):
    """Build an AnalyticsServer with asset-check patched out."""
    import TwitchChannelPointsMiner.classes.AnalyticsServer as _mod
    with mock.patch.object(_mod, "check_assets"):
        server = AnalyticsServer(
            host="127.0.0.1",
            port=5099,
            currently_watching=currently_watching,
            streamers=streamers if streamers is not None else [],
        )
    return server


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
        """AnalyticsServer without streamers= should still work (no crash)."""
        import TwitchChannelPointsMiner.classes.AnalyticsServer as _mod
        with mock.patch.object(_mod, "check_assets"):
            server = AnalyticsServer(
                host="127.0.0.1",
                port=5098,
                currently_watching=["streamer_a"],
            )
        data = json.loads(server.app.test_client().get("/watching").data)
        assert data["online"] == []
        assert data["watching"] == ["streamer_a"]
