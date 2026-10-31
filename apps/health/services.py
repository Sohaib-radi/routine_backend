"""
Writes of the health module. Views (and later the AI confirmation flow) call these.
Trackables are created and updated through tracking.services only.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.tracking import services as tracking
from apps.tracking.models import Trackable

from .calculations import WEIGHT_KG_RANGE, PlanInputs, Targets, calculate_targets
from .models import Food, FoodPortion, HealthMetric, HealthPlan, WeightEntry

__all__ = [
    'preview_plan', 'confirm_plan', 'log_weight',
    'create_custom_food', 'update_custom_food', 'delete_custom_food', 'import_foods',
]

MANAGED_BY = 'health'  # tracking.Trackable.managed_by of the metric trackables
WEIGHT_BACKFILL_DAYS = 30  # weight has no streak: a wider window than check-ins

Metric = HealthMetric.Metric
# metric -> how its trackable looks, and which plan field holds its target
METRIC_TRACKABLES = {
    Metric.WATER: {'name': 'Water', 'kind': Trackable.Kind.QUANTITY, 'unit': 'ml', 'icon': 'water',
                   'target': 'water_ml'},
    Metric.SLEEP: {'name': 'Sleep', 'kind': Trackable.Kind.QUANTITY, 'unit': 'h', 'icon': 'sleep',
                   'target': 'sleep_hours'},
    Metric.STEPS: {'name': 'Steps', 'kind': Trackable.Kind.QUANTITY, 'unit': 'steps', 'icon': 'steps',
                   'target': 'steps'},
    Metric.ACTIVITY: {'name': 'Activity', 'kind': Trackable.Kind.DURATION, 'unit': 'min', 'icon': 'activity',
                      'target': 'activity_minutes'},
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
        target = getattr(plan, spec['target'])
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
