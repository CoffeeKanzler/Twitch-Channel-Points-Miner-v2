# VOD Watch-Streak Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a watch-streak channel's live broadcast is missed (online→offline with the streak unearned), watch a recent VOD of that channel in a background thread to recover the streak.

**Architecture:** Detection happens at the online→offline edge in `Streamer.set_offline()`, which calls `WatchStreakMaintainer.maybe_enqueue(streamer)`. A dedicated `WatchStreakMaintainer(Thread)` drains a queue one streamer at a time, asks `Twitch` for a recent VOD id, and POSTs VOD minute-watched events to the channel's spade URL until the streak is awarded or a time cap is hit. Opt-in via a new per-streamer setting.

**Tech Stack:** Python 3.10+, `threading`, `queue.Queue`, `requests`, Twitch GQL + spade endpoints, `pytest` + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-19-vod-watch-streak-recovery-design.md`

---

## File Structure

- **Create** `TwitchChannelPointsMiner/classes/WatchStreakMaintainer.py` — the thread, queue, dedup, detection predicate, and recovery driver. Holds a reference to the `Twitch` instance.
- **Modify** `TwitchChannelPointsMiner/classes/entities/Streamer.py` — add `watch_streak_vod_recovery` to `StreamerSettings`; add `watch_streak_maintainer` slot to `Streamer`; hook `set_offline()`.
- **Modify** `TwitchChannelPointsMiner/classes/Twitch.py` — add `STREAK_WATCH_MINUTES` module constant (reuse in existing `Priority.STREAK` check), `get_recent_vod_id()`, and `watch_vod_for_streak()`.
- **Modify** `TwitchChannelPointsMiner/TwitchChannelPointsMiner.py` — construct the maintainer, attach it to each streamer, start/stop the thread (gated on the setting being enabled for at least one streamer).
- **Create** `tests/test_watch_streak_maintainer.py` — unit tests for the predicate, dedup, run loop, and Twitch parsing (network mocked).

---

## Task 0: Feasibility spike (manual — do FIRST, no committed code)

The exact VOD spade payload is not in the repo. Validate it before building anything.

- [ ] **Step 1: Capture a working VOD payload**

In a scratch Python REPL (or a throwaway `scratch_spike.py` that is NOT committed), using a logged-in `Twitch` instance from a normal run, pick one channel you have a live streak on that has a public VOD. Get its newest archive VOD id (browser URL `twitch.tv/videos/<ID>` works for the spike). Build this candidate payload and POST it to that channel's spade URL:

```python
import time, json
from base64 import b64encode

def vod_payload(channel_id, channel, user_id, vod_id):
    props = {
        "channel_id": channel_id,
        "broadcast_id": None,
        "player": "site",
        "user_id": user_id,
        "live": False,
        "channel": channel,
        "vod_id": vod_id,
        "content_mode": "video",
    }
    event = [{"event": "minute-watched", "properties": props}]
    data = json.dumps(event, separators=(",", ":"))
    return {"data": b64encode(data.encode("utf-8")).decode("utf-8")}

# twitch = <the running Twitch instance>; streamer = <the target Streamer>
# twitch.get_spade_url(streamer)   # ensures streamer.stream.spade_url
# import requests
# for _ in range(8):
#     r = requests.post(streamer.stream.spade_url, data=vod_payload(...),
#                       headers={"User-Agent": twitch.user_agent}, timeout=20)
#     print(r.status_code)
#     time.sleep(60)
```

- [ ] **Step 2: Confirm the streak is awarded**

Watch the live console / channel points: a `WATCH_STREAK` event should arrive (the running miner sets `streamer.stream.watch_streak_missing = False`). 

**Decision gate:**
- If the streak is awarded → record the EXACT property keys that worked (adjust `broadcast_id`/`content_mode`/`vod_id` field names as needed) and use them verbatim in Task 6.
- If nothing happens after ~8 minutes → try variants (omit `content_mode`; use `"vod_type": "archive"`; set `player: "popout"`). If no variant works, STOP and report back — the feature is not viable as designed and we reassess (do not ship a silent no-op).

- [ ] **Step 3: Delete the scratch file**

```bash
rm -f scratch_spike.py
```

---

## Task 1: Config flag + shared constant

**Files:**
- Modify: `TwitchChannelPointsMiner/classes/entities/Streamer.py` (StreamerSettings, ~lines 18-65)
- Modify: `TwitchChannelPointsMiner/classes/Twitch.py` (module top, near other imports; existing `minute_watched < 7` at the `Priority.STREAK` branch)
- Test: `tests/test_watch_streak_maintainer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_watch_streak_maintainer.py`:

```python
import unittest.mock as mock

