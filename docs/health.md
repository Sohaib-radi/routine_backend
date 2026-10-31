# Health module

`apps/health` holds the user's **plan and targets** (the "خطتك وسعراتك" screen),
their **body weight**, the link to the four **metric trackables** (water, sleep,
steps, activity) it creates in `tracking`, the **food database**, and the user's
**meals** (the "أكلك النهاردة" section, manual logging with "+ سجّل يدوي").

It may import `tracking` and `accounts`; they never import `health`.

> Status: Round 2 of 4 (plan + foods and manual meals). The health dashboard, the
> health score and AI (text/photo logging) come in later rounds.
> **Meals are the source of truth for calories and macros**: daily totals are
> computed from the meal items, never stored as check-ins.

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

## Food database

| Model | Purpose |
|-------|---------|
| `Food` | A food: names, aliases, category, nutrition **per 100 g**, source. |
| `FoodPortion` | A named portion and its weight: koshari `plate` = 350 g, egg `piece` = 50 g. Unique per `(food, unit)`. |

### Food

| Field | Notes |
|-------|-------|
| `name_ar`, `name_en`, `name_fr` | At least one is required. |
| `aliases` | Other names in any language: طعمية / فلافل / ta'meya are the same food. |
| `category` | dish, drink, fruit, vegetable, bread, grain, protein, dairy, sweet, snack, sauce, other |
| `kcal`, `protein_g`, `carbs_g`, `fat_g` | Per 100 g. kcal 0–900, each macro 0–100 g, protein + carbs + fat ≤ 100 g. |
| `source` | `nni`, `usda`, `off` (Open Food Facts), `custom` |
| `is_verified` | Checked against a trusted source. Test foods are `false`. |
| `is_active` | `false` hides the food from search and logging; past meals are unaffected. |
| `user` | **NULL = global food** (managed by staff). Set = a **private custom food**, visible only to its owner (`source` must then be `custom`). |
| `key` | Identifier in the source dataset. Global foods are unique on `(source, key)`: the import matches on it. |
| `search_text` | Normalized names + aliases, rebuilt on every save (admin included). Never edited directly. |

### Portion units

A fixed list in code (`PortionUnit` and `PORTION_UNIT_LABELS` in `models.py`), each
with an Arabic, English and French label, so translations stay consistent:

`plate`, `small_plate`, `bowl`, `piece`, `sandwich`, `slice`, `loaf`, `cup`, `glass`,
`can`, `bottle`, `tbsp`, `handful`.

Adding a unit = one entry in both places (and a migration for the choices). A
portion weighs 1–5000 g. The user can always enter grams directly instead.

### Search

`selectors.search_foods(user, q, limit=20)` searches names and aliases in all three
languages, across global foods and the user's own custom foods.

- **Normalization** (`text.normalize`), applied to the stored text *and* the query:
  - Arabic: أ إ آ → ا, ة → ه, ى → ي, tashkeel and tatweel (ـ) removed. "طعميه" finds "طعمية".
  - Latin: lowercase, accents removed (é → e), apostrophes and hyphens removed. "tameya" finds "ta'meya".
  - Other letters (ؤ, ئ...) are kept as they are.
- **Speed**: PostgreSQL `pg_trgm`. A GIN trigram index on `search_text` serves the
  `LIKE '%q%'` filter, so search stays fast with a large dataset.
- **Order**: exact name/alias match first, then names starting with the query, then trigram similarity.
- The query needs at least 2 characters. Default 20 results, at most 50.

### Custom foods

Users create private foods (`POST foods/`), and can edit (`PATCH`) and delete
(`DELETE`) **their own** custom foods only; global foods are never editable through
the API (`404`). `portions`, when sent on PATCH, replaces all portions. Past meals
are never affected: they keep their snapshot.

### Importing foods

```bash
python manage.py import_foods path/to/foods.csv            # create or update
python manage.py import_foods path/to/foods.csv --dry-run  # validate and count only
python manage.py seed_test_foods                           # ~20 TEST foods (is_verified=false)
```

