# Tracking engine

`apps/tracking` is the shared engine every domain uses. It answers four questions:
*what does the user track, when is it due, what did they do, and how consistent are they?*

## Models

| Model         | Purpose |
|---------------|---------|
| `Trackable`   | Anything a user does and records: a habit, a prayer, water intake, a workout. |
| `Schedule`    | When a trackable is due (one per trackable). |
| `Reminder`    | Local time of day and weekdays for a reminder (sending is not built yet). |
| `CheckIn`     | What the user did on one local date. Unique per `(trackable, date)`. |
| `Pause`       | A period when one trackable, or everything, is on hold. |
| `StreakCache` | Derived statistics: current streak, longest streak, 30-day completion rate. |

### Trackable

| Field | Notes |
|-------|-------|
| `domain` | `general`, `health`, `spiritual`, `tasks` |
| `kind` | `boolean` (done / not done), `quantity` (e.g. 8 glasses), `duration` (minutes) |
| `target_value`, `unit` | For quantity/duration: reaching the target makes the day `done`, below it is `partial`. |
| `start_date`, `end_date` | User's local dates. `start_date` defaults to today. |
| `is_archived` | The user stopped this habit. Archived trackables accept no check-ins. |
| `config` | JSON for domain-specific settings. |
| `managed_by` | Empty for the user's own trackables; otherwise the app that created it (e.g. `health`). See [managed trackables](#managed-trackables). |

Trackables are never deleted through the API: their check-ins are history. Archive them instead.

### Managed trackables

Some trackables are created by a domain app, which owns their targets. Example:
`health` creates water, sleep, steps and activity trackables whose targets come
from the user's plan ([health](health.md)). They have `managed_by='health'`.

On a managed trackable, `tracking.services` refuses (with a `400` in the API):

- changes to `target_value`, `unit`, `kind`, `domain`, `start_date`, `end_date` and the schedule,
- archiving and unarchiving,

unless the caller passes the same `managed_by` (`update_trackable(...,
managed_by='health')`). The tracking API never passes it, so the plan and its
trackables cannot drift apart. Sending an unchanged value is accepted, so clients
that send the whole object back still work.

Still allowed for everyone: renaming, icon, colour, and check-ins as usual.
`managed_by` is read-only in the API and the admin; in the admin, the locked fields
and the schedule are read-only too.

tracking never interprets the value beyond comparing it, so it still knows nothing
about `health`.

### Schedule

| `frequency` | Extra field | Example |
|-------------|-------------|---------|
| `daily` | | Every day |
| `weekdays` | `weekdays_mask` | Mon/Wed/Fri |
| `every_n_days` | `interval_days` | Every 3 days from the start date |
| `times_week` | `times_per_period` | Any 3 days a week |
| `times_month` | `times_per_period` | Any 10 days a month |
| `rrule` | `rrule` | Any RFC 5545 rule, e.g. `FREQ=MONTHLY;BYMONTHDAY=1,15` |

`weekdays_mask` is a bitmask: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64
(127 = every day). Mon/Wed/Fri = 1 + 4 + 16 = 21.

No future rows are generated. "What is due today" is computed from the schedule each time.

## Check-ins

**One row per trackable per day.** Logging again on the same day adds to the value:

```
POST value=2   → value 2  (partial, target 8)
POST value=1   → value 3
POST value=5   → value 8  (done)
```

Every source (manual, voice, photo, agent, import) goes through the same
`log_checkin()` function, so a voice log followed by a tap in the app still gives one
row and one streak update. The row keeps the source of the first log of the day.

| Action | Service | Behaviour |
|--------|---------|-----------|
| Log | `log_checkin` | Creates or accumulates. Logging a skipped day turns it into a real check-in. |
| Skip | `skip_checkin` | Marks the day `skipped`: neither breaks nor extends the streak. Not allowed if the day already has a check-in. |
| Correct | `set_checkin_value` | Sets the value directly (no accumulation). Not for boolean trackables: delete instead. |
| Delete | `delete_checkin` | Removes a check-in or a skip. |

### The today/yesterday window

Check-ins can be logged, skipped, corrected or deleted **only for today and
yesterday**, in the user's local date. Future dates and older dates are refused.
This applies to every entry point, including future AI agents. Older check-ins are frozen.

The date must also fall within the trackable's `start_date`/`end_date`.

### Concurrency

