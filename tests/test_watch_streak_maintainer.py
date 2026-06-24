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
from TwitchChannelPointsMiner.classes.Chat import ChatPresence

# Settings.logger must be initialised before any Streamer.__str__ call (used
# inside set_offline's logger.info).
_mock_logger = mock.MagicMock()
_mock_logger.less = False
Settings.logger = _mock_logger


def _offline_test_streamer():
    """A Streamer with explicit settings; chat=NEVER so set_offline's
    toggle_chat short-circuits and needs no IRC machinery."""
    s = Streamer("teststreamer", StreamerSettings())
    s.settings.chat = ChatPresence.NEVER
    return s


def test_set_offline_enqueues_when_online():
    s = _offline_test_streamer()
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
    s = _offline_test_streamer()
    s.is_online = False
    maint = mock.MagicMock()
    s.watch_streak_maintainer = maint
    s.set_offline()
    maint.maybe_enqueue.assert_not_called()


def test_set_offline_without_maintainer_does_not_crash():
    s = _offline_test_streamer()
    s.is_online = True
    s.set_offline()  # watch_streak_maintainer defaults to None


from TwitchChannelPointsMiner.classes.Twitch import Twitch


def _twitch():
    t = Twitch.__new__(Twitch)  # bypass __init__/login
    return t


def test_get_recent_vod_id_parses_first_edge():
    t = _twitch()
    streamer = mock.MagicMock()
    streamer.username = "foo"
    with mock.patch.object(Twitch, "post_gql_request", return_value={
        "data": {"user": {"videos": {"edges": [{"node": {"id": "12345"}}]}}}
    }):
        assert t.get_recent_vod_id(streamer) == "12345"


def test_get_recent_vod_id_returns_none_when_no_videos():
    t = _twitch()
    streamer = mock.MagicMock()
    streamer.username = "foo"
    with mock.patch.object(Twitch, "post_gql_request", return_value={
        "data": {"user": {"videos": {"edges": []}}}
    }):
        assert t.get_recent_vod_id(streamer) is None


def test_get_recent_vod_id_returns_none_on_bad_response():
    t = _twitch()
    streamer = mock.MagicMock()
    streamer.username = "foo"
    with mock.patch.object(Twitch, "post_gql_request", return_value={}):
        assert t.get_recent_vod_id(streamer) is None


def test_watch_vod_posts_and_stops_when_streak_earned():
    t = _twitch()
    t.user_agent = "ua"
    t.twitch_login = mock.MagicMock()
    t.twitch_login.get_user_id.return_value = "777"

    streamer = mock.MagicMock()
    streamer.username = "foo"
    streamer.channel_id = "5"
    streamer.stream.spade_url = "https://spade.example/track"
    # Streak starts missing; sleep simulates the pubsub event flipping it to
    # False so the loop stops after exactly one POST.
    streamer.stream.watch_streak_missing = True

    def _earn_streak(_seconds):
        streamer.stream.watch_streak_missing = False

    with mock.patch.object(Twitch, "get_spade_url") as mock_spade, mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.requests.post"
    ) as post, mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.time.sleep",
        side_effect=_earn_streak,
    ):
        post.return_value.status_code = 204
        t.watch_vod_for_streak(streamer, "999", max_minutes=8)

    assert post.call_count == 1  # stopped after streak earned


def test_watch_vod_fetches_spade_url_when_missing():
    t = _twitch()
    t.user_agent = "ua"
    t.twitch_login = mock.MagicMock()
    t.twitch_login.get_user_id.return_value = "777"

    streamer = mock.MagicMock()
    streamer.username = "foo"
    streamer.channel_id = "5"
    streamer.stream.spade_url = None
    streamer.stream.watch_streak_missing = False

    def _set_spade(s):
        s.stream.spade_url = "https://spade.example/track"

    with mock.patch.object(
        Twitch, "get_spade_url", side_effect=_set_spade
    ) as mock_spade, mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.requests.post"
    ) as post, mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.time.sleep"
    ):
        post.return_value.status_code = 204
        t.watch_vod_for_streak(streamer, "999", max_minutes=8)

    mock_spade.assert_called_once_with(streamer)