from TwitchChannelPointsMiner.classes.entities.Streamer import StreamerSettings


def test_streamer_settings_has_vod_recovery_default_off():
    s = StreamerSettings()
    s.default()
    assert s.watch_streak_vod_recovery is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py::test_streamer_settings_has_vod_recovery_default_off -v`
Expected: FAIL (`AttributeError: ... watch_streak_vod_recovery`)

- [ ] **Step 3: Add the setting**

In `StreamerSettings.__slots__` add `"watch_streak_vod_recovery"`. In `__init__` add param `watch_streak_vod_recovery: bool = None` and `self.watch_streak_vod_recovery = watch_streak_vod_recovery`. In `default()`, add to the loop body after the existing `community_goals` default:

```python
        if self.watch_streak_vod_recovery is None:
            self.watch_streak_vod_recovery = False
```

Also extend the `__repr__` string to include `watch_streak_vod_recovery={self.watch_streak_vod_recovery}`.

- [ ] **Step 4: Add the shared constant in Twitch.py**

Near the top of `TwitchChannelPointsMiner/classes/Twitch.py`, after `logger = logging.getLogger(__name__)`, add:

```python
# Minutes of watching required before Twitch awards a watch-streak bonus.
STREAK_WATCH_MINUTES = 7
```

Then in `send_minute_watched_events`, replace the literal in the `Priority.STREAK` branch:

```python
                                # fix #425
                                and streamers[index].stream.minute_watched < STREAK_WATCH_MINUTES
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py::test_streamer_settings_has_vod_recovery_default_off -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_watch_streak_maintainer.py TwitchChannelPointsMiner/classes/entities/Streamer.py TwitchChannelPointsMiner/classes/Twitch.py
git commit -m "feat: add watch_streak_vod_recovery setting and STREAK_WATCH_MINUTES constant"
```

---

## Task 2: WatchStreakMaintainer — predicate + dedup enqueue

**Files:**
- Create: `TwitchChannelPointsMiner/classes/WatchStreakMaintainer.py`
- Test: `tests/test_watch_streak_maintainer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_streak_maintainer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "enqueue or skip or dedup"`
Expected: FAIL (`ModuleNotFoundError: ... WatchStreakMaintainer`)

- [ ] **Step 3: Create the class with predicate + enqueue**

Create `TwitchChannelPointsMiner/classes/WatchStreakMaintainer.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "enqueue or skip or dedup"`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add TwitchChannelPointsMiner/classes/WatchStreakMaintainer.py tests/test_watch_streak_maintainer.py
git commit -m "feat: WatchStreakMaintainer enqueue predicate + dedup"
```

---

## Task 3: Detection hook in Streamer.set_offline

**Files:**
- Modify: `TwitchChannelPointsMiner/classes/entities/Streamer.py` (Streamer `__slots__` ~line 72, `__init__` ~line 93, `set_offline` ~line 127)
- Test: `tests/test_watch_streak_maintainer.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "set_offline"`
Expected: FAIL (`AttributeError: ... watch_streak_maintainer`)

- [ ] **Step 3: Add slot, default, and hook**

In `Streamer.__slots__` add `"watch_streak_maintainer"`. In `Streamer.__init__` add (near the other defaults):

```python
        self.watch_streak_maintainer = None
```

In `set_offline`, call the maintainer inside the existing online-edge guard:

```python
    def set_offline(self):
        if self.is_online is True:
            self.offline_at = time.time()
            self.is_online = False
            if self.watch_streak_maintainer is not None:
                self.watch_streak_maintainer.maybe_enqueue(self)
        self.toggle_chat()
        # ... unchanged logging ...
```

