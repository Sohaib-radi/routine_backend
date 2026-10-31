import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.profiles import services
from apps.profiles.models import Profile

pytestmark = pytest.mark.django_db


@pytest.fixture
def default():
    return Profile.objects.default()  # created by the data migration


def test_data_migration_creates_exactly_one_default(default):
    assert default is not None
    assert default.name == 'Default'
    assert default.is_active
    assert Profile.objects.filter(is_default=True).count() == 1


def test_create_profile_is_active_and_not_default():
    profile = services.create_profile(name='Calm', color='#2B2350', notification_style='silent')
    assert profile.is_active and not profile.is_default


def test_create_profile_validates_color():
    with pytest.raises(ValidationError):
        services.create_profile(name='Bad', color='green')


def test_update_profile_rejects_flag_fields():
    profile = services.create_profile(name='Calm')
    with pytest.raises(ValidationError):
        services.update_profile(profile, is_default=True)
    with pytest.raises(ValidationError):
        services.update_profile(profile, is_active=False)


def test_update_profile_changes_presets():
    profile = services.update_profile(services.create_profile(name='Calm'), color='#000000', position=3)
    profile.refresh_from_db()
    assert (profile.color, profile.position) == ('#000000', 3)


def test_make_default_moves_the_flag_and_activates(default):
    focus = services.create_profile(name='Focus')
    Profile.objects.filter(pk=focus.pk).update(is_active=False)
    focus.refresh_from_db()

    services.make_default(focus)

    default.refresh_from_db()
    focus.refresh_from_db()
    assert focus.is_default and focus.is_active
    assert not default.is_default
    assert Profile.objects.filter(is_default=True).count() == 1


def test_make_default_is_atomic(default, monkeypatch):
    """If saving the new default fails, the old default keeps its flag."""
    focus = services.create_profile(name='Focus')

    def boom(*args, **kwargs):
        raise RuntimeError('db down')

    monkeypatch.setattr(Profile, 'save', boom)
    with pytest.raises(RuntimeError):
        services.make_default(focus)
    monkeypatch.undo()

    default.refresh_from_db()
    assert default.is_default


def test_make_default_on_current_default_is_a_no_op(default):
    services.make_default(default)
    default.refresh_from_db()
    assert default.is_default


def test_activate_profile():
    profile = services.create_profile(name='Calm')
    Profile.objects.filter(pk=profile.pk).update(is_active=False)
    profile.refresh_from_db()
    assert services.activate_profile(profile).is_active


def test_mark_inactive_refuses_default(default):
    with pytest.raises(ValidationError):
        services.mark_inactive(default)


def test_delete_unused_profile():
    profile = services.create_profile(name='Calm')
    services.delete_profile(profile)
    assert not Profile.objects.filter(name='Calm').exists()


def test_delete_refuses_default(default):
    with pytest.raises(ValidationError, match='default'):
        services.delete_profile(default)


def test_delete_refuses_profile_in_use():
    profile = services.create_profile(name='Calm')
    User.objects.create_user('sara@example.com', 'S3cure-pass!', profile=profile)
    with pytest.raises(ValidationError, match='deactivate'):
        services.delete_profile(profile)
    assert Profile.objects.filter(pk=profile.pk).exists()
