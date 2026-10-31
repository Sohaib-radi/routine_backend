# CLAUDE.md

Core architecture decisions for this project. Follow them in every change.
If a request conflicts with a rule here, say so before writing code.

## Context

This repository is the **backend only** (Django + DRF) of a routine/habit app for
**individual users** (no multi-tenancy, no organizations). It serves a REST API
consumed by a Next.js web app and a React Native mobile app, which live in
separate repos.

- Target: ~20k users. Modular monolith on PostgreSQL; Redis + Celery for background work.
- Consequences for the API:
  - JWT auth usable by both web and mobile.
  - Versioned endpoints (`/api/v1/`). Never break an existing endpoint: mobile users
    run old app versions. Add fields or a new version instead.
  - The OpenAPI schema (`drf-spectacular`) must stay accurate: clients are generated from it.

## App structure and dependency direction

```
ai  ──►  health / spiritual / tasks  ──►  tracking  ──►  accounts  ──►  profiles
```

- Arrows mean "imports", not the user's path. AI is an optional entry point:
  every feature works through normal API endpoints without AI, and AI ends in the
  same domain services as manual input. AI never has its own write path.

  ```
  Manual: POST /api/v1/health/meals/ → health.services → tracking.services
  AI:     POST /api/v1/ai/...        → proposal → user confirms → health.services → tracking.services
  ```
- A lower app never imports a higher one. `tracking` knows nothing about `health`.
- `profiles` holds admin-defined presets (color, avatar, notification style) that
  users choose from. It is NOT the user's personal data: that lives on `accounts.User`
  (timezone, etc.). Each user has `User.profile` (FK, `on_delete=PROTECT`).
- `profiles` is the lowest app and cannot import `accounts`. Every write that touches
  both a user and a profile lives in `accounts.services`: `deactivate_profile(profile)`
  (moves its users to the default profile in the same transaction) and
  `select_profile(user, profile)`. Their endpoints (`/profiles/{id}/deactivate/`,
  `/profiles/{id}/select/`) and the admin "Deactivate" action are served by `accounts`.
- `tracking` is the shared engine: trackables, schedules, check-ins, pauses, streaks.
  Domain apps (health, spiritual, tasks) store their own data and plug into `tracking`.
- Do not re-implement scheduling, check-ins, pauses or streaks inside a domain app.

Example: a user logs lunch. `health` stores the meal and food items, then calls
`tracking.services.log_checkin()` to record calories for the day.

Layout of each app:

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

Draft code for upcoming work lives in `docs/draft/`.

## Rules

### 1. Write only through services
Other apps never create or update another app's models directly. They call its
services.

- Yes: `tracking.services.log_checkin(user, trackable, date, value, source)`
- No: `CheckIn.objects.create(...)` from `health`, `ai` or a view.

Example: a user logs "2 glasses of water" by voice, then taps +1 in the app. Both
go through `log_checkin()`, so the value becomes 3 and the streak updates once.

### 2. Every date is the user's local date
Each user has an IANA `timezone` (e.g. `Africa/Cairo`). "Today" is computed with
`tracking.services.user_today(user)`, never `date.today()` or a UTC date.

Example: a user in Cairo checks in at 01:30 on Tuesday (Monday 23:30 UTC).
The check-in belongs to Tuesday.

### 3. One check-in per trackable per day
`CheckIn` is unique on `(trackable, date)`. Repeated logs on the same day
accumulate into `value`; they never create extra rows.

Backfill: check-ins are accepted for today and yesterday only (user's local
date). Older and future dates are refused. This applies to every entry point,
including AI agents.

### 4. Pauses never change history
Vacation, sickness, travel = a `Pause` period (`trackable=NULL` pauses everything,
`end_date=NULL` means open-ended). Streaks and completion rates skip paused days;
reminders are not sent during a pause. Never edit or delete past check-ins because
of a pause.

### 5. Computed data is a cache, never the source of truth
`StreakCache` and other aggregates are derived from `CheckIn` + `Pause` and
recomputed by Celery. They must always be rebuildable from the source data.

### 6. Do not pre-generate future occurrences
"What is due today" is computed from `Schedule`. Rows are written only when the
user acts.

### 7. AI proposes, the user confirms
Agents (voice, photo, text) return proposed actions; nothing is saved until the
user confirms. Every saved check-in records its `source`
(`manual`, `voice`, `photo`, `agent`, `import`).

Example: a photo is analyzed as "rice + chicken, ~650 kcal"; the user corrects the
portion, confirms, and it is saved with `source=photo`.

## Code conventions

- UUID primary keys on all models exposed through the API.
- Every query is scoped to the requesting user. No endpoint returns another user's data.
- Views and serializers stay thin: logic lives in `services.py`, reads in `selectors.py`.
- Heavy work (AI calls, streak recompute, notifications) runs in Celery, never in
  the request.
- Every new service function gets tests.
