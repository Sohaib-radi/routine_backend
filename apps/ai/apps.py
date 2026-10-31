from django.apps import AppConfig


class AiConfig(AppConfig):
    name = 'apps.ai'
    label = 'ai'
    verbose_name = 'AI'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import signals  # noqa: F401  (cache invalidation on config/price changes)