(Place the `maybe_enqueue` call before `init_watch_streak` could ever reset state — it stays inside the `is_online is True` block, and `set_online` is the only caller of `init_watch_streak`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "set_offline"`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add TwitchChannelPointsMiner/classes/entities/Streamer.py tests/test_watch_streak_maintainer.py
git commit -m "feat: enqueue VOD streak recovery on Streamer offline transition"
```

---

## Task 4: Twitch.get_recent_vod_id

**Files:**
- Modify: `TwitchChannelPointsMiner/classes/Twitch.py` (new method near the other GQL helpers, e.g. after `get_stream_info`)
- Test: `tests/test_watch_streak_maintainer.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from TwitchChannelPointsMiner.classes.Twitch import Twitch


def _twitch():
    t = Twitch.__new__(Twitch)  # bypass __init__/login
    return t


def test_get_recent_vod_id_parses_first_edge():
    t = _twitch()
    t.post_gql_request = mock.MagicMock(return_value={
        "data": {"user": {"videos": {"edges": [{"node": {"id": "12345"}}]}}}
    })
    streamer = mock.MagicMock()
    streamer.username = "foo"
    assert t.get_recent_vod_id(streamer) == "12345"


def test_get_recent_vod_id_returns_none_when_no_videos():
    t = _twitch()
    t.post_gql_request = mock.MagicMock(return_value={
        "data": {"user": {"videos": {"edges": []}}}
    })
    streamer = mock.MagicMock()
    streamer.username = "foo"
    assert t.get_recent_vod_id(streamer) is None


def test_get_recent_vod_id_returns_none_on_bad_response():
    t = _twitch()
    t.post_gql_request = mock.MagicMock(return_value={})
    streamer = mock.MagicMock()
    streamer.username = "foo"
    assert t.get_recent_vod_id(streamer) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "get_recent_vod_id"`
Expected: FAIL (`AttributeError: 'Twitch' object has no attribute 'get_recent_vod_id'`)

- [ ] **Step 3: Implement the method**

Add to `class Twitch`:

```python
    def get_recent_vod_id(self, streamer):
        """Return the newest archived VOD id for the channel, or None.

        Uses a raw GraphQL query (no persisted-query hash to rot). The
        operationName key is included so post_gql_request's error handler
        does not KeyError.
        """
        json_data = {
            "operationName": "WatchStreakRecentVod",
            "query": (
                "query WatchStreakRecentVod($login: String!) {"
                "  user(login: $login) {"
                "    videos(first: 1, type: ARCHIVE, sort: TIME) {"
                "      edges { node { id } }"
                "    }"
                "  }"
                "}"
            ),
            "variables": {"login": streamer.username},
        }
        response = self.post_gql_request(json_data)
        try:
            edges = response["data"]["user"]["videos"]["edges"]
            if not edges:
                return None
            return edges[0]["node"]["id"]
        except (KeyError, TypeError, IndexError):
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "get_recent_vod_id"`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add TwitchChannelPointsMiner/classes/Twitch.py tests/test_watch_streak_maintainer.py
git commit -m "feat: Twitch.get_recent_vod_id via raw GraphQL query"
```

---

## Task 5: Twitch.watch_vod_for_streak

**Files:**
- Modify: `TwitchChannelPointsMiner/classes/Twitch.py` (new method; uses the spike-confirmed payload from Task 0)
- Test: `tests/test_watch_streak_maintainer.py`

> Replace the `properties` dict below with the EXACT keys confirmed in Task 0 Step 2 if they differed.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_watch_vod_posts_and_stops_when_streak_earned():
    t = _twitch()
    t.user_agent = "ua"
    t.twitch_login = mock.MagicMock()
    t.twitch_login.get_user_id.return_value = "777"
    t.get_spade_url = mock.MagicMock()

    streamer = mock.MagicMock()
    streamer.username = "foo"
    streamer.channel_id = "5"
    streamer.stream.spade_url = "https://spade.example/track"
    # Streak gets earned after first post -> loop should stop early.
    streamer.stream.watch_streak_missing = False

    with mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.requests.post"
    ) as post, mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.time.sleep"
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

    t.get_spade_url = mock.MagicMock(side_effect=_set_spade)

    with mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.requests.post"
    ) as post, mock.patch(
        "TwitchChannelPointsMiner.classes.Twitch.time.sleep"
    ):
        post.return_value.status_code = 204
        t.watch_vod_for_streak(streamer, "999", max_minutes=8)

    t.get_spade_url.assert_called_once_with(streamer)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "watch_vod"`
Expected: FAIL (`AttributeError: ... watch_vod_for_streak`)

- [ ] **Step 3: Implement the method**

Add to `class Twitch` (uses existing module imports: `requests`, `time`, `json`, `copy`, `b64encode`? — `b64encode` is NOT imported in Twitch.py; build the payload via the existing `Stream.encode_payload` helper instead):

```python
    def watch_vod_for_streak(self, streamer, vod_id, max_minutes=8):
        """POST VOD minute-watched events to the channel spade URL until the
        watch streak is awarded or max_minutes elapses."""
        if streamer.stream.spade_url is None:
            self.get_spade_url(streamer)
        if streamer.stream.spade_url is None:
            logger.debug(f"No spade_url for {streamer}; skip VOD streak recovery")
            return

        # NOTE: confirm these property keys against the Task 0 spike result.
        properties = {
            "channel_id": streamer.channel_id,
            "broadcast_id": None,
            "player": "site",
            "user_id": self.twitch_login.get_user_id(),
            "live": False,
            "channel": streamer.username,
            "vod_id": vod_id,
            "content_mode": "video",
        }
        streamer.stream.payload = [
            {"event": "minute-watched", "properties": properties}
        ]

        logger.info(f"Watching VOD {vod_id} to recover {streamer} watch streak")
        for _ in range(max_minutes):
            if streamer.stream.watch_streak_missing is False:
                logger.info(f"Watch streak recovered for {streamer} via VOD")
                return
            try:
                response = requests.post(
                    streamer.stream.spade_url,
                    data=streamer.stream.encode_payload(),
                    headers={"User-Agent": self.user_agent},
                    timeout=20,
                )
                logger.debug(
                    f"VOD minute-watched for {streamer} - {response.status_code}"
                )
            except requests.exceptions.RequestException as e:
                logger.debug(f"VOD minute-watched failed for {streamer}: {e}")
            time.sleep(60)

        if streamer.stream.watch_streak_missing is True:
            logger.info(f"VOD watch did not recover streak for {streamer}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "watch_vod"`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add TwitchChannelPointsMiner/classes/Twitch.py tests/test_watch_streak_maintainer.py
git commit -m "feat: Twitch.watch_vod_for_streak posts VOD minute-watched events"
```

---

## Task 6: WatchStreakMaintainer run/recover loop

**Files:**
- Modify: `TwitchChannelPointsMiner/classes/WatchStreakMaintainer.py`
- Test: `tests/test_watch_streak_maintainer.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "recover"`
Expected: FAIL (`AttributeError: ... _recover`)

- [ ] **Step 3: Implement run + _recover**

Add to `WatchStreakMaintainer`:

```python
    def _recover(self, streamer):
        try:
            vod_id = self.twitch.get_recent_vod_id(streamer)
            if vod_id is None:
                logger.info(f"No VOD available for {streamer}; cannot recover streak")
                return
            self.twitch.watch_vod_for_streak(
                streamer, vod_id, max_minutes=self.streak_watch_minutes + 1
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v -k "recover"`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole new suite**

Run: `python3 -m pytest tests/test_watch_streak_maintainer.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add TwitchChannelPointsMiner/classes/WatchStreakMaintainer.py tests/test_watch_streak_maintainer.py
git commit -m "feat: WatchStreakMaintainer run loop and VOD recovery driver"
```

---

## Task 7: Wire the maintainer into TwitchChannelPointsMiner

**Files:**
- Modify: `TwitchChannelPointsMiner/TwitchChannelPointsMiner.py` (`__slots__` ~line 64, `__init__` ~line 146-150, streamer construction ~line 301, thread startup ~line 347)

- [ ] **Step 1: Add slot and init**

In `__slots__` add `"watch_streak_maintainer"`. In `__init__`, near `self.minute_watcher_thread = None`:

```python
        self.watch_streak_maintainer = None
```

- [ ] **Step 2: Construct the maintainer and attach to streamers**

Add the import at the top of the file:

```python
from TwitchChannelPointsMiner.classes.WatchStreakMaintainer import WatchStreakMaintainer
```

Just before the streamer-building loop creates `self.streamers` (before the `for` that appends streamers, around line 251-301), create the maintainer:

```python
        from TwitchChannelPointsMiner.classes.Twitch import STREAK_WATCH_MINUTES
        self.watch_streak_maintainer = WatchStreakMaintainer(
            self.twitch, streak_watch_minutes=STREAK_WATCH_MINUTES
        )
```

Where each `streamer` is appended (right after `self.streamers.append(streamer)` ~line 301), attach the maintainer:

```python
                        streamer.watch_streak_maintainer = self.watch_streak_maintainer
```

- [ ] **Step 3: Start the thread only if any streamer opted in**

After `self.minute_watcher_thread.start()` (~line 352), add:

```python
            if at_least_one_value_in_settings_is(
                self.streamers, "watch_streak_vod_recovery", True
            ):
                self.watch_streak_maintainer.start()
            else:
                self.watch_streak_maintainer = None
                for streamer in self.streamers:
                    streamer.watch_streak_maintainer = None
```

(`at_least_one_value_in_settings_is` is already imported/used in this file — confirm the import near the top; it is used for `make_predictions`/`claim_drops`.)

- [ ] **Step 4: Stop the thread on shutdown**

Find where `self.minute_watcher_thread` / `self.ws_pool` are torn down (the `end()` / signal handler / `original_action`). Add, mirroring the existing shutdown style:

```python
        if self.watch_streak_maintainer is not None:
            self.watch_streak_maintainer.stop()
```

- [ ] **Step 5: Verify import + compile**

Run: `python3 -m compileall -q TwitchChannelPointsMiner`
Expected: no output (success)

Run: `python3 -c "import TwitchChannelPointsMiner.TwitchChannelPointsMiner"`
Expected: no error

- [ ] **Step 6: Commit**

```bash
git add TwitchChannelPointsMiner/TwitchChannelPointsMiner.py
git commit -m "feat: wire WatchStreakMaintainer into miner lifecycle (opt-in)"
```

---

## Task 8: Full regression + docs

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass (existing 21 watching-endpoint tests + the new maintainer tests)

- [ ] **Step 2: Compile the whole package**

Run: `python3 -m compileall -q TwitchChannelPointsMiner example.py`
Expected: no output

- [ ] **Step 3: Document the setting**

In `README.md`, find the `StreamerSettings(...)` documentation table/section and add a row for `watch_streak_vod_recovery` (default `False`): "When a streak channel goes offline before the miner could watch it live, watch a recent VOD to recover the watch streak. Requires `watch_streak=True`."

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document watch_streak_vod_recovery setting"
```

- [ ] **Step 5: Live validation**

Run the miner with `watch_streak_vod_recovery=True` on at least one streamer that has VODs. Confirm in logs: when a streak channel goes offline unwatched, you see "Queued ... for VOD watch-streak recovery", then "Watching VOD ... to recover", then ideally "Watch streak recovered ... via VOD".

---

## Self-Review Notes

- **Spec coverage:** trigger=missed-only (Task 0/3), dedicated thread+queue (Tasks 2/6), detection predicate (Task 2/3), get_recent_vod_id (Task 4), watch_vod_for_streak (Task 5), config opt-in (Task 1), wiring + lifecycle (Task 7), STREAK_WATCH_MINUTES shared constant (Task 1), spike-first to de-risk payload (Task 0), tests stay green (Task 8). All spec sections covered.
- **Payload risk:** Task 0 gates the whole feature; Task 5's payload keys must be updated to match the spike.
- **Type consistency:** `maybe_enqueue(streamer)->bool`, `_recover(streamer)`, `get_recent_vod_id(streamer)->str|None`, `watch_vod_for_streak(streamer, vod_id, max_minutes)`, setting `watch_streak_vod_recovery`, slot `watch_streak_maintainer`, constant `STREAK_WATCH_MINUTES` — used identically across tasks.
