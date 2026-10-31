import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts import services
from apps.accounts.models import User
from apps.profiles import services as profile_services
from apps.profiles.models import Profile

pytestmark = pytest.mark.django_db

PASSWORD = 'S3cure-pass!'


@pytest.fixture
def default():
    return Profile.objects.default()


@pytest.fixture
def calm():
    return profile_services.create_profile(name='Calm')


# ----------------------------- user model ---------------------------------- #
def test_new_user_gets_default_profile_and_utc(default):
    user = User.objects.create_user('sara@example.com', PASSWORD)
    assert user.profile == default
    assert user.timezone == 'UTC'


def test_profile_column_is_not_null():
    user = User.objects.create_user('sara@example.com', PASSWORD)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(profile=None)


def test_profile_is_protected(calm):
    User.objects.create_user('sara@example.com', PASSWORD, profile=calm)
    with pytest.raises(Exception):  # ProtectedError
        calm.delete()


def test_user_pk_is_uuid():
    user = User.objects.create_user('sara@example.com', PASSWORD)
    assert len(str(user.pk)) == 36


# ----------------------------- register / me ------------------------------- #
def test_register_user_with_timezone(default):
    user = services.register_user(email='Sara@EXAMPLE.com', password=PASSWORD, timezone='Africa/Cairo')
    assert user.email == 'Sara@example.com'
    assert user.timezone == 'Africa/Cairo'
    assert user.profile == default
    assert user.check_password(PASSWORD)


@pytest.mark.parametrize('tz', ['Mars/Olympus', 'cairo', '+02:00', ''])
def test_register_rejects_invalid_timezone(tz):
    with pytest.raises(ValidationError) as exc:
        services.register_user(email='sara@example.com', password=PASSWORD, timezone=tz)
    assert 'timezone' in exc.value.message_dict


def test_register_rejects_weak_password():
    with pytest.raises(ValidationError) as exc:
        services.register_user(email='sara@example.com', password='123')
    assert 'password' in exc.value.message_dict
    assert not User.objects.exists()


def test_register_rejects_privileged_fields():
    with pytest.raises(ValidationError):
        services.register_user(email='sara@example.com', password=PASSWORD, is_staff=True)


def test_update_me_timezone_and_profile(calm):
    user = User.objects.create_user('sara@example.com', PASSWORD)
    services.update_me(user, timezone='Asia/Tokyo', profile=calm, first_name='Sara')
    user.refresh_from_db()
    assert (user.timezone, user.profile, user.first_name) == ('Asia/Tokyo', calm, 'Sara')


def test_update_me_rejects_invalid_timezone():
    user = User.objects.create_user('sara@example.com', PASSWORD)
    with pytest.raises(ValidationError):
        services.update_me(user, timezone='Nowhere/City')
    user.refresh_from_db()
    assert user.timezone == 'UTC'


def test_update_me_rejects_read_only_fields():
    user = User.objects.create_user('sara@example.com', PASSWORD)
    with pytest.raises(ValidationError):
        services.update_me(user, is_staff=True)


def test_change_password():
    user = User.objects.create_user('sara@example.com', PASSWORD)
    with pytest.raises(ValidationError):
        services.change_password(user, old_password='wrong', new_password='An0ther-pass!')
    services.change_password(user, old_password=PASSWORD, new_password='An0ther-pass!')
    user.refresh_from_db()
    assert user.check_password('An0ther-pass!')


def test_staff_create_and_update_user(calm):
    user = services.create_user(email='new@example.com', password=PASSWORD, timezone='Europe/Paris')
    assert user.timezone == 'Europe/Paris'
    services.update_user(user, profile=calm, password='An0ther-pass!')
    user.refresh_from_db()
    assert user.profile == calm and user.check_password('An0ther-pass!')


# ----------------------------- select_profile ------------------------------ #
def test_select_profile(calm):
    user = User.objects.create_user('sara@example.com', PASSWORD)
    services.select_profile(user, calm)
    user.refresh_from_db()
    assert user.profile == calm


def test_select_inactive_profile_is_refused(calm, default):
    user = User.objects.create_user('sara@example.com', PASSWORD)
    services.deactivate_profile(calm)
    with pytest.raises(ValidationError):
        services.select_profile(user, calm)
    user.refresh_from_db()
    assert user.profile == default


# ----------------------------- deactivate_profile -------------------------- #
def test_deactivate_profile_moves_users_to_default(calm, default):
    on_calm = [User.objects.create_user(f'u{i}@example.com', PASSWORD, profile=calm) for i in range(3)]
    other = User.objects.create_user('other@example.com', PASSWORD)  # on default already

    moved = services.deactivate_profile(calm)

    assert moved == 3
    calm.refresh_from_db()
    assert not calm.is_active
    for user in [*on_calm, other]:
        user.refresh_from_db()
        assert user.profile == default
    assert not User.objects.filter(profile__is_active=False).exists()


def test_deactivate_profile_without_users(calm):
    assert services.deactivate_profile(calm) == 0
    calm.refresh_from_db()
    assert not calm.is_active


def test_deactivate_default_is_refused(default):
    user = User.objects.create_user('sara@example.com', PASSWORD)
    with pytest.raises(ValidationError, match='default'):
        services.deactivate_profile(default)
    default.refresh_from_db()
    user.refresh_from_db()
    assert default.is_active and user.profile == default


def test_deactivate_uses_the_current_default(calm):
    """The flag is re-read under lock: a profile made default meanwhile is refused."""
    stale = Profile.objects.get(pk=calm.pk)
    profile_services.make_default(calm)
    with pytest.raises(ValidationError):
        services.deactivate_profile(stale)  # stale copy still says is_default=False


def test_deactivate_moves_users_to_new_default(calm, default):
    focus = profile_services.create_profile(name='Focus')
    profile_services.make_default(focus)
    user = User.objects.create_user('sara@example.com', PASSWORD, profile=calm)
    services.deactivate_profile(calm)
    user.refresh_from_db()
    assert user.profile == focus


def test_deactivate_profile_is_atomic(calm, monkeypatch):
    """If flipping the flag fails, the users are not moved either."""
    user = User.objects.create_user('sara@example.com', PASSWORD, profile=calm)

    def boom(profile):
        raise RuntimeError('db down')

    monkeypatch.setattr(profile_services, 'mark_inactive', boom)
    with pytest.raises(RuntimeError):
        services.deactivate_profile(calm)

    user.refresh_from_db()
    calm.refresh_from_db()
    assert user.profile == calm and calm.is_active
