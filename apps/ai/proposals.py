"""
Turn an Extraction into the proposal the app shows, and apply the user's edits.

Each extracted name is matched against the Food table (health.selectors.match_foods).
- Matched: the numbers come from the Food table (best match + up to 3 alternatives).
  The AI's unit is used when the food has that portion; otherwise grams (the user's
  weight, or the AI's estimate, flagged `grams_estimated`).
- Not matched: the AI's estimate (grams + nutrition per 100 g), flagged `estimated`.

The proposal is JSON (decimals as strings). Nutrition shown here is a preview:
confirming goes through health.services.log_meal, which recomputes from the table.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from apps.health import selectors as health_selectors
from apps.health.models import Food, PortionUnit

from .extraction import GRAMS_RANGE, QUANTITY_RANGE, Estimate, Extraction, ExtractedItem, MAX_ITEMS, \
    MAX_NAME_LENGTH, _number
from .providers.base import ProviderBadOutput

MATCHED, ESTIMATED = 'matched', 'estimated'
NUTRIENTS = ('kcal', 'protein_g', 'carbs_g', 'fat_g')
_TENTH = Decimal('0.1')


def _s(value: Decimal) -> str:
    return str(value.quantize(_TENTH, rounding=ROUND_HALF_UP))


def _nutrition(per100: dict, grams: Decimal) -> dict:
    return {n: _s(Decimal(str(per100[n])) * grams / 100) for n in NUTRIENTS}


def _food_json(food: Food) -> dict:
    return {
        'food_id': str(food.pk), 'name_ar': food.name_ar, 'name_en': food.name_en, 'name_fr': food.name_fr,
        'is_custom': food.user_id is not None, 'is_verified': food.is_verified,
        **{n: str(getattr(food, n)) for n in NUTRIENTS},
        'portions': [{'unit': p.unit, 'grams': str(p.grams)} for p in food.portions.all()],
    }


def _clamp_grams(grams: Decimal) -> Decimal:
    return min(max(grams, GRAMS_RANGE[0]), GRAMS_RANGE[1]).quantize(_TENTH, rounding=ROUND_HALF_UP)


def _measure_for(food_json: dict, unit, quantity: Decimal, grams, estimated_grams: Decimal) -> dict:
    portion = next((p for p in food_json['portions'] if p['unit'] == unit), None) if unit else None
    if portion is not None and grams is None:
        total = Decimal(portion['grams']) * quantity
        if total <= GRAMS_RANGE[1]:
            return {'portion': unit, 'quantity': str(quantity), 'grams': _s(total), 'grams_estimated': False}
    if grams is not None:
        return {'portion': None, 'quantity': None, 'grams': _s(_clamp_grams(grams)), 'grams_estimated': False}
    return {'portion': None, 'quantity': None, 'grams': _s(_clamp_grams(estimated_grams)), 'grams_estimated': True}


def _item(extracted: ExtractedItem, candidates: list[Food]) -> dict:
    estimate = {n: str(getattr(extracted.estimate, n)) for n in NUTRIENTS}
    base = {'id': str(uuid.uuid4()), 'name': extracted.name, 'unit': extracted.unit,
            'quantity_said': str(extracted.quantity), 'estimate': estimate}
    if not candidates:
        grams = _clamp_grams(extracted.grams if extracted.grams is not None else extracted.estimated_grams)
        return {**base, 'status': ESTIMATED, 'food_id': None, 'candidates': [], 'portion': None, 'quantity': None,
                'grams': _s(grams), 'grams_estimated': extracted.grams is None,
                'nutrition': _nutrition(estimate, grams)}
    cands = [_food_json(f) for f in candidates]
    best = cands[0]
    measure = _measure_for(best, extracted.unit, extracted.quantity, extracted.grams, extracted.estimated_grams)
    return {**base, 'status': MATCHED, 'food_id': best['food_id'], 'candidates': cands, **measure,
            'nutrition': _nutrition(best, Decimal(measure['grams']))}


def build(user, extraction: Extraction) -> dict:
    """The proposal for an extraction. One match_foods() call per item."""
    items = [_item(e, health_selectors.match_foods(user, e.name)) for e in extraction.items]
    return {'items': items, 'totals': totals(items)}


def totals(items: list[dict]) -> dict:
    return {n: _s(sum((Decimal(i['nutrition'][n]) for i in items), Decimal(0))) for n in NUTRIENTS}


# ----------------------------- user edits ---------------------------------- #
def _bad(message):
    return ValidationError({'items': [message]})


def _decimal(value, field, bounds):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _bad(f'{field}: must be a number.') from None
    if not number.is_finite() or not (bounds[0] <= number <= bounds[1]):
        raise _bad(f'{field}: must be between {bounds[0]} and {bounds[1]}.')
    return number


def apply_edits(user, current: dict, edited_items: list[dict]) -> dict:
    """
    Replace the proposal's items with the user's edited list. Each item is either
    {"food_id", "portion" (+ "quantity") or "grams"} — any food the user can see —
    or an estimated item {"name", "grams", "estimate": {kcal, protein_g, carbs_g, fat_g}}.
    Sending an existing item's "id" keeps its candidates, so the user can switch again.
    """
    if not edited_items:
        raise _bad('A meal needs at least one item.')
    if len(edited_items) > MAX_ITEMS:
        raise _bad(f'At most {MAX_ITEMS} items.')
    previous = {i['id']: i for i in current.get('items', [])}
    visible = health_selectors.foods_for(user).prefetch_related('portions')
    items = []
    for n, spec in enumerate(edited_items, start=1):
        where = f'item {n}'
        old = previous.get(str(spec.get('id') or ''))
        base = {'id': old['id'] if old else str(uuid.uuid4()),
                'name': (old or {}).get('name') or spec.get('name') or '',
                'unit': (old or {}).get('unit'), 'quantity_said': (old or {}).get('quantity_said'),
                'estimate': (old or {}).get('estimate'), 'candidates': (old or {}).get('candidates', [])}
        if spec.get('food_id'):
            food = visible.filter(pk=spec['food_id']).first()
            if food is None:
                raise _bad(f'{where}: unknown food.')
            food_json = _food_json(food)
            if spec.get('grams') is not None and spec.get('portion'):
                raise _bad(f'{where}: send grams or a portion, not both.')
            if spec.get('portion'):
                if spec['portion'] not in PortionUnit.values or not any(
                        p['unit'] == spec['portion'] for p in food_json['portions']):
                    raise _bad(f'{where}: “{spec["portion"]}” is not a portion of this food.')
                quantity = _decimal(spec.get('quantity', 1), f'{where}.quantity', QUANTITY_RANGE)
                grams = _decimal(Decimal(next(p['grams'] for p in food_json['portions']
                                              if p['unit'] == spec['portion'])) * quantity,
                                 f'{where}.grams', GRAMS_RANGE)
                measure = {'portion': spec['portion'], 'quantity': str(quantity), 'grams': _s(grams)}
            elif spec.get('grams') is not None:
                grams = _decimal(spec['grams'], f'{where}.grams', GRAMS_RANGE)
                measure = {'portion': None, 'quantity': None, 'grams': _s(grams)}
            else:
                raise _bad(f'{where}: send grams or a portion.')
            if not any(c['food_id'] == food_json['food_id'] for c in base['candidates']):
                base['candidates'] = [food_json, *base['candidates']][:4]
            items.append({**base, 'status': MATCHED, 'food_id': food_json['food_id'], **measure,
                          'grams_estimated': False, 'nutrition': _nutrition(food_json, Decimal(measure['grams']))})
        else:
            name = str(spec.get('name') or base['name']).strip()
            if not name or len(name) > MAX_NAME_LENGTH:
                raise _bad(f'{where}: a name of 1-{MAX_NAME_LENGTH} characters is required.')
            raw = spec.get('estimate') or base['estimate']
            if not isinstance(raw, dict):
                raise _bad(f'{where}: an estimate (kcal, protein_g, carbs_g, fat_g per 100 g) is required.')
            try:
                est = Estimate(**{k: _number(_float(raw.get(k)), f'{where}.{k}', b) for k, b in (
                    ('kcal', (0, 900)), ('protein_g', (0, 100)), ('carbs_g', (0, 100)), ('fat_g', (0, 100)))})
            except ProviderBadOutput as exc:
                raise _bad(str(exc)) from None
            if est.protein_g + est.carbs_g + est.fat_g > 100:
                raise _bad(f'{where}: protein + carbs + fat cannot exceed 100 g per 100 g.')
            grams = _decimal(spec.get('grams', (old or {}).get('grams')), f'{where}.grams', GRAMS_RANGE)
            estimate = {k: str(getattr(est, k)) for k in NUTRIENTS}
            items.append({**base, 'name': name, 'estimate': estimate, 'status': ESTIMATED, 'food_id': None,
                          'portion': None, 'quantity': None, 'grams': _s(grams), 'grams_estimated': False,
                          'nutrition': _nutrition(estimate, grams)})
    return {'items': items, 'totals': totals(items)}


def _float(value):
    """Estimates arrive as JSON numbers or strings; _number expects a number."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value
