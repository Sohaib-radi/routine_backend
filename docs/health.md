# Health module

`apps/health` holds the user's **plan and targets** (the "خطتك وسعراتك" screen),
their **body weight**, and the link to the four **metric trackables** (water, sleep,
steps, activity) it creates in `tracking`.

It may import `tracking` and `accounts`; they never import `health`.

> Status: Round 1 of 4. Meals, the food table, the health dashboard, the health
> score and AI come in later rounds. When they arrive, **meals are the source of
> truth for calories and macros**: daily totals are computed from meals, not stored
> as check-ins.

## The plan screen

1. The user enters goal (cut / maintain / bulk), sex, weight, height, age, activity level.
2. **Calculate** → `POST /plan/preview/` returns the targets. Nothing is saved.
   Or the user switches to **manual** and sets calories and protein themselves.
   Water is auto (computed) or manual (+/- buttons).
3. **Confirm** → `POST /plan/` saves a new version of the plan, records today's
   weight and updates the four metric trackables.

## Models

| Model | Purpose |
|-------|---------|
| `HealthPlan` | One version of the plan: inputs + resulting targets. Unique on `(user, effective_from)`. |
| `WeightEntry` | Body weight on one local date. Unique on `(user, date)`. |
| `HealthMetric` | The link between a metric (`water`, `sleep`, `steps`, `activity`) and its trackable. Unique on `(user, metric)`. |

### HealthPlan

