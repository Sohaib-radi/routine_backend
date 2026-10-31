"""
Idempotency-Key support for non-idempotent POST endpoints.

A client (typically the mobile app on a flaky network) sends a unique
`Idempotency-Key` header per user action. A retry with the same key replays the
first response instead of running the action again, so "+2 glasses" retried
twice is still +2. Keys are scoped per user and kept 24 hours in the cache (Redis).

- Same key, same request  -> stored response replayed (header Idempotent-Replayed: true)
- Same key, other request -> 422
- Same key while the first request is still running -> 409
- Only successful (2xx) responses are stored; after an error the client may retry.
- No header -> the request runs normally.
"""

import functools
import hashlib
import json

from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter
from rest_framework import status
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder

HEADER = 'Idempotency-Key'
TTL_SECONDS = 24 * 60 * 60
LOCK_SECONDS = 30
MAX_KEY_LENGTH = 255

SCHEMA_PARAMETER = OpenApiParameter(
    name=HEADER, type=str, location=OpenApiParameter.HEADER, required=False,
    description='Unique per user action (e.g. a UUID). Retries with the same key replay the first '
                'response for 24 hours instead of recording the action twice.',
)


def _fingerprint(request) -> str:
    body = json.dumps(request.data, sort_keys=True, default=str)
    return hashlib.sha256(f'{request.method} {request.path}\n{body}'.encode()).hexdigest()


def idempotent(view_method):
    """Decorate an APIView/ViewSet method (post/create/action) to honour Idempotency-Key."""

    @functools.wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        key = request.headers.get(HEADER)
        if not key:
            return view_method(self, request, *args, **kwargs)
        if len(key) > MAX_KEY_LENGTH:
            return Response({'detail': f'{HEADER} is longer than {MAX_KEY_LENGTH} characters.'},
                            status=status.HTTP_400_BAD_REQUEST)

        cache_key = f'idempotency:{request.user.pk}:{hashlib.sha256(key.encode()).hexdigest()}'
        fingerprint = _fingerprint(request)

        stored = cache.get(cache_key)
        if stored is not None:
            if stored['fingerprint'] != fingerprint:
                return Response({'detail': f'This {HEADER} was already used for a different request.'},
                                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            return Response(stored['data'], status=stored['status'], headers={'Idempotent-Replayed': 'true'})

        lock_key = f'{cache_key}:lock'
        if not cache.add(lock_key, 1, timeout=LOCK_SECONDS):
            return Response({'detail': 'A request with this Idempotency-Key is already in progress.'},
                            status=status.HTTP_409_CONFLICT)
        try:
            response = view_method(self, request, *args, **kwargs)
            if 200 <= response.status_code < 300:
                # Plain JSON types: ReturnDict holds its serializer and does not pickle cleanly.
                data = json.loads(json.dumps(response.data, cls=JSONEncoder))
                cache.set(cache_key, {'fingerprint': fingerprint, 'status': response.status_code,
                                      'data': data}, timeout=TTL_SECONDS)
            return response
        finally:
            cache.delete(lock_key)

    return wrapper
