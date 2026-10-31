"""
Writes of the health module. Views (and later the AI confirmation flow) call these.
Trackables are created and updated through tracking.services only.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.tracking import services as tracking
from apps.tracking.models import Trackable

from .calculations import WEIGHT_KG_RANGE, PlanInputs, Targets, calculate_targets
from . import selectors
from .models import METRIC_PLAN_FIELDS, Food, FoodPortion, HealthMetric, HealthPlan, Meal, MealItem, WeightEntry

__all__ = [
    'preview_plan', 'confirm_plan', 'log_weight',
    'create_custom_food', 'update_custom_food', 'delete_custom_food', 'import_foods',
    'log_meal', 'update_meal', 'delete_meal', 'add_item', 'update_item', 'remove_item',
]

MANAGED_BY = 'health'  # tracking.Trackable.managed_by of the metric trackables
WEIGHT_BACKFILL_DAYS = 30  # weight has no streak: a wider window than check-ins

Metric = HealthMetric.Metric
# metric -> how its trackable looks (its target comes from models.METRIC_PLAN_FIELDS)
METRIC_TRACKABLES = {
    Metric.WATER: {'name': 'Water', 'kind': Trackable.Kind.QUANTITY, 'unit': 'ml', 'icon': 'water'},
    Metric.SLEEP: {'name': 'Sleep', 'kind': Trackable.Kind.QUANTITY, 'unit': 'h', 'icon': 'sleep'},
    Metric.STEPS: {'name': 'Steps', 'kind': Trackable.Kind.QUANTITY, 'unit': 'steps', 'icon': 'steps'},
    Metric.ACTIVITY: {'name': 'Activity', 'kind': Trackable.Kind.DURATION, 'unit': 'min', 'icon': 'activity'},
}


# ----------------------------- plan ---------------------------------------- #
def preview_plan(inputs: PlanInputs) -> Targets:
    """Calculate the targets for these inputs. Saves nothing."""
    return calculate_targets(inputs)


@transaction.atomic
def confirm_plan(user, inputs: PlanInputs) -> HealthPlan:
    """
    Save the plan as a new version effective today (the user's local date). A second
    confirm on the same day updates today's version: earlier dates keep their plan.
    Also records today's weight and creates/updates the 4 metric trackables.
    """
    targets = calculate_targets(inputs)
    # Serialise confirms of the same user, so the metric trackables are never duplicated.
    get_user_model().objects.select_for_update().only('pk').get(pk=user.pk)
    today = tracking.user_today(user)

    plan, _ = HealthPlan.objects.update_or_create(
        user=user, effective_from=today,
        defaults={
            'goal': inputs.goal, 'sex': inputs.sex, 'birth_year': today.year - inputs.age,
            'height_cm': inputs.height_cm, 'activity_level': inputs.activity_level,
            'mode': inputs.mode, 'water_mode': inputs.water_mode,
            'calories': targets.calories, 'protein_g': targets.protein_g, 'carbs_g': targets.carbs_g,
            'fat_g': targets.fat_g, 'water_ml': targets.water_ml, 'steps': targets.steps,
            'activity_minutes': targets.activity_minutes, 'sleep_hours': targets.sleep_hours,
            'calorie_floor_applied': targets.calorie_floor_applied,
        },
    )
    log_weight(user, today, inputs.weight_kg)
    _sync_metric_trackables(user, plan)
    return plan


def _sync_metric_trackables(user, plan: HealthPlan) -> None:
    """Exactly one trackable per metric per user, found through HealthMetric (never by name)."""
    links = {link.metric: link for link in HealthMetric.objects.filter(user=user).select_related('trackable')}
    for metric, spec in METRIC_TRACKABLES.items():
        target = getattr(plan, METRIC_PLAN_FIELDS[metric])
        link = links.get(metric)
        if link is None:
            trackable = tracking.create_trackable(
                user, name=spec['name'], domain=Trackable.Domain.HEALTH, kind=spec['kind'],
                unit=spec['unit'], icon=spec['icon'], target_value=target, managed_by=MANAGED_BY,
            )
            HealthMetric.objects.create(user=user, metric=metric, trackable=trackable)
        elif link.trackable.target_value != target:
            tracking.update_trackable(user, link.trackable, managed_by=MANAGED_BY, target_value=target)


# ----------------------------- weight -------------------------------------- #
def _weight(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({'weight_kg': 'Must be a number.'}) from None
    low, high = WEIGHT_KG_RANGE
    if not result.is_finite() or not (low <= result <= high):
        raise ValidationError({'weight_kg': f'Must be between {low} and {high} kg.'})
    return result.quantize(Decimal('0.01'))


@transaction.atomic
def log_weight(user, d, weight_kg) -> WeightEntry:
    """
    Record the weight on the user's local date `d` (one per day: a second log that
    day replaces it). Accepted for the last 30 days, never a future date.
    """
    today = tracking.user_today(user)
    if d > today:
        raise ValidationError({'date': 'Cannot record a future date.'})
    if d < today - timedelta(days=WEIGHT_BACKFILL_DAYS):
        raise ValidationError({'date': f'Weight can be recorded for the last {WEIGHT_BACKFILL_DAYS} days only.'})
    entry, _ = WeightEntry.objects.update_or_create(user=user, date=d, defaults={'weight_kg': _weight(weight_kg)})
    return entry


# ----------------------------- custom foods -------------------------------- #
CUSTOM_FOOD_FIELDS = {'name_ar', 'name_en', 'name_fr', 'aliases', 'category', 'kcal', 'protein_g', 'carbs_g',
                      'fat_g'}


def _check_fields(fields):
    unknown = set(fields) - CUSTOM_FOOD_FIELDS
    if unknown:
        raise ValidationError(f'Unknown fields: {", ".join(sorted(unknown))}.')


def _owned_custom_food(user, food, *, lock=False) -> Food:
    """The user's own custom food. Global foods and other users' foods are "unknown"."""
    qs = Food.objects.select_for_update() if lock else Food.objects
    try:
        return qs.get(pk=food.pk, user=user)
    except Food.DoesNotExist:
        raise ValidationError({'food': 'Unknown food.'}) from None


def _set_portions(food: Food, portions: list[dict]) -> None:
    """Replace the food's portions with `portions` ([{'unit': 'piece', 'grams': 50}, ...])."""
    units = [p.get('unit') for p in portions]
    if len(units) != len(set(units)):
        raise ValidationError({'portions': 'Each unit can appear only once.'})
    food.portions.all().delete()
    for portion in portions:
        row = FoodPortion(food=food, unit=portion.get('unit'), grams=portion.get('grams'))
        try:
            row.full_clean(exclude=['food'])
        except ValidationError as exc:
            raise ValidationError({'portions': [f'{k}: {" ".join(v)}' for k, v in exc.message_dict.items()]}) from None
        row.save()


