# Development guide

## Local setup

See the [README](../README.md#getting-started) for the step-by-step setup.
Services run in Docker (`docker compose up -d`); Django and Celery run on your machine.

## Environment variables

Copy `.env.example` to `.env`. Never commit `.env`; it is in `.gitignore`.

| Variable | Example | Notes |
|----------|---------|-------|
| `SECRET_KEY` | `change-me` | Required |
| `DEBUG` | `true` | Dev only |
| `DATABASE_URL` | `postgres://routine:routine@localhost:5432/routine` | |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `CACHE_URL` | `redis://localhost:6379/1` | Django cache (idempotency keys) |
| `ALLOWED_HOSTS` | `api.example.com` | Required in production |
| `CSRF_TRUSTED_ORIGINS` | `https://api.example.com` | Production |
| `MEDIA_ROOT` | | Optional, uploaded avatars |
| `DB_CONN_MAX_AGE` | `60` | Optional |
| `LOG_LEVEL` | `INFO` | Production |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | AI provider key. **Environment only**, never in the database or admin. |
| `AI_ENABLED`, `AI_PROVIDER`, `AI_MODEL`, `AI_EFFORT`, `AI_DAILY_LIMIT` | `true`, `anthropic`, `claude-haiku-5-5`, `low`, `20` | AI defaults when a feature has no row in the admin (AI → Feature configuration) |
| `AI_TIMEOUT_SECONDS`, `AI_PROVIDER_MAX_RETRIES`, `AI_DRAFT_TTL_HOURS`, `AI_STUCK_AFTER_MINUTES` | `20`, `1`, `24`, `5` | See [ai](ai.md#settings-environment) |
| `AI_FORCE_PROVIDER` | empty | Forces a provider for every feature; tests set `fake` |

Choose the settings module with `DJANGO_SETTINGS_MODULE`
(`config.settings.dev` by default, `config.settings.prod` in production).

## Tests

```bash
pytest                         # whole suite
pytest apps/tracking           # one app
pytest -k pause                # by name
```

The suite uses `config.settings.test` and needs PostgreSQL and Redis running.
Tests live next to the code (`apps/<app>/tests/`) plus cross-app tests in `tests/`.

## Conventions

- Business logic in `services.py` (writes) and `selectors.py` (reads); views and serializers stay thin.
- Never write another app's models directly; call its services.
- Never use `date.today()` or a UTC date for user-facing dates: use `user_today(user)`.
- Every query is scoped to the requesting user.
- UUID primary keys on models exposed through the API.
- Heavy work runs in Celery, queued with `transaction.on_commit`.
- Never break an existing `/api/v1/` endpoint; keep the OpenAPI schema accurate.
- Every new service function gets tests.

## Admin panel

`/admin/` uses Django Unfold, with a custom theme (`static/admin_theme/`) and
language switcher (English, Arabic, French). Check-ins, pauses and streak caches are
read only there, so the admin cannot bypass the tracking rules. Streak caches can be
recomputed from an admin action. Profiles are activated, deactivated and made
default only through dedicated actions so users stay consistent.

**Help inside the admin.** Staff guides live in `docs/admin-guide/<app>.md` and are
shown in the admin at `/admin/help/<app>/` (rendered with `markdown`, staff only).
To give an admin page a "How to use this page" button:

```python
from config.admin_help import HelpLinkMixin

class FoodAdmin(HelpLinkMixin, ModelAdmin):
    help_guide, help_section = 'health', '5-foods'  # guide file, heading anchor
```

A new guide also needs an entry in `config.admin_help.GUIDES` and a "How to use"
item in the sidebar. Tests check that every button's section exists and that every
link inside a guide points to one of its headings.

The sidebar is an explicit list in `config/unfold.py` (`SIDEBAR.navigation`, with
`show_all_applications` off). **When you register a new admin, add it there**, with an
icon and its `view_*` permission; a test fails if a registered admin has no sidebar link.

## Production notes

- Use `config.settings.prod`: it requires `SECRET_KEY` and `ALLOWED_HOSTS`, forces
  HTTPS redirects, HSTS and secure cookies, and logs to the console.
- Run `python manage.py collectstatic` and serve `staticfiles/`.
- Run at least one Celery worker and **exactly one** Celery beat process.
- The health migrations run `CREATE EXTENSION pg_trgm` (food search). It is a
  trusted extension on PostgreSQL 13+, so the database owner can create it; on a
  managed database where that is blocked, create it once as an admin before migrating.
- Set `ANTHROPIC_API_KEY` for the AI features; configure model and limits in the
  admin (AI → Feature configuration). Check quality with `python manage.py try_meal_text`.
- Load the food dataset with `python manage.py import_foods <file.csv>`; never run
  `seed_test_foods` in production (approximate test values).
