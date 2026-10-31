"""
ai: optional entry point that turns free input (text now; photo and voice later)
into PROPOSALS. Nothing is saved to the domain apps until the user confirms
(CLAUDE.md rule 7), and confirming goes through the domain services.

ai may import health, tracking and accounts; nothing imports ai.

API keys live in environment variables only, never in the database.
"""

import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Feature(models.TextChoices):
    MEAL_TEXT = 'meal_text', 'Meal from text'
    MEAL_PHOTO = 'meal_photo', 'Meal from photo'
    VOICE = 'voice', 'Voice'


class Provider(models.TextChoices):
    ANTHROPIC = 'anthropic', 'Anthropic (Claude)'
    FAKE = 'fake', 'Fake (tests and local development)'


class Effort(models.TextChoices):
    LOW = 'low', 'Low'
    MEDIUM = 'medium', 'Medium'
    HIGH = 'high', 'High'


class AIFeatureConfig(models.Model):
    """
    Runtime configuration of one AI feature, managed in the admin. Without a row, the
    environment defaults (settings.AI_DEFAULTS) apply. Cached in Redis and invalidated
    on save, so a change applies immediately.
    """

    feature = models.CharField(max_length=20, choices=Feature.choices, unique=True)
    enabled = models.BooleanField(default=True, help_text='Off: the endpoints answer 503 "ai_disabled".')
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.ANTHROPIC)
    model = models.CharField(max_length=100, help_text='Provider model id, e.g. claude-haiku-5-5.')
    effort = models.CharField(max_length=10, choices=Effort.choices, default=Effort.LOW)
    daily_limit = models.PositiveIntegerField(
        default=20, validators=[MinValueValidator(1)], help_text='Analyses per user per local day.',
    )
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['feature']
        verbose_name = 'AI feature configuration'

    def __str__(self):
        return self.get_feature_display()


class ProviderPrice(models.Model):
    """USD per million tokens, used to estimate AIUsage.cost_usd. Editable in the admin."""

    provider = models.CharField(max_length=20, choices=Provider.choices)
    model = models.CharField(max_length=100)
    input_usd_per_mtok = models.DecimalField(max_digits=10, decimal_places=4, validators=[MinValueValidator(0)])
    output_usd_per_mtok = models.DecimalField(max_digits=10, decimal_places=4, validators=[MinValueValidator(0)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['provider', 'model']
        constraints = [models.UniqueConstraint(fields=['provider', 'model'], name='uniq_provider_price')]

    def __str__(self):
        return f'{self.provider} {self.model}'


class AIUsage(models.Model):
    """One row per provider call (retries included): tokens, estimated cost, latency, outcome."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='ai_usage', help_text='Empty for calls from the try_* commands.')
    feature = models.CharField(max_length=20, choices=Feature.choices)
    provider = models.CharField(max_length=20, choices=Provider.choices)
    model = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=40)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True,
                                   help_text='Empty when no price is configured for this model.')
    latency_ms = models.PositiveIntegerField(default=0)
    success = models.BooleanField()
    error_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'AI usage'
        verbose_name_plural = 'AI usage'
        indexes = [models.Index(fields=['user', 'feature', 'created_at'])]

    def __str__(self):
        return f'{self.feature} {self.model} {"ok" if self.success else self.error_code}'
