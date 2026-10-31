# Routine Backend

REST API for a routine and habit tracking app built for individual users. It
serves a Next.js web app and a React Native mobile app; both clients live in
separate repositories and are generated from this API's OpenAPI schema.

Users create **trackables** (pray Fajr, drink 8 glasses of water, walk 30 minutes),
give them a **schedule**, record a **check-in** each day, **pause** them for
vacations or sickness, and see their **streaks** and 30-day completion rate.

## Features

- **Accounts**: email login, JWT auth (access + rotating refresh tokens, logout via
  blacklist), personal time zone, change password, delete account.
- **Profiles**: admin-defined presets (colour, avatar, notification style and sound)
  that each user picks from.
- **Tracking engine**:
  - Trackables of three kinds: `boolean` (done / not done), `quantity`, `duration`.
  - Schedules: daily, specific weekdays, every N days, X times per week/month, or an RFC 5545 RRULE.
  - One check-in per trackable per day; repeated logs add up (2 glasses + 1 = 3).
  - Check-ins can be logged, skipped, corrected or deleted for **today and yesterday** only.
  - Pauses for one trackable or for everything, open-ended or planned ahead, without rewriting history.
  - Streaks (current, longest, 30-day completion rate) recomputed in the background by Celery,
    including an hourly job for users whose local midnight just passed.
  - "Today" screen: what is due today, in the user's own time zone.
- **Idempotency keys** on check-in endpoints, so a retry on a flaky mobile network never counts twice.
- **Admin panel** (Django Unfold) in English, Arabic and French, with roles and permissions.
- **API docs**: interactive Swagger UI and an OpenAPI 3 schema.

## Tech stack

| Layer        | Choice                                           |
|--------------|--------------------------------------------------|
| Language     | Python 3.14                                      |
| Framework    | Django 6.1, Django REST Framework 3.18           |
| Auth         | djangorestframework-simplejwt                    |
| Database     | PostgreSQL 17                                    |
| Cache/broker | Redis 7                                          |
| Background   | Celery 5 (worker + beat)                         |
| API schema   | drf-spectacular (OpenAPI 3)                      |
| Admin        | django-unfold                                    |
| Tests        | pytest, pytest-django, factory_boy               |

## Getting started

Requirements: Python 3.14, Docker (for PostgreSQL and Redis).

```bash
git clone https://github.com/Sohaib-radi/routine_backend.git
cd routine_backend

python -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt

cp .env.example .env              # then set SECRET_KEY
docker compose up -d              # PostgreSQL + Redis

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

In two more terminals, start the background workers:

```bash
celery -A config worker -l info
celery -A config beat -l info     # hourly streak recompute
```

Then open:

| URL                                    | What                     |
|----------------------------------------|--------------------------|
| http://localhost:8000/admin/           | Admin panel              |
| http://localhost:8000/api/v1/docs/     | Swagger UI               |
| http://localhost:8000/api/v1/schema/   | OpenAPI schema (YAML)    |

## Running the tests

```bash
pytest
```

The tests need the PostgreSQL and Redis containers to be running.

## Project layout

```
apps/
  profiles/   admin-defined presets (lowest layer)
  accounts/   users, auth, roles, profile selection
  tracking/   trackables, schedules, check-ins, pauses, streaks
config/       settings (base/dev/test/prod), URLs, Celery, idempotency, error handler
docs/         project documentation
tests/        cross-app tests
```

## Documentation

- [Architecture](docs/architecture.md): apps, dependency rules, design principles
- [Tracking engine](docs/tracking.md): schedules, check-ins, pauses, streaks and their rules
- [API reference](docs/api.md): endpoints, authentication, examples
- [Development guide](docs/development.md): configuration, testing, conventions, deployment notes
- [Changelog](docs/changelog.md): what has been built so far

## Roadmap

- Domain apps on top of the tracking engine: `health` (meals, water, workouts),
  `spiritual` (prayers, Quran reading), `tasks`.
- AI entry point (voice, photo, text) that proposes actions for the user to confirm.
- Reminders and push notifications.
- Streaks for "X times per week/month" schedules.