- **Idempotent**: foods are matched on `(source, key)`, portions on `unit`. Running
  the same file twice changes nothing; a changed row is updated in place.
- Portions not in the file anymore are removed from that food. Foods missing from
  the file are left alone. `is_active` is never changed by an import (a food staff
  hid stays hidden). Users' custom foods are never touched.
- **All or nothing**: one invalid row and nothing is written; every error is printed
  with its line number.
- `seed_test_foods` loads `apps/health/data/test_foods.csv` through the same import.
  Its values are approximate, for development and demos only. The real dataset
  will come later through `import_foods`.

### Food CSV format

UTF-8, comma-separated, one header row. Unknown columns are refused.

| Column | Required | Example | Notes |
|--------|----------|---------|-------|
| `key` | yes | `koshari` | Unique per source; lowercase, digits, `-`, `_` |
| `source` | yes | `nni` | `nni`, `usda`, `off`, `custom` |
| `category` | no | `dish` | Default `other` |
| `name_ar` | one of the three | `كشري` | |
| `name_en` | one of the three | `Koshari` | |
| `name_fr` | one of the three | `Koshari` | |
| `aliases` | no | `كشرى\|kushari` | Separated by `\|` |
| `kcal` | yes | `150` | Per 100 g |
| `protein_g` | yes | `5` | Per 100 g |
| `carbs_g` | yes | `27` | Per 100 g |
| `fat_g` | yes | `2.5` | Per 100 g |
| `portions` | no | `plate:350\|small_plate:250` | `unit:grams`, separated by `\|`; units from the list above |
| `is_verified` | no | `true` | `true`/`false` (also `1`/`0`, `yes`/`no`). Default `false` |

```csv
key,source,category,name_ar,name_en,name_fr,aliases,kcal,protein_g,carbs_g,fat_g,portions,is_verified
koshari,nni,dish,كشري,Koshari,Koshari,كشرى|kushari,150,5,27,2.5,plate:350|small_plate:250,true
```

## Meals

| Model | Purpose |
|-------|---------|
| `Meal` | `user`, `date` (local), `meal_type` (breakfast / lunch / dinner / snack), `note`, `source` (manual now; voice/photo/agent later). Several meals of the same type per day are allowed. |
| `MealItem` | One food in a meal: `food` (SET_NULL), `portion` (SET_NULL), `quantity` + `portion_unit` **or** `grams`, and the snapshot. |

### The snapshot rule

Each `MealItem` stores a **copy** of the food's names (`name_ar`, `name_en`,
`name_fr`), the portion unit, the grams, and the kcal, protein, carbs and fat for
those grams, computed when the item is logged. **Totals are always computed from
the snapshots.** Editing a food, deleting it, or changing its portions never changes
a past meal.

Example: a plate of koshari (350 g) is logged at 150 kcal/100 g → 525 kcal. Staff
later correct koshari to 160 kcal/100 g: that meal still shows 525 kcal; new meals
use 160.

Editing an item (quantity, portion, grams or food) recomputes its snapshot from the
food *as it is now*. If the food was deleted, the snapshot is rescaled to the new
grams (and only grams can be entered).

### Portions vs grams

- Portion: `{"food_id", "portion": "plate", "quantity": 2}` → grams = 2 × 350 = 700.
  `quantity` defaults to 1; 0.25–50.
- Grams: `{"food_id", "grams": 200}`; 1–5000 g. Not both.
- Nutrition = per-100 g value × grams / 100, rounded to 0.1 (half up).

### Rules

