# AI module

`apps/ai` is an **optional entry point** that turns free input into a meal the user
can confirm. Round 1: log a meal from **text** ("اكتب أكلك"). Photo and voice come
later and reuse the same pieces (provider interface, matching, drafts, confirm).

It may import `health`, `tracking` and `accounts`. **Nothing imports `ai`**: every
feature works without it, and turning it off never affects manual logging.

## CLAUDE.md rule 7: the AI proposes, the user confirms

| Rule | How it holds |
|------|--------------|
| Nothing is saved before the user confirms | The analysis writes only a `MealDraft` (and `AIUsage`). No `Meal`, `MealItem` or `Food` exists until `POST confirm/`. |
| AI has no write path of its own | Confirming calls `health.services.log_meal(..., source="agent")`, exactly like a manual meal (date window, snapshots, validation). |
| The AI only extracts | The model returns names, amounts, units and an estimate. The **backend** matches names to the Food table, and calories come from the table when there is a match. |
| Every saved meal records its source | `Meal.source = "agent"`. |

## The flow

```
1. POST /ai/meal-drafts/ {text}         → 202, draft "pending"; Celery task queued (never in the request)
2. Celery: provider call → validate → match foods → draft "ready" (or "failed")
3. GET  /ai/meal-drafts/{id}/           → the app polls until ready / failed
4. PATCH /ai/meal-drafts/{id}/ {items}  → the user's edits (switch food, quantity, portion, remove, add)
5. POST /ai/meal-drafts/{id}/confirm/   → health.services.log_meal(source=agent) → the meal
```

Example: "فطرت ساندوتش فول وساندوتش طعمية وكوباية شاي بسكر" →
`breakfast`; ful (matched, sandwich ≈ 150 g, grams estimated because ful has no
sandwich portion), ta'meya (matched, falafel), tea with sugar (matched, 1 glass).

## Statuses

```
pending → processing → ready → confirmed
                     ↘ failed
(any status except confirmed) → expired after 24 h
```

| Status | Meaning |
|--------|---------|
| `pending` | Created, waiting for the Celery task |
| `processing` | The provider is being called |
| `ready` | Proposal available; the user can edit and confirm |
| `failed` | `error.code` says why (below) |
| `confirmed` | The meal exists (`meal_id`) |
| `expired` | Not confirmed within `AI_DRAFT_TTL_HOURS` (24 h) |

A draft **never stays in `processing`**: every outcome of the task ends in `ready`
or `failed`, and a beat task (every 5 minutes) fails drafts stuck in `pending` /
`processing` for more than `AI_STUCK_AFTER_MINUTES` (5) with code `stuck`.

**Failure codes:** `invalid_output` (after one retry), `no_food_found`, `timeout`,
`provider_unavailable` (rate limit, 5xx, network), `provider_misconfigured` (no or
bad API key, unknown model), `refused` (safety), `ai_disabled`, `stuck`,
`internal_error`. Each has a user-facing `error.message`.

## The provider layer

| File | Role |
|------|------|
| `providers/base.py` | The interface: `structured(StructuredRequest, model, effort, timeout) → StructuredResponse` (JSON + tokens). Error classes with a `code`. `StructuredRequest.images` is reserved for meal_photo. |
| `providers/anthropic_provider.py` | Claude through the official `anthropic` SDK |
| `providers/fake.py` | Scripted responses for tests and local development; never touches the network |
| `providers/__init__.py` | `get_provider(name)` |

**Anthropic adapter:**
- **Structured outputs** (`output_config.format` with our JSON schema): the reply is JSON matching the schema.
- **No tools** are declared: the model can only answer.
- `effort` from the configuration (`low` by default); thinking stays adaptive.
- **Refusal fallback**: on models that support it (Opus 5.5, Opus 5, Fable 5.1,
  Sonnet 5.5), the server-side `fallbacks: "default"` option re-runs a
  safety-declined request on a fallback model inside the same call. Haiku 5.5 has
  no server-side fallback: a refusal fails the draft with `refused`.
- Timeouts: `AI_TIMEOUT_SECONDS` (20 s) per HTTP request, `AI_PROVIDER_MAX_RETRIES`
  (1) SDK retries for 429 / 5xx / network; the Celery task has a 60 s soft limit.

**Adding a provider** = one adapter class implementing `structured()`, mapping its
errors to the error classes, plus an entry in `get_provider`, `Provider` choices,
`AI_PROVIDER_KEY_ENV` and the price table.

## Extraction (`extraction.py`)

- `SYSTEM_PROMPT`, `SCHEMA` and `PROMPT_VERSION` (`meal-text-v1`, stored on drafts
  and usage). **Change the prompt or schema → bump the version.**