Each write locks the trackable row (`SELECT ... FOR UPDATE`), so two simultaneous
logs (for example a double tap) are serialized and cannot collide on the unique
constraint or lose an increment. On top of that, the API supports
[idempotency keys](api.md#idempotency-keys) for retries.

## Pauses

A pause covers vacation, sickness, travel or anything else.

- `trackable = null` pauses **everything**; otherwise one trackable.
- `end_date = null` means "until I resume". Both dates are inclusive.
- A pause can start **yesterday at the earliest** (no backdating over missed days).
  Future pauses (a planned vacation) are allowed.
- **Ending** a pause: the last paused day defaults to yesterday, so today counts
  again. It cannot be earlier than yesterday. Ending a pause that has not started
  yet cancels (deletes) it. A pause that is already over cannot be ended.
- Pauses **never** edit or delete check-ins. Paused days are just skipped when
  streaks are computed.

**Archive vs pause:** archive = the user stopped the habit (streak not protected).
Pause = a temporary break (streak protected).

## Streaks

`selectors.compute_streak()` walks every day from the trackable's start to today:

| Day | Effect |
|-----|--------|
| Done | Extends the current run |
| Paused, not scheduled, or skipped | Neutral |
| Today, not done yet | Neutral (the day isn't over) |
| Any other scheduled past day | Breaks the run |

It returns `current`, `longest` and `completion_rate_30d` (done days / expected days
over the last 30, in percent; today counts only once it is done).

The result is stored in `StreakCache`, which is **only a cache**: it is recomputed by
Celery after each change and every hour for users whose local midnight just passed,
and can always be rebuilt from the source data. `computed_for_date` tells clients how
fresh it is. Admins can also trigger a recompute from the admin panel.

### Known limitations

- `times_week` / `times_month` schedules report zero streaks for now; they need a
  per-period computation (a week counts when enough days are done).
- Every skip is neutral; there is no skip limit per trackable yet.

## The "Today" screen

`GET /api/v1/tracking/today/` returns the user's local date and, for each trackable
due today (not archived, not paused, scheduled for today), the trackable,
today's check-in (or `null`) and its cached streak.

## Services (`services.py`)

Every write goes through these functions: the API, the admin, Celery, the domain
apps (health) and, later, AI. Each one checks that the objects belong to `user`.

| Function | What it does |
|----------|--------------|
| `user_today(user)` | The user's local date (from `tracking.dates`). Use it instead of `date.today()`. |
| `create_trackable(user, *, name, schedule=None, managed_by='', **fields)` | A trackable and its schedule (default daily). `start_date` defaults to today. |
| `update_trackable(user, trackable, *, schedule=None, managed_by='', **fields)` | `kind` never changes; `start_date` cannot move past existing check-ins; managed fields are locked (see above). |
| `archive_trackable` / `unarchive_trackable(user, trackable, managed_by='')` | Stop / resume. Refused for managed trackables. |
| `log_checkin(user, trackable, date, value=None, source='manual', note='')` | Creates or **accumulates** the day's check-in. Today/yesterday only. Locks the trackable row. |
| `skip_checkin(user, trackable, date, ...)` | Marks the day skipped. |
| `set_checkin_value(user, checkin, value)` | Correction: sets the value (no accumulation). Positive values only: delete instead of setting 0. |
| `delete_checkin(user, checkin)` | Correction: removes a check-in or a skip. |
| `create_pause(user, *, start_date, end_date=None, trackable=None, reason, note)` | Pause one trackable or everything. Starts yesterday at the earliest. |
| `end_pause(user, pause, last_paused_day=None)` | Resume; cancels a pause that hasn't started. |
| `refresh_streak_cache(trackable_id)` | Rebuild one `StreakCache` (called by Celery). |

Every write that changes how history reads queues a streak recompute **after the
transaction commits** (`transaction.on_commit`), never inside the request.

## Selectors (`selectors.py`)

| Function | Returns |
|----------|---------|
| `trackables_for(user)` | The user's trackables (with schedule and streak) |
| `due_today(user)`, `today_screen(user)` | What is due today; the Today screen (3 queries) |
| `scheduled_dates(schedule, start, end)`, `is_scheduled(schedule, date)` | When a trackable is due |
| `paused_dates(user, trackable, start, end)`, `is_paused(user, trackable, date)` | Paused days of one trackable |
| `paused_on(user, date)` | `(everything_paused, ids paused individually)` on one date, one query. Used by the health dashboard. |
| `checkins_for(user)`, `checkin_history(user, trackable, start, end)` | Check-ins (history: newest first, max 366 days) |
| `pauses_for(user, active=None)` | Pauses, running/planned or over |
| `compute_streak(trackable, today)` | Current, longest, 30-day completion rate, from the source data |
| `timezones_with_new_day(start, end)`, `trackable_ids_for_new_day(start, end)` | Used by the hourly midnight job |

## API

Under `/api/v1/tracking/`; full list in the [API reference](api.md#tracking).

| Method | Path | Description |
|--------|------|-------------|
| GET | `today/` | The Today screen |
| GET / POST | `trackables/` | List (`?archived=`, `?domain=`) / create |
| GET / PATCH | `trackables/{id}/` | Read / edit (no DELETE: archive instead) |
| POST | `trackables/{id}/archive/`, `unarchive/` | Stop / resume |
| GET | `trackables/{id}/checkins/` | History (`?from=&to=`) |
| POST | `checkins/`, `checkins/skip/` | Log (accumulates) / skip. `Idempotency-Key` supported. |
| PATCH / DELETE | `checkins/{id}/` | Correct the value / remove |
| GET / POST | `pauses/` | List (`?active=`) / create |
| GET | `pauses/{id}/` | Read |
| POST | `pauses/{id}/end/` | Resume |

## Celery tasks (`tasks.py`)

| Task | When | What |
|------|------|------|
| `recompute_streak(trackable_id)` | After each write that affects history | Rebuild one streak cache |
| `recompute_streaks(trackable_ids)` | Batches of the hourly job | Rebuild several |
| `recompute_streaks_after_local_midnight(now=None)` | Celery beat, every hour at minute 1 | Users whose local midnight passed in the last full hour: queue all their trackables in batches of 200, so a missed day resets the streak even with no activity. |

## Admin

Trackables (with schedule and reminders inline) can be edited for support, except
the locked fields of managed trackables. Check-ins, pauses and streak caches are
read-only; streak caches have a **Recompute** action.