- **Date window**: meals are logged, edited and deleted for **today and yesterday**
  only (user's local date), like check-ins. Moving a meal checks both dates. Older
  meals are frozen.
- A meal has 1–30 items. Removing the last item is refused: delete the meal instead.
- Only active global foods and the user's own custom foods can be logged.
- Every service checks ownership; another user's meal or item is "unknown" (`404` in the API).

### Daily nutrition

`selectors.daily_nutrition(user, date)` is what the calories ring and macro cards need:

- `totals`: sum of that day's item snapshots (kcal, protein, carbs, fat),
- `targets`: from `plan_for(user, date)`, the plan that applied **that day** (a plan
  changed later does not change past days); `null` before the first plan,
- `remaining`: targets − totals (negative when over).

`meals_for` and `daily_nutrition` use **2 queries each**, whatever the number of meals and items.

## Services

| Function | What it does |
|----------|--------------|
| `preview_plan(inputs)` | Calculates targets. Saves nothing. |
| `confirm_plan(user, inputs)` | One transaction: calculate, save today's plan version, record today's weight, sync the metric trackables. |
| `log_weight(user, date, weight_kg)` | Save or replace the weight for that date (last 30 days). |
| `create_custom_food(user, *, portions, **fields)` | A private food. |
| `update_custom_food(user, food, *, portions=None, **fields)` | The user's own custom foods only. |
| `delete_custom_food(user, food)` | The user's own custom foods only. |
| `import_foods(rows, *, dry_run)` | The CSV import (used by the commands). |
| `log_meal(user, *, date, meal_type, items, note, source)` | A meal and its items, one transaction. |
| `update_meal(user, meal, **fields)` | `date`, `meal_type`, `note`. |
| `delete_meal(user, meal)` | |
| `add_item(user, meal, spec)`, `update_item(user, item, **changes)`, `remove_item(user, item)` | Item changes recompute the snapshot. They lock the meal row, so the item limit and the last-item rule hold under concurrent requests. |

## Selectors

| Function | Returns |
|----------|---------|
| `plan_for(user, date)` | The plan that applied on that date, or `None` |
| `current_plan(user)` | `plan_for(user, today)` |
| `plan_history(user)` | Every version, newest first |
| `weights_for(user)`, `latest_weight(user, on=None)` | Weight entries |
| `bmi(weight_kg, height_cm)`, `current_bmi(user)` | Computed BMI |
| `metric_trackables(user)` | `{metric: trackable}` |
| `foods_for(user)`, `custom_foods_for(user)` | Visible foods / the user's custom foods |
| `search_foods(user, q, limit)` | Ranked search results |
| `meals_for(user, date)`, `meal_totals(meal)` | A day's meals with items; one meal's totals |
| `daily_nutrition(user, date)` | Totals, targets, remaining |

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
| GET | `foods/?q=&limit=` | Search foods |
| GET | `foods/{id}/` | A food with its portions |
| POST | `foods/` | Create a custom food (`201`) |
| PATCH / DELETE | `foods/{id}/` | Edit / delete your own custom food |
| GET | `meals/?date=` | A day's meals (default today) with items and totals |
| POST | `meals/` | Log a meal with its items (`201`, `Idempotency-Key`) |
| PATCH / DELETE | `meals/{id}/` | Edit `date` / `meal_type` / `note`; delete |
| POST | `meals/{id}/items/` | Add an item; returns the updated meal (`201`, `Idempotency-Key`) |
| PATCH / DELETE | `meals/{id}/items/{item_id}/` | Edit / remove an item; both return the updated meal (`200`) |
| GET | `nutrition/?date=` | Daily totals vs targets |

## Admin

- Plans, weights, metric links and meals are read-only: plans are history, meals
  follow the date window and the snapshot rule, and every write goes through
  `health.services`.
- **Foods**: staff create and edit global foods, with their portions inline (filter
  "owner" to see global vs custom). Users' custom foods are read-only there.

## Known limitations / next rounds

- Health dashboard endpoint and health score; AI logging (text/photo), which will
  use `log_meal(..., source=...)` after the user confirms.
- The test foods are approximate; the real dataset comes through `import_foods`.
- Steps, activity and sleep targets use the defaults (8000, 30 min, 8 h); they are
  not editable on the plan screen yet.
- Age is derived from the birth year only.
