"""Invalidate the cached AI configuration whenever it changes (admin or code)."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import config
from .models import AIFeatureConfig, ProviderPrice


@receiver([post_save, post_delete], sender=AIFeatureConfig)
def _feature_config_changed(sender, instance, **kwargs):
    config.invalidate_feature(instance.feature)


@receiver([post_save, post_delete], sender=ProviderPrice)
def _price_changed(sender, instance, **kwargs):
    config.invalidate_price(instance.provider, instance.model)
