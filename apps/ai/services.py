"""
Writes of the ai app. Every provider call goes through call_provider(), which
records an AIUsage row for success AND failure (tokens, cost, latency).
"""

import logging
import time
from datetime import datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from zoneinfo import ZoneInfo

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.health import services as health_services
from apps.health.models import Food, Meal
from apps.health.text import normalize
from apps.tracking import services as tracking

from . import config, extraction, proposals
from .errors import AIDisabled, DailyLimitExceeded
from .models import AIUsage, Feature, MealDraft
from .providers import get_provider
from .providers.base import (
    ProviderBadOutput,
    ProviderError,
    ProviderMisconfigured,
    StructuredRequest,
    StructuredResponse,
)

logger = logging.getLogger(__name__)


def call_provider(*, user, cfg: config.FeatureConfig, request: StructuredRequest, prompt_version: str,
                  draft=None) -> tuple[StructuredResponse, AIUsage]:
    """
    One provider call with the feature's provider, model and effort, under the
    configured timeout. Always logs AIUsage; re-raises ProviderError on failure.
    Returns the response and its usage row (the caller marks it failed if the
    reply breaks our rules).
    """
    started = time.monotonic()
    usage = {'user': user, 'feature': cfg.feature, 'provider': cfg.provider, 'model': cfg.model,
             'prompt_version': prompt_version, 'draft': draft}
    try:
        if not config.provider_key_configured(cfg.provider):
            raise ProviderMisconfigured(f'No API key configured for {cfg.provider}.')
        response = get_provider(cfg.provider).structured(
            request, model=cfg.model, effort=cfg.effort, timeout=settings.AI_TIMEOUT_SECONDS,
        )
    except ProviderError as exc:
        _record(usage, started, exc.input_tokens, exc.output_tokens, success=False, error_code=exc.code)
        raise
    except Exception:
        _record(usage, started, 0, 0, success=False, error_code='internal_error')
        raise
    row = _record({**usage, 'model': response.model or cfg.model}, started, response.input_tokens,
                  response.output_tokens, success=True)
    return response, row


def _record(usage: dict, started: float, input_tokens: int, output_tokens: int, *, success: bool,
            error_code: str = '') -> AIUsage:
    return AIUsage.objects.create(
        **usage, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=config.estimate_cost(usage['provider'], usage['model'], input_tokens, output_tokens),
        latency_ms=int((time.monotonic() - started) * 1000), success=success, error_code=error_code,
    )


# ----------------------------- meal drafts --------------------------------- #
# Guess for meal_type when neither the user nor the text gives one (user's local hour).
MEAL_TYPE_BY_HOUR = ((11, 'breakfast'), (16, 'lunch'), (21, 'dinner'))  # before 11:00 -> breakfast ...
LATE_MEAL_TYPE = 'snack'
ANALYSIS_ATTEMPTS = 2  # invalid output -> one retry, then the draft fails


def _local_now(user) -> datetime:
    return timezone.now().astimezone(ZoneInfo(user.timezone))


def guess_meal_type(user) -> str:
    hour = _local_now(user).hour
    return next((meal for before, meal in MEAL_TYPE_BY_HOUR if hour < before), LATE_MEAL_TYPE)


def day_bounds(user) -> tuple[datetime, datetime]:
    """[start, end) of the user's current local day, as aware datetimes."""
    tz = ZoneInfo(user.timezone)
    today = _local_now(user).date()
    start = datetime.combine(today, dt_time.min, tzinfo=tz)
    return start, datetime.combine(today + timedelta(days=1), dt_time.min, tzinfo=tz)


def _check_enabled(cfg) -> None:
    if not cfg.enabled:
        raise AIDisabled()


def _check_draft_date(user, d) -> None:
    today = tracking.user_today(user)
    if d > today:
        raise ValidationError({'date': 'Cannot record a future date.'})
    if d < today - timedelta(days=health_services.MEAL_BACKFILL_DAYS):
        raise ValidationError({'date': 'Meals can only be recorded for today or yesterday.'})