@transaction.atomic
def create_custom_food(user, *, portions: list[dict] | None = None, **fields) -> Food:
    """A private food, visible only to `user`. Nutrition is per 100 g."""
    _check_fields(fields)
    food = Food(user=user, source=Food.Source.CUSTOM, is_verified=False, **fields)
    food.full_clean()
    food.save()
    _set_portions(food, portions or [])
    return food


@transaction.atomic
def update_custom_food(user, food, *, portions: list[dict] | None = None, **fields) -> Food:
    """
    Edit one of the user's own custom foods (never a global one). `portions`, when
    given, replaces them all. Past meals keep their snapshot.
    """
    _check_fields(fields)
    food = _owned_custom_food(user, food, lock=True)
    for name, value in fields.items():
        setattr(food, name, value)
    food.full_clean()
    food.save()
    if portions is not None:
        _set_portions(food, portions)
    return food


@transaction.atomic
def delete_custom_food(user, food) -> None:
    """Delete one of the user's own custom foods. Past meals keep their snapshot."""
    _owned_custom_food(user, food, lock=True).delete()


# ----------------------------- food import --------------------------------- #
IMPORT_COLUMNS = ['key', 'source', 'category', 'name_ar', 'name_en', 'name_fr', 'aliases',
                  'kcal', 'protein_g', 'carbs_g', 'fat_g', 'portions', 'is_verified']