def test_watch_vod_sends_all_events_and_reports_count_when_not_flipped():
    """When watch_streak_missing never flips (the normal VOD case, since a
    save is confirmed by a separate milestone), we send all max_minutes events
    and log an honest completion count -- never a false 'did not recover'."""
    t = _twitch()
    t.user_agent = "ua"
    t.twitch_login = mock.MagicMock()
    t.twitch_login.get_user_id.return_value = "777"

    streamer = mock.MagicMock()
    streamer.username = "foo"
    streamer.channel_id = "5"
    streamer.stream.spade_url = "https://spade.example/track"
    streamer.stream.watch_streak_missing = True  # never flips

    with mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.requests.post"
    ) as post, mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.time.sleep"
    ), mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.logger"
    ) as log:
        post.return_value.status_code = 204
        t.watch_vod_for_streak(streamer, "999", max_minutes=4)

    assert post.call_count == 4  # all events sent, no early stop
    logged = " ".join(str(c.args[0]) for c in log.info.call_args_list)
    assert "Sent 4/4" in logged
    assert "did not recover" not in logged


def test_recover_calls_twitch_with_vod():
    twitch = mock.MagicMock()
    twitch.get_recent_vod_id.return_value = "42"
    m = WatchStreakMaintainer(twitch=twitch, streak_watch_minutes=7)
    streamer = mock.MagicMock()
    m._recover(streamer)
    twitch.get_recent_vod_id.assert_called_once_with(streamer)
    twitch.watch_vod_for_streak.assert_called_once()
    assert twitch.watch_vod_for_streak.call_args.args[1] == "42"


def test_recover_skips_when_no_vod():
    twitch = mock.MagicMock()
    twitch.get_recent_vod_id.return_value = None
    m = WatchStreakMaintainer(twitch=twitch, streak_watch_minutes=7)
    m._recover(mock.MagicMock())
    twitch.watch_vod_for_streak.assert_not_called()


def test_recover_clears_dedup_so_channel_can_requeue():
    twitch = mock.MagicMock()
    twitch.get_recent_vod_id.return_value = "42"
    m = WatchStreakMaintainer(twitch=twitch, streak_watch_minutes=7)
    streamer = mock.MagicMock()
    streamer.channel_id = "5"
    m._queued.add("5")
    m._recover(streamer)
    assert "5" not in m._queued


def test_history_records_enqueue_and_recovery_result():
    twitch = mock.MagicMock()
    twitch.get_recent_vod_id.return_value = "42"
    twitch.watch_vod_for_streak.return_value = {
        "recovered": False, "accepted": 8, "max": 8
    }
    m = WatchStreakMaintainer(twitch=twitch, streak_watch_minutes=7)
    m.maybe_enqueue(_streamer(channel_id="9"))  # _streamer() has username via MagicMock
    streamer = mock.MagicMock()
    streamer.username = "agurin"
    streamer.channel_id = "9"
    m._recover(streamer)

    feed = m.recovery_history()  # newest first
    statuses = [e["status"] for e in feed]
    assert "missed" in statuses
    assert "recovering" in statuses
    assert "sent" in statuses
    # newest-first ordering: the final 'sent' event is at index 0
    assert feed[0]["status"] == "sent"
    assert "8/8" in feed[0]["detail"]


def test_history_records_no_vod():
    twitch = mock.MagicMock()
    twitch.get_recent_vod_id.return_value = None
    m = WatchStreakMaintainer(twitch=twitch, streak_watch_minutes=7)
    streamer = mock.MagicMock()
    streamer.username = "foo"
    m._recover(streamer)
    assert m.recovery_history()[0]["status"] == "no_vod"


def test_records_to_persistent_store_when_provided():
    store = mock.MagicMock()
    twitch = mock.MagicMock()
    twitch.get_recent_vod_id.return_value = None
    m = WatchStreakMaintainer(twitch=twitch, streak_watch_minutes=7, store=store)
    streamer = mock.MagicMock()
    streamer.username = "foo"
    m._recover(streamer)
    # the no_vod event was persisted
    assert any(
        c.args[1] == "no_vod" for c in store.record.call_args_list
    )
