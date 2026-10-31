# Architecture

## Overview

This repository is the **backend only** of a routine/habit app for individual users
(no organizations, no multi-tenancy). It is a **modular monolith**: one Django
project split into apps with strict dependency rules, on PostgreSQL, with Redis and
Celery for background work. It is sized for roughly 20,000 users.

```
            ┌──────────────┐   ┌────────────────────┐
            │ Next.js web  │   │ React Native mobile│
            └──────┬───────┘   └─────────┬──────────┘
                   │  JWT, /api/v1/      │
                   ▼                     ▼
            ┌─────────────────────────────────────┐
            │      Django + DRF (this repo)       │
            └──────┬──────────────────┬───────────┘
                   │                  │ tasks
                   ▼                  ▼
            ┌────────────┐     ┌──────────────┐     ┌───────────────┐
            │ PostgreSQL │     │ Redis        │◄────│ Celery worker │
            └────────────┘     │ broker+cache │     │ Celery beat   │
                               └──────────────┘     └───────────────┘
```

## Apps and dependency direction

```
ai  ──►  health / spiritual / tasks  ──►  tracking  ──►  accounts  ──►  profiles
(planned)        (planned)               (built)        (built)        (built)
```

Arrows mean "imports". A lower app never imports a higher one.

| App        | Responsibility |
|------------|----------------|
| `profiles` | Admin-defined presets: name, colour, avatar, notification style/sound/vibration. Exactly one default profile, which is always active. Cannot import `accounts`. |
| `accounts` | The `User` model (email login, UUID id, IANA time zone, selected profile), registration, JWT auth, roles (groups) and permissions. Owns every write that touches both a user and a profile (`select_profile`, `deactivate_profile`). |
| `tracking` | The shared engine: trackables, schedules, reminders, check-ins, pauses and streak caches. Knows nothing about health or spiritual data. |
| `health`, `spiritual`, `tasks` | Planned. Will store their own data and record progress through `tracking.services`. |
| `ai`       | Planned. Optional entry point (voice, photo, text) that proposes actions; it ends in the same domain services as manual input. |

### Inside each app

```
apps/<app>/
  models.py
  services.py    # business logic (writes)
  selectors.py   # read queries
  api/           # serializers + views (thin)
  tasks.py       # Celery tasks
  admin.py
  tests/
```

- **Views and serializers are thin.** They validate shapes and call a service or selector.
- **Writes go through services.** No app creates another app's rows directly.
  For example, a future `health` app will call `tracking.services.log_checkin()`,
  never `CheckIn.objects.create()`.
- **Reads go through selectors**, always scoped to the requesting user.
- Services raise Django's `ValidationError`; `config/exceptions.py` turns it into a
  DRF `400` response.

## Design principles

1. **Write only through services.** Every entry point (API, admin, Celery, future AI)
   uses the same functions, so rules are enforced once.
2. **Every date is the user's local date.** "Today" comes from
   `tracking.dates.user_today(user)`, based on `User.timezone`. A check-in at 01:30
   Tuesday in Cairo belongs to Tuesday, even though it is still Monday in UTC.
3. **One check-in per trackable per day.** Enforced by a unique constraint; repeated
   logs accumulate.
4. **Pauses never change history.** They change how history is interpreted.
5. **Computed data is a cache.** `StreakCache` can always be rebuilt from
   `CheckIn` + `Pause` + `Schedule`.
6. **No pre-generated future occurrences.** What is due is computed from the schedule;
   rows are written only when the user acts.
7. **AI proposes, the user confirms.** Every check-in records its `source`
   (`manual`, `voice`, `photo`, `agent`, `import`).

## API conventions

- All endpoints live under `/api/v1/`. Existing endpoints are never broken, because
  mobile users run older app versions: fields are added, or a new version is created.
- The OpenAPI schema (drf-spectacular) must stay accurate; clients are generated from it.
- UUID primary keys on every model exposed through the API.
- No endpoint returns another user's data.
- Heavy work (streak recompute, future AI calls and notifications) runs in Celery, never in the request.

## Background jobs

| Task | Trigger | What it does |
|------|---------|--------------|
| `tracking.tasks.recompute_streak` | After every check-in, pause or trackable change (on transaction commit) | Rebuilds one trackable's `StreakCache`. |
| `tracking.tasks.recompute_streaks_after_local_midnight` | Celery beat, every hour at minute 1 | Finds users whose local midnight passed in the last full hour and queues their trackables in batches of 200, so a missed day resets the streak even when nothing was logged. |

## Configuration

Settings are split by environment in `config/settings/`:

| Module    | Use |
|-----------|-----|
| `base.py` | Shared settings: apps, DRF, JWT, Redis cache, Celery, i18n (en/ar/fr). |
| `dev.py`  | Local development (DEBUG on). Default for `manage.py` and Celery. |
| `test.py` | Used by pytest. |
| `prod.py` | Production: every secret and host comes from the environment; HTTPS, HSTS, secure cookies. |
