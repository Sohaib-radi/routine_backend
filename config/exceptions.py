"""DRF exception handler: services raise Django's ValidationError; the API returns 400."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, 'error_dict') else exc.messages
        exc = ValidationError(detail)
    return drf_exception_handler(exc, context)
