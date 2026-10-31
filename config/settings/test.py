"""Test run (pytest): fast hashing, Celery tasks run inline, a throwaway secret."""

import os

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-not-for-production-use')
os.environ.setdefault('DATABASE_URL', 'postgres://routine:routine@localhost:5432/routine')

from .base import *  # noqa: E402,F401,F403

DEBUG = False
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Tasks queued with .delay() run immediately, in-process; no broker needed.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = 'memory://'

CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

MEDIA_ROOT = BASE_DIR / '.test_media'  # noqa: F405
