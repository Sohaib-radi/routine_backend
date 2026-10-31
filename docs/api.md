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

## Idempotency keys

`POST /tracking/checkins/` and `POST /tracking/checkins/skip/` accept an optional
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
