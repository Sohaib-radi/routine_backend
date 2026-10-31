"""Shared pytest fixtures."""

import importlib
from datetime import datetime, timezone as dt_timezone

import pytest
from django.apps import apps as django_apps

from apps.accounts.models import User


@pytest.fixture(scope='session')
def django_db_setup(django_db_setup, django_db_blocker):
    """
    Transactional tests flush the database on teardown, which also removes rows
    created by data migrations. Re-run them so a reused test database (--reuse-db)
    always starts with its default profile.
    """
    migration = importlib.import_module('apps.profiles.migrations.0002_create_default_profile')
    with django_db_blocker.unblock():
        migration.create_default_profile(django_apps, None)


@pytest.fixture
def make_user(db):
    counter = iter(range(1, 10_000))

    def make(**fields):
        fields.setdefault('email', f'user{next(counter)}@example.com')
        email = fields.pop('email')
        return User.objects.create_user(email, 'S3cure-pass!', **fields)

    return make


@pytest.fixture
def user(make_user):
    return make_user(timezone='UTC')


@pytest.fixture
def freeze_utc(monkeypatch):
    """freeze_utc(2026, 10, 26, 23, 30): make timezone.now() return that UTC instant."""

    def freeze(*args):
        instant = datetime(*args, tzinfo=dt_timezone.utc)
        monkeypatch.setattr('django.utils.timezone.now', lambda: instant)
        return instant

    return freeze
