# Profiles module

`apps/profiles` holds **application profiles**: presets that administrators define
(colour, avatar, notification style) and that each user picks from. The apps apply
the selected profile's look and notification behaviour.

A profile is **not** the user's personal data (name, time zone): that lives on
`accounts.User`. Each user has exactly one selected profile (`User.profile`).

`profiles` is the **lowest app**: it imports nothing from the project. Any write
that also changes users lives in `accounts.services` (see below).

## Model: `Profile`

| Field | Notes |
|-------|-------|
| `id` | UUID |
| `name` | Unique |
| `description` | Optional |
| `color` | Hex like `#728668` |
| `avatar` | Optional image (`media/profiles/avatars/`) |
| `notification_style` | `banner`, `alert`, `badge`, `silent` |
| `notification_sound` | `default`, `chime`, `bell`, `digital`, `soft`, `none` |
| `notification_vibrate` | Boolean |
| `position` | Order in which the apps list profiles |
| `is_active` | Inactive profiles cannot be selected |
| `is_default` | Given to new users, and to users of a deactivated profile |

**Rules, enforced by database constraints:**

- Exactly **one** default profile (`one_default_profile`).
- The default profile is always **active** (`default_profile_is_active`).
- A migration (`0002_create_default_profile`) creates the "Default" profile, so a
  fresh database always has one.

## Services (`services.py`)

| Function | What it does |
|----------|--------------|
| `create_profile(**fields)` | A new active, non-default profile. Only presentation fields are accepted. |
| `update_profile(profile, **fields)` | Edit presets. `is_active` / `is_default` cannot be set here. |
| `make_default(profile)` | Make it the only default (and active), atomically. |
| `activate_profile(profile)` | Make an inactive profile selectable again. |
| `delete_profile(profile)` | Only an **unused**, non-default profile. A profile with users must be deactivated instead. |
| `ensure_can_deactivate`, `mark_inactive` | Building blocks for `accounts.services.deactivate_profile`. Do not call `mark_inactive` alone: it would leave users on an inactive profile. |

**In `accounts.services`** (they write users): `select_profile(user, profile)` and
`deactivate_profile(profile)`. See [accounts](accounts.md#services-servicespy).

## Selectors (`selectors.py`)

| Function | Returns |
|----------|---------|
| `profiles_for(user)` | All profiles for profile managers (`profiles.change_profile`), active ones for everyone else |
| `default_profile()` | The default profile |

## API

| Method | Path | Who | Description |
|--------|------|-----|-------------|
| GET | `/profiles/` | signed in | Active profiles (all of them for managers). Not paginated. |
| GET | `/profiles/{id}/` | signed in | One profile |
| POST / PATCH / DELETE | `/profiles/`, `/profiles/{id}/` | `profiles.*_profile` | Manage profiles |
| POST | `/profiles/{id}/set-default/` | `profiles.change_profile` | Make it the default |
| POST | `/profiles/{id}/activate/` | `profiles.change_profile` | Selectable again |
| POST | `/profiles/{id}/select/` | signed in | *(served by accounts)* Switch to this profile |
| POST | `/profiles/{id}/deactivate/` | `profiles.change_profile` | *(served by accounts)* Deactivate and move its users to the default |

## Admin

- List with colour swatch, user count and inline `position` editing.
- `is_active` and `is_default` are read-only fields: they change only through the
  **Make default**, **Activate** and **Deactivate** actions, so users stay consistent.
- A profile can be deleted only when it is unused and not the default; bulk delete is disabled.
- Saving goes through `profiles.services`.

## Known limitations

- Profile names and descriptions are not translated (one value for ar/en/fr).
