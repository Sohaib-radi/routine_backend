# API reference

Base URL: `/api/v1/`. The complete, always up-to-date reference is the interactive
Swagger UI at `/api/v1/docs/` (schema at `/api/v1/schema/`). This page is an overview.

## Authentication

JWT via `djangorestframework-simplejwt`. Every endpoint requires authentication
except registration and token endpoints.

| Token   | Lifetime   |
|---------|------------|
| Access  | 15 minutes |
| Refresh | 30 days, rotated on every refresh; the old one is blacklisted |

```http
POST /api/v1/auth/token/
Content-Type: application/json

{"email": "sara@example.com", "password": "..."}
```

```json
{"access": "eyJ...", "refresh": "eyJ..."}
```

Then send `Authorization: Bearer <access>` on every request.

## Endpoints

### Auth and current user

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register/` | Create an account (`email`, `password`, `first_name`, `last_name`, `timezone`) |
| POST | `/auth/token/` | Log in: get access + refresh tokens |
| POST | `/auth/token/refresh/` | Get a new access token (and a new refresh token) |
| POST | `/auth/logout/` | Blacklist a refresh token |
| GET / PATCH / DELETE | `/me/` | Read or update your account (timezone, names, profile), or delete it |
| POST | `/me/change-password/` | Change password (`old_password`, `new_password`) |

### Profiles

| Method | Path | Description |
|--------|------|-------------|
| GET | `/profiles/` | List profiles (active ones; profile managers see all) |
| POST | `/profiles/{id}/select/` | Choose this profile for yourself |
| POST / PATCH / DELETE | `/profiles/`, `/profiles/{id}/` | Manage profiles (needs `profiles.*_profile` permissions) |
| POST | `/profiles/{id}/set-default/` | Make it the default profile (admin) |
| POST | `/profiles/{id}/activate/` | Make an inactive profile selectable again (admin) |
| POST | `/profiles/{id}/deactivate/` | Deactivate; its users move to the default profile (admin) |

### Administration

Gated by Django model permissions (including `view_*` for reads).

| Path | Description |
|------|-------------|
| `/users/` | User management (search by email/name) |
| `/roles/` | Roles (Django groups) and their permissions |
| `/permissions/` | Catalog of available permissions (read only) |

### Tracking

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tracking/today/` | What is due today, with today's check-in and streak |
| GET | `/tracking/trackables/` | List (`?archived=true`, `?domain=health`) |
| POST | `/tracking/trackables/` | Create (schedule defaults to daily) |
| GET / PATCH | `/tracking/trackables/{id}/` | Read or update |
| POST | `/tracking/trackables/{id}/archive/` | Archive (no DELETE: check-ins are history) |
| POST | `/tracking/trackables/{id}/unarchive/` | Unarchive |
| GET | `/tracking/trackables/{id}/checkins/` | History, newest first (`?from=&to=`; default last 30 days, max 366) |
| POST | `/tracking/checkins/` | Log (accumulates into the day's check-in) |
| POST | `/tracking/checkins/skip/` | Skip a day |
| PATCH | `/tracking/checkins/{id}/` | Correct the value |
| DELETE | `/tracking/checkins/{id}/` | Delete a check-in or skip |
| GET | `/tracking/pauses/` | List (`?active=true` running or planned, `false` over) |
| POST | `/tracking/pauses/` | Create a pause |
| GET | `/tracking/pauses/{id}/` | Read a pause |
| POST | `/tracking/pauses/{id}/end/` | Resume (`last_paused_day` optional); `204` if it was cancelled |

Trackables have a read-only `managed_by` field. When it is set (e.g. `health`), the
target, unit, kind, domain, dates and schedule cannot be changed here and the
trackable cannot be archived (`400`); change them through the owning module.

### Health

See [health](health.md) for the formulas and rules.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/health/plan/preview/` | Calculate the targets ("Calculate" button). Saves nothing. |
| GET | `/health/plan/` | Current plan, with `weight_kg` (latest) and `bmi`. `404` before the first plan. |
| POST | `/health/plan/` | Confirm the plan (`201`): new version effective today, today's weight, metric trackables updated |
| GET | `/health/plan/history/` | Every version, newest first |
| GET | `/health/weight/` | Weight history, newest first |
| POST | `/health/weight/` | Log a weight (`weight_kg`, optional `date`: last 30 days); same day replaces |
| GET | `/health/foods/?q=&limit=` | Search foods in ar/en/fr (min 2 chars; default 20, max 50) |
| GET | `/health/foods/{id}/` | A food with its portions (labels in ar/en/fr) |
| POST | `/health/foods/` | Create a private custom food (`201`) |
| PATCH / DELETE | `/health/foods/{id}/` | Edit / delete **your own** custom food (`404` for global foods) |
| GET | `/health/meals/?date=` | A day's meals (default today) with items and per-meal totals |
| POST | `/health/meals/` | Log a meal and its items (`201`). Supports `Idempotency-Key`. |
| PATCH / DELETE | `/health/meals/{id}/` | Edit `date`, `meal_type`, `note` / delete. Today and yesterday only. |
| POST | `/health/meals/{id}/items/` | Add an item (`201`). Supports `Idempotency-Key`. |
| PATCH / DELETE | `/health/meals/{id}/items/{item_id}/` | Edit / remove an item. The last item cannot be removed (`400`). |
| GET | `/health/nutrition/?date=` | Daily totals, targets from that day's plan, remaining |

List endpoints are paginated (50 per page, `?page=`), except profiles, permissions
and check-in history.

## Examples

Create a trackable: 8 glasses of water a day.

```http
POST /api/v1/tracking/trackables/
{
  "name": "Water",
  "domain": "health",
  "kind": "quantity",
  "target_value": "8",
  "unit": "glasses",
  "schedule": {"frequency": "daily"}
}
```

Log 2 glasses today (date defaults to the user's today):

```http
POST /api/v1/tracking/checkins/
Idempotency-Key: 3f1c9a52-6b0e-4a51-9a3f-2d8f1b7c4e10
{"trackable_id": "<uuid>", "value": "2"}
```

```json
{
  "id": "<uuid>", "trackable_id": "<uuid>", "date": "2026-10-31",
  "status": "partial", "value": "2.00", "note": "", "source": "manual",
  "created_at": "...", "updated_at": "..."
}
```

Pause everything for a vacation:

```http
POST /api/v1/tracking/pauses/
{"trackable_id": null, "start_date": "2026-11-10", "end_date": "2026-11-20", "reason": "vacation"}
```

Preview or confirm a plan (auto mode):

```http
POST /api/v1/health/plan/preview/
{"goal": "cut", "sex": "male", "weight_kg": "80", "height_cm": "178", "age": 24, "activity_level": "light"}
```

```json
{"calories": 2100, "protein_g": 160, "carbs_g": 235, "fat_g": 58, "water_ml": 4000,
 "steps": 8000, "activity_minutes": 30, "sleep_hours": "8.0", "calorie_floor_applied": false}
```

Manual mode adds `"mode": "manual", "calories": 2000, "protein_g": 150`; manual water
adds `"water_mode": "manual", "water_ml": 3000`. When `calorie_floor_applied` is
`true`, the calories were raised to the safety floor: show the user a message.

Log a meal (portion or grams per item; `date` defaults to today):

```http
POST /api/v1/health/meals/
Idempotency-Key: 8b1d2f4e-...
{
  "meal_type": "lunch",
  "items": [
    {"food_id": "<koshari uuid>", "portion": "plate"},
    {"food_id": "<egg uuid>", "portion": "piece", "quantity": 2}
  ]
}
```

An item can also give grams instead of a portion: `{"food_id": "<uuid>", "grams": 50}`.

Daily nutrition after that meal (plan of the reference case):

```json
{"date": "2026-03-10",
 "totals":    {"kcal": "680.0",  "protein_g": "30.5",  "carbs_g": "95.6",  "fat_g": "19.8"},
 "targets":   {"kcal": "2100.0", "protein_g": "160.0", "carbs_g": "235.0", "fat_g": "58.0"},
 "remaining": {"kcal": "1420.0", "protein_g": "129.5", "carbs_g": "139.4", "fat_g": "38.2"}}
```

## Idempotency keys

`POST /tracking/checkins/`, `POST /tracking/checkins/skip/`, `POST /health/meals/`
and `POST /health/meals/{id}/items/` accept an optional
`Idempotency-Key` header (for example a UUID generated per user action). Keys are
scoped per user and kept 24 hours in Redis.

| Situation | Result |
|-----------|--------|
| No header | Request runs normally |
| Same key, same request | First response replayed, with header `Idempotent-Replayed: true` |
| Same key, different request | `422` |
| Same key while the first request is still running | `409` |
| First request failed | Nothing stored; the client may retry |

So "+2 glasses" retried on a flaky network still counts as +2.

## Errors

Validation errors return `400` with field messages:

```json
{"date": ["Check-ins can only be recorded for today or yesterday."]}
```

Objects that belong to other users are reported as not existing (`404`, or "does not
exist" for an ID in the body).
