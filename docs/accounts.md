# Accounts module

`apps/accounts` holds the **user**: email login, JWT authentication, the user's
personal settings (time zone, selected profile), roles and permissions, and staff
user management.

It imports `profiles` (never the reverse). It is imported by `tracking` and `health`.

## Model: `User`

A custom user model (`AUTH_USER_MODEL = 'accounts.User'`) that logs in with
**email** instead of a username.

| Field | Notes |
|-------|-------|
| `id` | UUID (exposed through the API) |
| `email` | Unique; the login. Normalized on creation. |
| `first_name`, `last_name` | Optional |
| `timezone` | IANA name (e.g. `Africa/Cairo`), default `UTC`. **Defines the user's "today"** for every module (`tracking.dates.user_today`). Validated against the zones Python knows. |
| `profile` | FK to `profiles.Profile`, `on_delete=PROTECT`. Always an active profile. New users get the default profile. |
| `is_staff`, `is_superuser`, `groups`, `user_permissions` | Django's permission system. `groups` are shown as **roles**. |

Why the time zone matters: a user in Cairo who logs water at 01:30 Tuesday (Monday
23:30 UTC) logs it for **Tuesday**. Every date in the app is the user's local date.

## Authentication

JWT (`djangorestframework-simplejwt`), usable by web and mobile:

- Access token: 15 minutes. Refresh token: 30 days, **rotated** on every refresh;
  the old refresh token is blacklisted.
- Logout = blacklist the refresh token (`POST /auth/logout/`).
- Session authentication also works (admin, browsable API).
- Outstanding and blacklisted tokens are visible in the admin (Security group).

## Services (`services.py`)

**The user's own account**

| Function | What it does |
|----------|--------------|
| `register_user(*, email, password, **fields)` | Self sign-up. Validates the password (Django's validators) and the time zone. Starts on the default profile. |
| `update_me(user, *, profile=None, **fields)` | The user edits `first_name`, `last_name`, `timezone`, and may switch profile. Other fields are refused. |
| `change_password(user, *, old_password, new_password)` | Checks the old password, validates the new one. |
| `delete_account(user)` | Deletes the user. **Everything they own cascades**: trackables, check-ins, pauses, health plans, weights, meals, custom foods. |

**Staff user management**

| Function | What it does |
|----------|--------------|
| `create_user(*, email, password=None, groups=(), user_permissions=(), **fields)` | Staff create an account. Only active profiles. |
| `update_user(user, *, password=None, groups=None, user_permissions=None, **fields)` | Staff edit an account; a profile change goes through `select_profile`. |

**Profiles that touch users** (here because `profiles` cannot import `accounts`)

| Function | What it does |
|----------|--------------|
| `select_profile(user, profile)` | Switch the user to an **active** profile. Locks the profile row, so it cannot race with a deactivation. |
| `deactivate_profile(profile)` | Make a profile unselectable **and move its users to the default profile**, in one transaction. Refuses the default profile. Returns the number of users moved. |

Example: staff deactivate the "Night owl" profile. Its 300 users are moved to the
default profile in the same transaction, so no user is ever left on an inactive profile.

## API

| Method | Path | Who | Description |
|--------|------|-----|-------------|
| POST | `/auth/register/` | anyone | Sign up (`email`, `password`, optional names and `timezone`) |
| POST | `/auth/token/` | anyone | Log in → `access` + `refresh` |
| POST | `/auth/token/refresh/` | anyone | New access (and refresh) token |
| POST | `/auth/logout/` | signed in | Blacklist a refresh token |
| GET / PATCH / DELETE | `/me/` | signed in | Your account. PATCH: `first_name`, `last_name`, `timezone`, `profile_id`. DELETE removes the account and its data. |
| POST | `/me/change-password/` | signed in | `old_password`, `new_password` |
| POST | `/profiles/{id}/select/` | signed in | Switch to that profile |
| POST | `/profiles/{id}/deactivate/` | `profiles.change_profile` | Deactivate and move its users |
| CRUD | `/users/` | `accounts.*_user` | Staff user management (search by email/name) |
| CRUD | `/roles/` | `auth.*_group` | Roles and their permissions |
| GET | `/permissions/` | `auth.view_permission` | Permission catalog, for role editors |

`GET /me/` returns the user's `groups` (role names) and the full list of
`permissions`, so the apps can show or hide staff features.

Staff endpoints use `StrictDjangoModelPermissions`: even **reading** requires the
`view_*` permission (Django's default only checks writes).

## Admin

- Users: Unfold admin with email login forms; the profile list only offers active profiles.
- Roles (Django groups) and JWT tokens are re-registered with Unfold.
- Profiles: the admin defined in `profiles`, plus the **Deactivate** action (it moves
  users, so it is added here).

## Known limitations

- No email verification or password reset by email yet.
- No social login.
