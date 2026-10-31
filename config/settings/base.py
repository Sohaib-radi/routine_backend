"""
Settings shared by every environment. dev.py, test.py and prod.py import this
module and override what differs. Values that vary per machine or deployment
come from environment variables (see .env.example), read with django-environ.
"""

from datetime import timedelta
from pathlib import Path

import environ
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')  # optional; real environment variables win

SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])


# Application definition

INSTALLED_APPS = [
    'unfold',  # must come before django.contrib.admin
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',  # pg_trgm search (health foods)
    # Third party
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    # Local (dependency direction: ai -> health -> tracking -> accounts -> profiles)
    'apps.profiles',
    'apps.accounts',
    'apps.tracking',
    'apps.health',
    'apps.ai',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # project overrides win over Unfold's templates
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database: PostgreSQL, e.g. postgres://routine:routine@localhost:5432/routine
DATABASES = {'default': env.db('DATABASE_URL')}
DATABASES['default']['CONN_MAX_AGE'] = env.int('DB_CONN_MAX_AGE', default=60)
DATABASES['default']['CONN_HEALTH_CHECKS'] = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom user model (email login, UUID primary key)
AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'  # server time; user-facing dates use User.timezone
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ('ar', _('Arabic')),
    ('en', _('English')),
    ('fr', _('French')),
]

# Earlier paths win: our own catalogs first, then the Unfold translations.
LOCALE_PATHS = [
    BASE_DIR / 'locale',
    BASE_DIR / 'locale_vendor' / 'unfold',
]


# Static and uploaded files

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']  # admin theme: fonts, stylesheet, login image

MEDIA_URL = 'media/'
MEDIA_ROOT = env.path('MEDIA_ROOT', default=BASE_DIR / 'media')


# REST API

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'rest_framework.filters.SearchFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'config.exceptions.exception_handler',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
}

# OpenAPI schema: the web and mobile clients are generated from it.
SPECTACULAR_SETTINGS = {
    'TITLE': 'Routine API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,  # separate request/response components for generated clients
    'SCHEMA_PATH_PREFIX': r'/api/v1',
    'ENUM_NAME_OVERRIDES': {
        'PlanModeEnum': 'apps.health.calculations.Mode',  # used by both mode and water_mode
        'PortionUnitEnum': 'apps.health.models.PortionUnit',  # unit and portion fields
        'BmiCategoryEnum': 'apps.health.selectors.BMI_CATEGORY_CHOICES',
        'FoodCategoryEnum': 'apps.health.models.Food.Category',
    },
}


# Redis: Celery broker (db 0) and the Django cache (db 1: idempotency keys, ...).

REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': env('CACHE_URL', default='redis://localhost:6379/1'),
        'KEY_PREFIX': 'routine',
    },
}

# Celery. Heavy work never runs in the request.
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default=REDIS_URL)
CELERY_RESULT_BACKEND = None  # tasks write their results to the database
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TIMEZONE = 'UTC'

# Celery beat (`celery -A config beat`): periodic tasks.
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    'recompute-streaks-after-local-midnight': {
        'task': 'apps.tracking.tasks.recompute_streaks_after_local_midnight',
        'schedule': crontab(minute=1),  # every hour, just after the full hour
    },
}


# Email

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}


# Admin theme (Unfold)
from config.unfold import UNFOLD  # noqa: E402,F401  (read by Unfold from settings)


# AI (apps.ai). Defaults for features without an AIFeatureConfig row in the admin.
# API keys come from the environment only (ANTHROPIC_API_KEY, read by the SDK) and
# are never stored in the database.
AI_DEFAULTS = {
    'enabled': env.bool('AI_ENABLED', default=True),
    'provider': env('AI_PROVIDER', default='anthropic'),
    'model': env('AI_MODEL', default='claude-haiku-5-5'),
    'effort': env('AI_EFFORT', default='low'),
    'daily_limit': env.int('AI_DAILY_LIMIT', default=20),
}
AI_PROVIDER_KEY_ENV = {'anthropic': 'ANTHROPIC_API_KEY'}  # provider -> env variable holding its key
AI_FORCE_PROVIDER = env('AI_FORCE_PROVIDER', default='')  # tests force 'fake' whatever the admin says
AI_TIMEOUT_SECONDS = env.float('AI_TIMEOUT_SECONDS', default=20.0)  # per provider HTTP request
AI_PROVIDER_MAX_RETRIES = env.int('AI_PROVIDER_MAX_RETRIES', default=1)  # SDK retries (429, 5xx, network)
AI_DRAFT_TTL_HOURS = env.int('AI_DRAFT_TTL_HOURS', default=24)
AI_STUCK_AFTER_MINUTES = env.int('AI_STUCK_AFTER_MINUTES', default=5)  # processing longer -> failed