@transaction.atomic
def create_draft(user, *, text: str, date=None, meal_type: str | None = None) -> MealDraft:
    """
    Start an analysis: checks the feature is on, the text, the date and the daily
    limit, saves a pending draft and queues the Celery task once committed.
    Nothing is written to health.
    """
    cfg = config.feature_config(Feature.MEAL_TEXT)
    _check_enabled(cfg)
    text = (text or '').strip()
    if not text:
        raise ValidationError({'text': 'Tell us what you ate.'})
    if len(text) > extraction.MAX_TEXT_LENGTH:
        raise ValidationError({'text': f'At most {extraction.MAX_TEXT_LENGTH} characters.'})
    d = date or tracking.user_today(user)
    _check_draft_date(user, d)
    if meal_type is not None and meal_type not in Meal.Type.values:
        raise ValidationError({'meal_type': f'Unknown meal type “{meal_type}”.'})

    # Serialise this user's analyses so two requests cannot both pass the limit.
    get_user_model().objects.select_for_update().only('pk').get(pk=user.pk)
    start, end = day_bounds(user)
    if MealDraft.objects.filter(user=user, created_at__gte=start, created_at__lt=end).count() >= cfg.daily_limit:
        raise DailyLimitExceeded(cfg.daily_limit, end)

    draft = MealDraft.objects.create(
        user=user, text=text, date=d,
        meal_type=meal_type or guess_meal_type(user),
        meal_type_source=MealDraft.MealTypeSource.USER if meal_type else MealDraft.MealTypeSource.TIME,
        expires_at=timezone.now() + timedelta(hours=settings.AI_DRAFT_TTL_HOURS),
    )
    from .tasks import analyze_meal_draft  # tasks imports services

    transaction.on_commit(lambda: analyze_meal_draft.delay(str(draft.pk)))
    return draft


def _fail(draft: MealDraft, code: str, message: str) -> MealDraft:
    MealDraft.objects.filter(pk=draft.pk).update(status=MealDraft.Status.FAILED, error_code=code,
                                                 error_message=message[:255], updated_at=timezone.now())
    draft.refresh_from_db()
    return draft


FAILURE_MESSAGES = {
    'invalid_output': 'We could not read the analysis. Please try again or log the meal manually.',
    'no_food_found': 'We did not find any food or drink in the text.',
    'timeout': 'The analysis took too long. Please try again.',
    'provider_unavailable': 'The analysis service is busy. Please try again in a moment.',
    'provider_misconfigured': 'The analysis service is not configured.',
    'refused': 'This text could not be analyzed.',
    'ai_disabled': 'Meal analysis is turned off.',
    'stuck': 'The analysis did not finish. Please try again.',
    'internal_error': 'Something went wrong. Please try again.',
    'provider_error': 'The analysis failed. Please try again.',
}


def analyze_draft(draft_id) -> MealDraft | None:
    """
    Run the analysis of a pending draft (called by the Celery task). Never leaves the
    draft in processing: every outcome ends in ready or failed.
    """
    with transaction.atomic():
        draft = MealDraft.objects.select_for_update().filter(pk=draft_id).first()
        if draft is None or draft.status != MealDraft.Status.PENDING:
            return draft  # duplicate delivery, or already handled
        cfg = config.feature_config(Feature.MEAL_TEXT)
        draft.status = MealDraft.Status.PROCESSING
        draft.provider, draft.model, draft.prompt_version = cfg.provider, cfg.model, extraction.PROMPT_VERSION
        draft.save(update_fields=['status', 'provider', 'model', 'prompt_version', 'updated_at'])

    try:
        if not cfg.enabled:
            return _fail(draft, 'ai_disabled', FAILURE_MESSAGES['ai_disabled'])
        request = StructuredRequest(system=extraction.SYSTEM_PROMPT, user_content=extraction.user_content(draft.text),
                                    schema=extraction.SCHEMA)
        result = None
        for attempt in range(1, ANALYSIS_ATTEMPTS + 1):
            usage = None
            try:
                response, usage = call_provider(user=draft.user, cfg=cfg, request=request,
                                                prompt_version=extraction.PROMPT_VERSION, draft=draft)
                result = extraction.validate(response.data)
                break
            except ProviderBadOutput:
                if usage is not None:  # valid JSON that breaks our rules: the call counts as failed
                    AIUsage.objects.filter(pk=usage.pk).update(success=False, error_code='invalid_output')
                if attempt == ANALYSIS_ATTEMPTS:
                    return _fail(draft, 'invalid_output', FAILURE_MESSAGES['invalid_output'])
            except ProviderError as exc:  # transport errors were already retried by the SDK
                return _fail(draft, exc.code, FAILURE_MESSAGES.get(exc.code, FAILURE_MESSAGES['provider_error']))
        if not result.items:
            return _fail(draft, 'no_food_found', FAILURE_MESSAGES['no_food_found'])

        draft.proposal = proposals.build(draft.user, result)
        if result.meal_type and draft.meal_type_source != MealDraft.MealTypeSource.USER:
            draft.meal_type, draft.meal_type_source = result.meal_type, MealDraft.MealTypeSource.AI
        draft.status, draft.analyzed_at = MealDraft.Status.READY, timezone.now()
        draft.save(update_fields=['proposal', 'meal_type', 'meal_type_source', 'status', 'analyzed_at',
                                  'updated_at'])
        return draft
    except SoftTimeLimitExceeded:
        return _fail(draft, 'timeout', FAILURE_MESSAGES['timeout'])
    except Exception:  # a draft must never stay in processing
        logger.exception('Meal draft %s analysis failed', draft.pk)
        _fail(draft, 'internal_error', FAILURE_MESSAGES['internal_error'])
        raise