IMPORT_REQUIRED = {'key', 'source', 'kcal', 'protein_g', 'carbs_g', 'fat_g'}
_TRUE, _FALSE = {'true', '1', 'yes', 'y'}, {'false', '0', 'no', 'n', ''}


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_row(row: dict) -> tuple[dict, list[dict]]:
    """One CSV row -> (Food fields, portions). Raises ValidationError on a malformed cell."""
    get = lambda name: (row.get(name) or '').strip()  # noqa: E731
    fields = {name: get(name) for name in ('key', 'source', 'category', 'name_ar', 'name_en', 'name_fr')}
    fields['category'] = fields['category'] or Food.Category.OTHER
    fields['aliases'] = [a.strip() for a in get('aliases').split('|') if a.strip()]
    for name in ('kcal', 'protein_g', 'carbs_g', 'fat_g'):
        try:
            fields[name] = Decimal(get(name))
        except InvalidOperation:
            raise ValidationError({name: f'Not a number: “{get(name)}”.'}) from None
    verified = get('is_verified').lower()
    if verified not in _TRUE | _FALSE:
        raise ValidationError({'is_verified': f'Use true or false, not “{verified}”.'})
    fields['is_verified'] = verified in _TRUE

    portions = []
    for part in filter(None, (p.strip() for p in get('portions').split('|'))):
        unit, sep, grams = part.partition(':')
        try:
            portions.append({'unit': unit.strip(), 'grams': Decimal(grams.strip())})
        except InvalidOperation:
            raise ValidationError({'portions': f'Expected unit:grams, got “{part}”.'}) from None
        if not sep:
            raise ValidationError({'portions': f'Expected unit:grams, got “{part}”.'})
    return fields, portions


def _sync_portions(food: Food, portions: list[dict]) -> bool:
    """Make the food's portions exactly `portions` (matched by unit). Returns True if anything changed."""
    current = {p.unit: p for p in food.portions.all()}
    wanted = {p['unit']: p['grams'] for p in portions}
    if len(wanted) != len(portions):
        raise ValidationError({'portions': 'Each unit can appear only once.'})
    changed = False
    for unit, grams in wanted.items():
        row = current.get(unit) or FoodPortion(food=food, unit=unit)
        if row.pk and row.grams == grams:
            continue
        row.grams = grams
        row.full_clean(exclude=['food'])
        row.save()
        changed = True
    stale = [p.pk for unit, p in current.items() if unit not in wanted]
    if stale:
        FoodPortion.objects.filter(pk__in=stale).delete()  # past meals keep their snapshot
        changed = True
    return changed


def import_foods(rows, *, dry_run: bool = False) -> ImportResult:
    """
    Create or update global foods from CSV rows (dicts keyed by IMPORT_COLUMNS).
    Idempotent: foods are matched on (source, key), portions on unit; running the
    same file twice changes nothing. Foods missing from the file are left alone,
    and `is_active` is never touched. All or nothing: any invalid row -> nothing
    is written and every error is reported.
    """
    result = ImportResult()
    seen = set()
    with transaction.atomic():
        for line, row in enumerate(rows, start=2):  # line 1 is the header
            try:
                missing = sorted(c for c in IMPORT_REQUIRED if not (row.get(c) or '').strip())
                if missing:
                    raise ValidationError(f'Missing {", ".join(missing)}.')
                fields, portions = _parse_row(row)
                identity = (fields['source'], fields['key'])
                if identity in seen:
                    raise ValidationError(f'Duplicate key “{fields["key"]}” for source {fields["source"]}.')
                seen.add(identity)

                food = Food.objects.filter(user__isnull=True, source=fields['source'], key=fields['key']).first()
                created = food is None
                food = food or Food(source=fields['source'], key=fields['key'])
                changed = created or any(getattr(food, n) != v for n, v in fields.items())
                for name, value in fields.items():
                    setattr(food, name, value)
                food.full_clean()
                if changed:
                    food.save()
                portions_changed = _sync_portions(food, portions)
            except ValidationError as exc:
                messages = exc.message_dict.items() if hasattr(exc, 'error_dict') else [('', exc.messages)]
                result.errors += [f'line {line}: {f"{k}: " if k else ""}{" ".join(v)}' for k, v in messages]
                continue
            if created:
                result.created += 1
            elif changed or portions_changed:
                result.updated += 1
            else:
                result.unchanged += 1
        if result.errors or dry_run:
            transaction.set_rollback(True)
    return result


# ----------------------------- meals --------------------------------------- #
# Same window as check-ins (CLAUDE.md rule 3): today and yesterday, user's local date.
MEAL_BACKFILL_DAYS = tracking.BACKFILL_DAYS
ITEM_GRAMS_RANGE = (Decimal(1), Decimal(5000))
ITEM_QUANTITY_RANGE = (Decimal('0.25'), Decimal(50))
MAX_ITEMS_PER_MEAL = 30
MEAL_FIELDS = {'date', 'meal_type', 'note'}
ITEM_FIELDS = {'food', 'portion', 'quantity', 'grams'}
_TENTH = Decimal('0.1')