- Per item the model returns: `name` (the food only: "ساندوتش فول" → "فول" +
  unit `sandwich`), `quantity`, `unit` (our fixed list) or `grams`,
  `estimated_grams`, and `estimated_per_100g` (kcal, protein, carbs, fat); plus
  `meal_type` if the text says it.
- `validate()` checks the JSON against the schema **and** our rules (at most 20
  items, names ≤ 80 characters, quantity 0.25–50, grams 1–5000, kcal ≤ 900 and
  macros ≤ 100 g per 100 g). Invalid → **one retry**, then `invalid_output`. A
  call whose JSON breaks our rules is logged as a failed call.

### Prompt-injection measures

- The user's text is **data**: it is sent inside `<meal_text>` tags; the system
  prompt says never to follow instructions in it. Tags that could close the
  wrapper are stripped from the text.
- The model has **no tools** and can only return the schema; anything else is
  rejected by validation.
- The extracted names are only used as **search strings**; numbers are range-checked.
- Text limit: **500 characters**.

## Matching (`proposals.py` + `health.selectors.match_foods`)

For each extracted name, `match_foods(user, name)` returns up to 4 foods (global +
the user's own): the normal search first, then foods containing any word of the
name ranked by trigram similarity (≥ 0.3).

| Case | Proposed item |
|------|---------------|
| Match, and the food has the AI's unit as a portion | `portion` × `quantity` (e.g. 2 × falafel piece = 60 g) |
| Match, no such portion | `grams`: the user's weight if given, else the AI's estimate, flagged `grams_estimated` |
| No match | `status: "estimated"`: the AI's grams and nutrition per 100 g |

Matched nutrition always comes from the **Food table**; the AI's estimate is kept
on the item but unused. `candidates` = best match + up to 3 alternatives, so the
user can switch with one tap. The proposal's nutrition is a preview: confirming
recomputes everything through `log_meal`.

### Edits (PATCH)

The app sends the **full item list**. Each item is either
`{id?, food_id, portion [, quantity]}` / `{id?, food_id, grams}` (any food the user
can see), or an estimated item `{id?, name, grams, estimate: {kcal, protein_g,
carbs_g, fat_g}}`. Sending an existing `id` keeps its candidates. At least one item.
`date` (today/yesterday) and `meal_type` can change too.

### Confirming

One transaction:
1. Each **estimated** item becomes a **private food** with `source = "ai"`,
   `is_verified = false` (name in `name_ar` for Arabic text, else `name_en`). An
   existing AI food of the user with the same normalized name is **reused**, so
   "سوشي" twice gives one food. The user can find, edit and delete it like a custom food.
2. `health.services.log_meal(user, date, meal_type, items, source="agent")`.
3. The draft becomes `confirmed` with its `meal_id`. Confirming again returns the same meal.

Refused when the draft is not `ready`, has expired, or its date is no longer
today/yesterday.

### Meal type

The user's choice wins; otherwise the text's ("فطرت" → breakfast); otherwise a
guess from the user's **local** time: before 11:00 breakfast, before 16:00 lunch,
before 21:00 dinner, else snack (`meal_type_source`: `user` / `ai` / `time`).

## Configuration (admin: AI → Feature configuration)

One `AIFeatureConfig` row per feature (`meal_text` now; `meal_photo`, `voice` later):

| Field | Effect |
|-------|--------|
| `enabled` | Off → every endpoint of the feature answers **503 `ai_disabled`**. Manual logging is unaffected. |
| `provider`, `model`, `effort` | Used for the next analysis |
| `daily_limit` | Analyses per user per **local** day (default 20). Failed analyses (including `no_food_found`) count. |

- **No row → environment defaults** (`AI_ENABLED`, `AI_PROVIDER`, `AI_MODEL` =
  `claude-haiku-5-5`, `AI_EFFORT` = `low`, `AI_DAILY_LIMIT` = 20).
- **Cached in Redis**, invalidated on save/delete: a change applies to the next call
  immediately, without a database read per call.
- **API keys live in environment variables only** (`ANTHROPIC_API_KEY`). They are
  never stored or shown; the admin shows "Configured" or "Missing: set …".
- **History**: every change records who, when, and old → new values (the admin
  "History" button), plus `updated_by` on the row.
- **Tests always use the fake provider** (`AI_FORCE_PROVIDER = 'fake'` in test
  settings), whatever the admin says.

### Prices and cost

`ProviderPrice` (admin: AI → Prices): USD per million input / output tokens per
provider and model, seeded with Haiku 5.5 ($0.10 / $0.50), Sonnet 5.5 ($2 / $10),
Opus 5.5 ($4 / $20). Edits are cached the same way and recorded in history.
A model without a price gets an empty cost (and a warning when configured).

## Usage log (`AIUsage`)

One row **per provider call**, retries included: user, feature, provider, model,
prompt version, input and output tokens (billed even on failure when the provider
reports them), estimated cost, latency, success, error code, draft. Read-only in
the admin, with **Daily usage**: calls, failures, tokens and cost per day (UTC) and
model for the last 30 days.

## Daily limit

`POST meal-drafts/` beyond the limit → **429**:

```json
{"code": "daily_limit_exceeded", "detail": "At most 20 analyses per day.", "limit": 20,
 "reset_at": "2026-03-11T00:00:00+02:00"}
```

with a `Retry-After` header (seconds). `reset_at` is the user's next local midnight.
The check locks the user row, so two simultaneous requests cannot both pass.

## Services (`services.py`)

| Function | What it does |
|----------|--------------|
| `call_provider(user, cfg, request, prompt_version, draft=None)` | One provider call; always logs `AIUsage`; returns (response, usage) |
| `create_draft(user, text, date=None, meal_type=None)` | Checks enabled, text, date, daily limit; saves a pending draft; queues the task on commit |
| `analyze_draft(draft_id)` | The task body: provider → validate (1 retry) → proposal → ready / failed |
| `update_draft(user, draft, items=None, date=None, meal_type=None)` | The user's edits (ready only) |
| `confirm_draft(user, draft)` | AI foods + `log_meal(source=agent)` → the meal |
| `sweep_drafts()` | Fail stuck drafts, expire old ones |
| `guess_meal_type(user)`, `day_bounds(user)` | Local-time helpers |

## Selectors (`selectors.py`)

| Function | Returns |
|----------|---------|
| `drafts_for(user)` | The user's drafts |
| `draft_for(user, id)` | One draft (or `None`), with expiry applied on read |

## API

Under `/api/v1/ai/` (see [API reference](api.md#ai)):

| Method | Path | Description |
|--------|------|-------------|
| POST | `meal-drafts/` | `{text, date?, meal_type?}` → **202** with the draft. `Idempotency-Key`. 429 over the daily limit. |
| GET | `meal-drafts/{id}/` | Status, items, totals, error |
| PATCH | `meal-drafts/{id}/` | `{items?, date?, meal_type?}` (ready only) |
| POST | `meal-drafts/{id}/confirm/` | Creates the meal → **201** with it (200 if already confirmed). `Idempotency-Key`. |

Owner only: another user's draft → 404. Feature off → 503 `ai_disabled` on all four.

## Celery tasks (`tasks.py`)

| Task | When | What |
|------|------|------|
| `analyze_meal_draft(draft_id)` | On draft creation (after commit) | The analysis. Soft limit 60 s, hard 90 s. |
| `sweep_meal_drafts()` | Beat, every 5 minutes | Fail stuck drafts, expire drafts past their TTL |

## Checking quality by hand

```bash
python manage.py try_meal_text                       # built-in samples (ar/MSA/en/fr/mixed/injection/no food)
python manage.py try_meal_text "اتغديت كشري وبيبسي"   # your own phrases
python manage.py try_meal_text --model claude-opus-5-5 --effort medium   # compare models
python manage.py try_meal_text --user me@example.com  # also match that user's private foods
```

Uses the **configured** provider (real money with a real key), saves no drafts,
prints extraction, matches, tokens, cost and latency. Each call is logged in
`AIUsage` without a user.

## Admin

AI group in the sidebar: Feature configuration, Prices (editable, with value
history), Usage, Daily usage (read-only), Meal drafts (read-only).

## Settings (environment)

| Variable | Default | Notes |
|----------|---------|-------|
| `ANTHROPIC_API_KEY` | – | Required for the Anthropic provider. Never in the database. |
| `AI_ENABLED`, `AI_PROVIDER`, `AI_MODEL`, `AI_EFFORT`, `AI_DAILY_LIMIT` | `true`, `anthropic`, `claude-haiku-5-5`, `low`, `20` | Defaults when a feature has no admin row |
| `AI_TIMEOUT_SECONDS` | `20` | Per provider HTTP request |
| `AI_PROVIDER_MAX_RETRIES` | `1` | SDK retries (429, 5xx, network) |
| `AI_DRAFT_TTL_HOURS` | `24` | Draft expiry |
| `AI_STUCK_AFTER_MINUTES` | `5` | Pending/processing longer → failed `stuck` |
| `AI_FORCE_PROVIDER` | empty | Overrides every feature's provider (`fake` in tests) |

## Known limitations / next rounds

- Photo (`meal_photo`) and voice: same drafts, matching and confirm; the provider
  request gains images / a transcript.
- The daily-usage page groups by UTC day.
- Matching quality depends on the food dataset; until the real import, many
  dishes fall back to AI estimates.
- No streaming of progress: the app polls.
