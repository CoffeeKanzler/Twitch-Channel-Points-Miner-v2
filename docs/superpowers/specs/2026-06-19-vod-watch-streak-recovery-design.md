# VOD Watch-Streak Recovery — Design

**Date:** 2026-06-19
**Addresses:** upstream issue #825 "Maintain watch streak after missing stream"
**Status:** Approved design, pending implementation plan

## Problem

Twitch awards a "watch streak" bonus for watching consecutive live broadcasts of a
channel. This miner can only watch **2 channels at once**. With a large streamer list,
a lower-priority channel can go **online and offline before the miner ever tunes in**,
so that broadcast's streak is missed and the consecutive streak resets — losing points.

The user has confirmed empirically that **watching a recent VOD of the channel maintains
the watch streak**. This feature uses that to recover a streak that live watching missed.

## Goal / Non-goals

**Goal:** When a watch-streak channel's broadcast is missed (online→offline with the
streak never earned), watch a recent VOD of that channel to recover the streak.

**Non-goals:**
- No periodic/24h sweep (explicitly deferred — trigger is missed-streak only).
- No change to live watching behavior or the 2-slot limit.
- Not enabled by default — opt-in.

## Trigger: missed-streak detection only

A broadcast is considered *missed* when a streamer transitions **online → offline** and,
for that broadcast:
- `streamer.settings.watch_streak is True`, and
- `streamer.stream.watch_streak_missing is True` (no `WATCH_STREAK` points event was
  received — see `Streamer.update_history`/reason_code `WATCH_STREAK`), and
- `streamer.stream.minute_watched < 7` (the live watch never reached the streak
  threshold — the same `minute_watched < 7` check the live `Priority.STREAK` branch
  already uses; factor it into a shared `STREAK_WATCH_MINUTES = 7` constant to avoid a
  magic-number divergence).

When all hold at the offline transition, the streamer is enqueued for VOD recovery.

## Architecture

Detection lives in the existing live path; recovery runs in a **dedicated background
thread with a queue**, so it never competes with the 2 live watch slots and can be
tested in isolation.

```
send_minute_watched_events (existing thread)
    on streamer online->offline:
        if missed_streak(streamer): maintainer.enqueue(streamer)

WatchStreakMaintainer(Thread)              # new component
    while self.running:
        streamer = self.queue.get()
        vod_id = self.twitch.get_recent_vod_id(streamer)
        if vod_id is None: continue        # channel has no public VODs -> skip
        self.twitch.watch_vod_for_streak(streamer, vod_id)
```

### Components

**1. `WatchStreakMaintainer(threading.Thread)`** — new file
`TwitchChannelPointsMiner/classes/WatchStreakMaintainer.py`.
- Owns a `queue.Queue` of streamers needing recovery.
- `enqueue(streamer)` — dedupes (don't queue a streamer already queued/in-progress).
- `run()` — drains the queue, calls into `Twitch` for the VOD work, sleeps when idle.
- Started/stopped alongside the existing minute-watcher thread in
  `TwitchChannelPointsMiner` (mirrors how `send_minute_watched_events`/`ws_pool` are
  managed), gated on the config flag below.

**2. Detection hook** — in `Twitch.send_minute_watched_events` (or the offline-transition
point in `Streamer`/`TwitchChannelPointsMiner`, whichever owns the online→offline edge).
Calls `maintainer.enqueue(streamer)` when `missed_streak(streamer)` is true. Detection
adds no network calls.

**3. `Twitch.get_recent_vod_id(streamer)`** — new method.
Fetches the channel's most recent VOD id via a **raw GraphQL query** (send `query` text,
not a `persistedQuery` hash — avoids depending on a Twitch-internal hash that would rot).
Returns the newest archive/highlight video id, or `None` if the channel has no public
VODs (VODs disabled / none published).

**4. `Twitch.watch_vod_for_streak(streamer, vod_id)`** — new method.
- Reuses `GQLOperations.PlaybackAccessToken` with `isVod=True, vodID=vod_id` (the op
  already exists; the old m3u8 code used it with `isVod=False`).
- Builds a **VOD minute-watched spade payload** (see Risk below) and POSTs it to
  `streamer.stream.spade_url`, reusing `Stream.encode_payload`-style b64 encoding.
- Sends events for up to `STREAK_WATCH_MINUTES + 1` minutes, ~1/min, stopping early once
  a `WATCH_STREAK` points event is observed for the streamer (sets
  `watch_streak_missing = False`). Logs success/failure.

### Config

- New setting `watch_streak_vod_recovery: bool`, **default `False`** (opt-in), on
  `StreamerSettings` (per-streamer, with a global default like other settings).
- Reuses the existing `watch_streak` flag — recovery only applies to channels that
  already have watch-streak watching enabled.

## Data flow

1. Live loop watches ≤2 channels; a streak channel goes live but isn't picked.
2. Channel goes offline; detection sees missed streak → `maintainer.enqueue(streamer)`.
3. Maintainer thread: `get_recent_vod_id` → `watch_vod_for_streak`.
4. Twitch awards the streak; a `WATCH_STREAK` points event flips `watch_streak_missing`
   to `False`; the maintainer stops early and logs the recovered streak.

## Error handling

- No spade URL / no VOD / GQL error / non-2xx watch response → log at debug/warning and
  skip the channel (never crash the thread; wrap the run loop like
  `send_minute_watched_events` does).
- Queue dedupe prevents pile-ups if a channel is missed repeatedly.
- Thread is daemon-style and honors `self.running` for clean shutdown.

## Testing

- **Unit:** `missed_streak(streamer)` truth table (each condition flipped); maintainer
  `enqueue` dedupe; maintainer skips when `get_recent_vod_id` returns `None`. Twitch
  network methods mocked.
- **Spike (first implementation step, manual/live):** send a single VOD watch for one
  channel with a known recent VOD and confirm a `WATCH_STREAK` event arrives — this
  validates the VOD minute-watched payload **before** building the full loop.
- Existing `tests/test_watching_endpoint.py` must stay green.

## Risk: VOD minute-watched payload is not in the codebase

The live payload is
`{"event":"minute-watched","properties":{channel_id, broadcast_id, player:"site",
user_id, live:True, channel}}`. The **VOD** variant (`live:False`, a `vod_id`/video field,
possibly `content_mode`/`vod_type`) is not present in this repo and must be validated
empirically. Mitigation: the implementation plan begins with the spike above; the full
recovery loop is only built once one VOD watch is confirmed to award a streak. If the
spike fails, we stop and reassess rather than shipping a silent no-op.

## Out of scope / future

- Periodic 24h sweep (the issue's broader proposal) — can layer on later behind a mode
  setting if desired.
- Clip-based recovery (VOD is sufficient and simpler).
