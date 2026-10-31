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

Trackables are never deleted through the API: their check-ins are history. Archive them instead.

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