| Group | Fields |
|-------|--------|
| Version | `effective_from` (user's local date) |
| Inputs | `goal`, `sex`, `birth_year`, `height_cm`, `activity_level`, `mode` (auto/manual), `water_mode` (auto/manual) |
| Targets | `calories`, `protein_g`, `carbs_g`, `fat_g`, `water_ml`, `steps` (8000), `activity_minutes` (30), `sleep_hours` (8) |
| Flag | `calorie_floor_applied`: calories were raised to the safety floor |

- **History, never rewritten.** Each confirm creates a row with `effective_from =
  today`. A second confirm on the same day updates that day's row. The plan that
  applied on a date is the latest row with `effective_from <= date`
  (`selectors.plan_for(user, date)`), so changing the plan never changes what past
  dates show.
- **Birth year, not age.** The UI sends an age; we store `today.year − age` and
  give back an age computed from it. It can be one year high before the
  birthday; good enough for a calorie formula.
- **Weight is not on the plan.** It lives in `WeightEntry`.

### WeightEntry and BMI

- One weight per day; logging again that day replaces it.
- Accepted for the **last 30 days** (user's local date), never a future date. Weight
  has no streak, so the window is wider than the check-in window (today/yesterday).
- Confirming a plan records today's weight.
- **BMI is computed, never stored**: latest weight ÷ (current plan's height in m)², one decimal.

## Calculations (`calculations.py`)

Pure functions, no database. **Every constant lives at the top of the file**: change
the formula there and nowhere else.

| Step | Rule |
|------|------|
| BMR | Mifflin-St Jeor: `10 × kg + 6.25 × cm − 5 × age + s`, with s = +5 (men), −161 (women) |
| TDEE | BMR × activity factor: sedentary 1.2, light 1.375, moderate 1.55, active 1.725, very active 1.9 |
| Goal | cut ×0.85 (−15%), maintain ×1.00, bulk ×1.10 (+10%) |
| Rounding | Calories to the nearest 10 |
| Safety floor | Never below **1500 kcal (men) / 1200 kcal (women)**, auto and manual. If the result is below, the floor is used and `calorie_floor_applied = true` so the app can show a message. |
| Protein | 2 g per kg (auto) |
| Fat | 25% of calories ÷ 9, rounded |
| Carbs | (calories − protein × 4 − fat × 9) ÷ 4, rounded, using the *rounded* fat grams |
| Water | 50 ml per kg, rounded to the nearest 250 ml (auto) |

**Manual mode:** the user sets calories and protein; fat and carbs are derived the
same way and the floor still applies. Protein above **75% of calories** is refused
(fat takes 25%, so carbs would be negative).

**Reference case** (from the design): male, 80 kg, 178 cm, 24 years, light activity, cut:

```
BMR  = 800 + 1112.5 − 120 + 5          = 1797.5
TDEE = 1797.5 × 1.375                  = 2471.6
Cut  = 2471.6 × 0.85 = 2100.8          → 2100 kcal
Protein = 2 × 80                       = 160 g   (640 kcal)
Fat     = 2100 × 25% / 9 = 58.3        → 58 g    (522 kcal)
Carbs   = (2100 − 640 − 522) / 4 = 234.5 → 235 g
Water   = 50 × 80                      = 4000 ml
```

The original design showed 2130 kcal; no standard adjustment gives that, so the
standard −15% was chosen (2100).

### Accepted inputs

| Input | Range |
|-------|-------|
| Age | 18–100 (deficit plans are not appropriate for minors) |
| Weight | 30–300 kg |
| Height | 120–230 cm |
| Manual calories | 1200–6000 kcal |
| Manual protein | 1–500 g, and at most 75% of calories |
| Manual water | 500–10,000 ml |

## Metric trackables

On confirm, health creates (first time) or updates the four trackables in
`tracking`, **only through `tracking.services`**:

| Metric | Kind | Unit | Target from |
|--------|------|------|-------------|
| water | quantity | ml | `water_ml` |
| sleep | quantity | h | `sleep_hours` |
| steps | quantity | steps | `steps` |
| activity | duration | min | `activity_minutes` |

All are `domain=health`, daily, starting the day of the first confirm.

- **Exactly one per metric per user.** `HealthMetric` is the link; health never
  finds a trackable by its name (the user may rename it, or have their own "Water"
  habit, which health leaves alone). `confirm_plan` locks the user row so two
  confirms at the same moment cannot create duplicates.
- **The plan owns the targets.** The trackables are created with
  `managed_by='health'`. tracking then refuses changes to their target, unit, kind,
  domain, dates and schedule, and refuses archiving, unless the caller passes the
  same `managed_by` (see [tracking: managed trackables](tracking.md#managed-trackables)).
  Users can still rename them, change icon/colour and log check-ins normally.

## Services

| Function | What it does |
|----------|--------------|
| `preview_plan(inputs)` | Calculates targets. Saves nothing. |
| `confirm_plan(user, inputs)` | One transaction: calculate, save today's plan version, record today's weight, sync the metric trackables. |
| `log_weight(user, date, weight_kg)` | Save or replace the weight for that date (last 30 days). |

## Selectors

| Function | Returns |
|----------|---------|
| `plan_for(user, date)` | The plan that applied on that date, or `None` |
| `current_plan(user)` | `plan_for(user, today)` |
| `plan_history(user)` | Every version, newest first |
| `weights_for(user)`, `latest_weight(user, on=None)` | Weight entries |
| `bmi(weight_kg, height_cm)`, `current_bmi(user)` | Computed BMI |
| `metric_trackables(user)` | `{metric: trackable}` |

## API

Under `/api/v1/health/` (see [API reference](api.md#health)):

| Method | Path | Description |
|--------|------|-------------|
| POST | `plan/preview/` | Calculate targets; saves nothing |
| GET | `plan/` | Current plan with latest weight and BMI (`404` if none yet) |
| POST | `plan/` | Confirm: new version + weight + trackables (`201`) |
| GET | `plan/history/` | All versions, newest first (paginated) |
| GET | `weight/` | Weight history, newest first (paginated) |
| POST | `weight/` | Log a weight (`date` defaults to today) |

## Admin

Plans, weights and metric links are read-only in the admin: plans are history, and
every write goes through `health.services`, which keeps the trackables in sync.

## Known limitations / next rounds

- Meals and food table (Round 2+), health dashboard and score, AI logging.
- Steps, activity and sleep targets use the defaults (8000, 30 min, 8 h); they are
  not editable on the plan screen yet.
- Age is derived from the birth year only.