def _owned_draft(user, draft, *, lock=True) -> MealDraft:
    qs = MealDraft.objects.select_for_update() if lock else MealDraft.objects
    found = qs.filter(pk=draft.pk, user=user).first()
    if found is None:
        raise ValidationError({'draft': 'Unknown draft.'})
    if found.status in MealDraft.OPEN and timezone.now() >= found.expires_at:
        found.status = MealDraft.Status.EXPIRED
        found.save(update_fields=['status', 'updated_at'])
    return found


def _require_ready(draft: MealDraft) -> None:
    if draft.status == MealDraft.Status.EXPIRED:
        raise ValidationError({'draft': 'This draft has expired. Please analyze the text again.'})
    if draft.status != MealDraft.Status.READY:
        raise ValidationError({'draft': f'This draft is {draft.status}, not ready.'})


@transaction.atomic
def update_draft(user, draft, *, items: list[dict] | None = None, date=None, meal_type: str | None = None) -> MealDraft:
    """The user's edits (items, date, meal type) while the draft is ready. Still nothing in health."""
    draft = _owned_draft(user, draft)
    _require_ready(draft)
    fields = []
    if items is not None:
        draft.proposal = proposals.apply_edits(user, draft.proposal, items)
        fields.append('proposal')
    if date is not None:
        _check_draft_date(user, date)
        draft.date = date
        fields.append('date')
    if meal_type is not None:
        if meal_type not in Meal.Type.values:
            raise ValidationError({'meal_type': f'Unknown meal type “{meal_type}”.'})
        draft.meal_type, draft.meal_type_source = meal_type, MealDraft.MealTypeSource.USER
        fields += ['meal_type', 'meal_type_source']
    if fields:
        draft.save(update_fields=[*fields, 'updated_at'])
    return draft


def _name_field(name: str) -> str:
    return 'name_ar' if any('؀' <= c <= 'ۿ' for c in name) else 'name_en'


def _ai_food(user, item: dict) -> Food:
    """The user's private AI food for an estimated item: reused by normalized name, else created."""
    existing = Food.objects.filter(user=user, source=Food.Source.AI, search_text=normalize(item['name'])).first()
    if existing is not None:
        return existing
    return health_services.create_custom_food(
        user, source=Food.Source.AI, **{_name_field(item['name']): item['name']},
        **{n: Decimal(item['estimate'][n]) for n in proposals.NUTRIENTS},
    )


@transaction.atomic
def confirm_draft(user, draft) -> Meal:
    """
    Save the meal (CLAUDE.md rule 7: only now does anything reach health), through
    health.services.log_meal with source=agent, like a manual meal. Estimated items
    become private `ai` foods (reused by name). Confirming twice returns the same meal.
    """
    draft = _owned_draft(user, draft)
    if draft.status == MealDraft.Status.CONFIRMED and draft.meal_id:
        return draft.meal
    _require_ready(draft)
    if not draft.proposal.get('items'):
        raise ValidationError({'items': 'A meal needs at least one item.'})
    specs = []
    for item in draft.proposal['items']:
        food = Food(pk=item['food_id']) if item['status'] == proposals.MATCHED else _ai_food(user, item)
        if item.get('portion'):
            specs.append({'food': food, 'portion': item['portion'], 'quantity': Decimal(item['quantity'])})
        else:
            specs.append({'food': food, 'grams': Decimal(item['grams'])})
    meal = health_services.log_meal(user, date=draft.date, meal_type=draft.meal_type, items=specs,
                                    source=Meal.Source.AGENT)
    draft.status, draft.meal, draft.confirmed_at = MealDraft.Status.CONFIRMED, meal, timezone.now()
    draft.save(update_fields=['status', 'meal', 'confirmed_at', 'updated_at'])
    return meal


def sweep_drafts(now: datetime | None = None) -> dict:
    """Beat task body: fail drafts stuck in pending/processing; expire open drafts past their TTL."""
    now = now or timezone.now()
    stuck_before = now - timedelta(minutes=settings.AI_STUCK_AFTER_MINUTES)
    stuck = MealDraft.objects.filter(
        status__in=[MealDraft.Status.PENDING, MealDraft.Status.PROCESSING], updated_at__lt=stuck_before,
    ).update(status=MealDraft.Status.FAILED, error_code='stuck', error_message=FAILURE_MESSAGES['stuck'],
             updated_at=now)
    expired = MealDraft.objects.filter(status__in=MealDraft.OPEN, expires_at__lte=now).update(
        status=MealDraft.Status.EXPIRED, updated_at=now)
    return {'stuck': stuck, 'expired': expired}