def _tenth(value: Decimal) -> Decimal:
    return value.quantize(_TENTH, rounding=ROUND_HALF_UP)


def _check_meal_date(user, d, field='date') -> None:
    today = tracking.user_today(user)
    if d > today:
        raise ValidationError({field: 'Cannot record a future date.'})
    if d < today - timedelta(days=MEAL_BACKFILL_DAYS):
        raise ValidationError({field: 'Meals can only be recorded for today or yesterday.'})


def _owned_meal(user, meal, *, lock=False) -> Meal:
    qs = Meal.objects.select_for_update() if lock else Meal.objects
    try:
        return qs.get(pk=meal.pk, user=user)
    except Meal.DoesNotExist:
        raise ValidationError({'meal': 'Unknown meal.'}) from None


def _visible_food(user, food) -> Food:
    try:
        return selectors.foods_for(user).prefetch_related('portions').get(pk=food.pk)
    except Food.DoesNotExist:
        raise ValidationError({'food': 'Unknown food.'}) from None


def _in_range(field, value, bounds, unit=''):
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({field: 'Must be a number.'}) from None
    low, high = bounds
    if not value.is_finite() or not (low <= value <= high):
        raise ValidationError({field: f'Must be between {low} and {high}{unit}.'})
    return value


def _measure(food: Food | None, *, portion: str | None, quantity, grams):
    """
    -> (FoodPortion | None, unit, quantity, grams). Either a portion (with a quantity,
    default 1) or grams entered directly; not both.
    """
    if grams is not None:
        if portion:
            raise ValidationError({'grams': 'Send grams or a portion, not both.'})
        return None, '', None, _tenth(_in_range('grams', grams, ITEM_GRAMS_RANGE, ' g'))
    if not portion:
        raise ValidationError({'grams': 'Send grams or a portion.'})
    if food is None:
        raise ValidationError({'portion': 'This food no longer exists: enter grams.'})
    row = next((p for p in food.portions.all() if p.unit == portion), None)
    if row is None:
        raise ValidationError({'portion': f'“{portion}” is not a portion of this food.'})
    quantity = _in_range('quantity', 1 if quantity is None else quantity, ITEM_QUANTITY_RANGE)
    total = _in_range('grams', row.grams * quantity, ITEM_GRAMS_RANGE, ' g')
    return row, row.unit, quantity, _tenth(total)


def _snapshot_from_food(item: MealItem, food: Food) -> None:
    """Copy the food's names and its nutrition for item.grams (per-100 g values x grams / 100)."""
    factor = item.grams / 100
    item.name_ar, item.name_en, item.name_fr = food.name_ar, food.name_en, food.name_fr
    for name in ('kcal', 'protein_g', 'carbs_g', 'fat_g'):
        setattr(item, name, _tenth(getattr(food, name) * factor))


def _rescale_snapshot(item: MealItem, old_grams: Decimal) -> None:
    """The food is gone: keep the snapshot's per-gram values for the new weight."""
    factor = item.grams / old_grams
    for name in ('kcal', 'protein_g', 'carbs_g', 'fat_g'):
        setattr(item, name, _tenth(getattr(item, name) * factor))


def _build_item(user, meal: Meal, spec: dict) -> MealItem:
    unknown = set(spec) - ITEM_FIELDS
    if unknown:
        raise ValidationError(f'Unknown item fields: {", ".join(sorted(unknown))}.')
    if spec.get('food') is None:
        raise ValidationError({'food': 'Required.'})
    food = _visible_food(user, spec['food'])
    portion, unit, quantity, grams = _measure(food, portion=spec.get('portion'), quantity=spec.get('quantity'),
                                              grams=spec.get('grams'))
    item = MealItem(meal=meal, food=food, portion=portion, portion_unit=unit, quantity=quantity, grams=grams)
    _snapshot_from_food(item, food)
    return item


def _validate_meal_fields(user, fields):
    unknown = set(fields) - MEAL_FIELDS
    if unknown:
        raise ValidationError(f'Unknown fields: {", ".join(sorted(unknown))}.')
    if 'date' in fields:
        _check_meal_date(user, fields['date'])


