# Changelog

What has been built so far, oldest first.

## Project skeleton

- Django + DRF project with settings split into base, dev, test and prod.
- PostgreSQL and Redis via docker-compose; Celery app wired to Redis.
- JWT authentication (rotating refresh tokens, logout via blacklist).
- OpenAPI schema and Swagger UI with drf-spectacular.
- Admin panel with Django Unfold, custom theme, English/Arabic/French.
- **profiles** app: admin-defined presets (colour, avatar, notification
  style/sound/vibration), one default profile that is always active.
- **accounts** app: email-based `User` with UUID id, IANA time zone and selected
  profile; registration, `/me/`, change password, delete account; user, role and
  permission administration; select and deactivate profile.
- **tracking** models: trackables, schedules, reminders, check-ins, pauses, streak cache.

## Check-in window

- Check-ins are accepted for **today and yesterday only** (user's local date),
  from every entry point.

## Tracking services and selectors

- Services: create/update/archive/unarchive trackables; log, skip, correct and
  delete check-ins (with row locking); create and end pauses; rebuild streak caches.
- Selectors: scheduled dates for every frequency, paused dates, what is due today,
  the Today screen, check-in history, streak computation.
- Streak recompute queued to Celery after each change.

## Tracking API

- `/api/v1/tracking/`: today, trackables, check-ins, pauses (see [API reference](api.md)).
- `Idempotency-Key` support on check-in endpoints.
- Django validation errors returned as `400` responses.

## Hourly streak recompute

- Celery beat task runs every hour and recomputes streaks for users whose local
  midnight passed, so missed days reset streaks even without new activity.

## Documented rules

- Corrections allowed in the same today/yesterday window; older check-ins are frozen.
- Pauses start yesterday at the earliest; future pauses allowed.
- Archive (habit stopped) vs pause (temporary break, streak protected).

## Managed trackables

- `Trackable.managed_by`: a trackable created by a domain app (e.g. `health`) has
  its target, unit, kind, domain, dates and schedule locked for everyone else
  (API, admin), and cannot be archived. Name, icon, colour and check-ins stay free.

## Health, round 1: plan and targets

- New `health` app: `HealthPlan` (versioned by `effective_from`, never rewritten),
  `WeightEntry` (one per day, last 30 days), `HealthMetric` (metric ↔ trackable link).
- Calculations: Mifflin-St Jeor, activity factors, cut −15% / maintain / bulk +10%,
  calories rounded to 10, safety floor 1500/1200 kcal with a flag, protein 2 g/kg,
  fat 25%, carbs the remainder, water 50 ml/kg rounded to 250 ml. Manual mode with
  protein capped at 75% of calories. Age 18+.
- Confirming a plan records today's weight and creates/updates the water, sleep,
  steps and activity trackables (exactly one each).
- API: `/api/v1/health/plan/preview/`, `plan/`, `plan/history/`, `weight/`.
- CLAUDE.md: meals (not check-ins) are the source of truth for calories and macros.