@transaction.atomic
def log_meal(user, *, date, meal_type, items: list[dict], note: str = '',
             source: str = Meal.Source.MANUAL) -> Meal:
    """
    Record a meal and its items in one transaction (today or yesterday only).
    Each item is {'food', 'portion' + optional 'quantity'} or {'food', 'grams'}; its
    name and nutrition are snapshotted now. Several meals of the same type per day are fine.
    """
    _validate_meal_fields(user, {'date': date, 'meal_type': meal_type, 'note': note})
    if source not in Meal.Source.values:
        raise ValidationError({'source': f'Unknown source “{source}”.'})
    if not items:
        raise ValidationError({'items': 'A meal needs at least one item.'})
    if len(items) > MAX_ITEMS_PER_MEAL:
        raise ValidationError({'items': f'At most {MAX_ITEMS_PER_MEAL} items per meal.'})
    meal = Meal(user=user, date=date, meal_type=meal_type, note=note, source=source)
    meal.full_clean()
    meal.save()
    rows = []
    for index, spec in enumerate(items):
        try:
            rows.append(_build_item(user, meal, spec))
        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, 'error_dict') else {'non_field_errors': exc.messages}
            raise ValidationError({'items': [f'item {index + 1}: {k}: {" ".join(v)}' for k, v in detail.items()]}) \
                from None
    MealItem.objects.bulk_create(rows)
    return meal


@transaction.atomic
def update_meal(user, meal, **fields) -> Meal:
    """Change date, meal_type or note. Both the current and the new date must be in the window."""
    meal = _owned_meal(user, meal, lock=True)
    _check_meal_date(user, meal.date)
    _validate_meal_fields(user, fields)
    for name, value in fields.items():
        setattr(meal, name, value)
    meal.full_clean()
    meal.save()
    return meal


@transaction.atomic
def delete_meal(user, meal) -> None:
    meal = _owned_meal(user, meal, lock=True)
    _check_meal_date(user, meal.date)
    meal.delete()


def _owned_item(user, item, *, lock_meal=True) -> tuple[MealItem, Meal]:
    """Lock the item's meal (serialises item changes: the last-item rule, the item limit), then the item."""
    try:
        meal_id = MealItem.objects.filter(pk=item.pk, meal__user=user).values_list('meal_id', flat=True).get()
    except MealItem.DoesNotExist:
        raise ValidationError({'item': 'Unknown item.'}) from None
    meal = _owned_meal(user, Meal(pk=meal_id), lock=lock_meal)
    item = MealItem.objects.select_related('food').prefetch_related('food__portions').get(pk=item.pk)
    return item, meal


@transaction.atomic
def add_item(user, meal, spec: dict) -> MealItem:
    meal = _owned_meal(user, meal, lock=True)
    _check_meal_date(user, meal.date)
    if meal.items.count() >= MAX_ITEMS_PER_MEAL:
        raise ValidationError({'items': f'At most {MAX_ITEMS_PER_MEAL} items per meal.'})
    item = _build_item(user, meal, spec)
    item.save()
    return item


@transaction.atomic
def update_item(user, item, **changes) -> MealItem:
    """
    Change an item's food, portion/quantity or grams; the snapshot is recomputed from
    the food as it is now. If the food was deleted, the snapshot is rescaled to the
    new grams (and only grams can be entered).
    """
    unknown = set(changes) - ITEM_FIELDS
    if unknown:
        raise ValidationError(f'Unknown item fields: {", ".join(sorted(unknown))}.')
    item, meal = _owned_item(user, item)
    _check_meal_date(user, meal.date)

    food = _visible_food(user, changes['food']) if changes.get('food') is not None else item.food
    if 'grams' in changes and changes['grams'] is not None:
        measure = {'portion': None, 'quantity': None, 'grams': changes['grams']}
    elif 'portion' in changes or 'quantity' in changes:
        measure = {'portion': changes.get('portion', item.portion_unit or None),
                   'quantity': changes.get('quantity', item.quantity), 'grams': None}
    elif item.portion_unit:  # only the food changed: keep the same portion
        measure = {'portion': item.portion_unit, 'quantity': item.quantity, 'grams': None}
    else:
        measure = {'portion': None, 'quantity': None, 'grams': item.grams}
    old_grams = item.grams
    item.portion, item.portion_unit, item.quantity, item.grams = _measure(food, **measure)
    item.food = food
    if food is not None:
        _snapshot_from_food(item, food)
    else:
        _rescale_snapshot(item, old_grams)
    item.save()
    return item


@transaction.atomic
def remove_item(user, item) -> None:
    """Remove an item. A meal cannot be left empty: delete the meal instead."""
    item, meal = _owned_item(user, item)
    _check_meal_date(user, meal.date)
    if meal.items.count() <= 1:
        raise ValidationError({'item': 'This is the last item of the meal: delete the meal instead.'})
    item.delete()
