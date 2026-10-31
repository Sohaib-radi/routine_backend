"""
Writes for accounts. accounts sits above profiles, so it owns every write that
touches both a user and a profile (selection, deactivation).
"""

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.profiles import services as profile_services
from apps.profiles.models import Profile

from .models import User, validate_timezone

# Fields a user may change on their own account (PATCH /me/).
SELF_EDITABLE_FIELDS = {'first_name', 'last_name', 'timezone'}


def _check_timezone(fields):
    if 'timezone' in fields:
        try:
            validate_timezone(fields['timezone'])
        except ValidationError as exc:
            raise ValidationError({'timezone': exc.messages}) from None


# ----------------------------- accounts ------------------------------------ #
@transaction.atomic
def register_user(*, email: str, password: str, **fields) -> User:
    """Self sign-up. Starts on the default profile; timezone defaults to UTC."""
    unknown = set(fields) - SELF_EDITABLE_FIELDS
    if unknown:
        raise ValidationError(f'Unknown fields: {", ".join(sorted(unknown))}.')
    _check_timezone(fields)
    candidate = User(email=User.objects.normalize_email(email), **fields)
    try:
        password_validation.validate_password(password, candidate)
    except ValidationError as exc:
        raise ValidationError({'password': exc.messages}) from None
    return User.objects.create_user(email, password, **fields)


@transaction.atomic
def update_me(user: User, *, profile: Profile | None = None, **fields) -> User:
    """A user editing their own account: name, timezone, selected profile."""
    unknown = set(fields) - SELF_EDITABLE_FIELDS
    if unknown:
        raise ValidationError(f'Unknown or read-only fields: {", ".join(sorted(unknown))}.')
    _check_timezone(fields)
    for name, value in fields.items():
        setattr(user, name, value)
    if fields:
        user.save(update_fields=list(fields))
    if profile is not None and profile.pk != user.profile_id:
        select_profile(user, profile)
    return user


def change_password(user: User, *, old_password: str, new_password: str) -> None:
    if not user.check_password(old_password):
        raise ValidationError({'old_password': 'Old password is incorrect.'})
    try:
        password_validation.validate_password(new_password, user)
    except ValidationError as exc:
        raise ValidationError({'new_password': exc.messages}) from None
    user.set_password(new_password)
    user.save(update_fields=['password'])


def delete_account(user: User) -> None:
    """The user deletes their own account; their tracking data cascades with it."""
    user.delete()


# ----------------------------- staff user management ----------------------- #
@transaction.atomic
def create_user(*, email: str, password: str | None = None, groups=(), user_permissions=(), **fields) -> User:
    """Staff creating an account (staff API). Validates the password and timezone."""
    _check_timezone(fields)
    if 'profile' in fields and not fields['profile'].is_active:
        raise ValidationError({'profile': 'This profile is not available.'})
    if password:
        try:
            password_validation.validate_password(password, User(email=email, **fields))
        except ValidationError as exc:
            raise ValidationError({'password': exc.messages}) from None
    user = User.objects.create_user(email, password, **fields)
    user.groups.set(groups)
    user.user_permissions.set(user_permissions)
    return user


@transaction.atomic
def update_user(user: User, *, password: str | None = None, groups=None, user_permissions=None, **fields) -> User:
    """Staff editing an account (staff API)."""
    _check_timezone(fields)
    profile = fields.pop('profile', None)
    for name, value in fields.items():
        setattr(user, name, value)
    if password:
        try:
            password_validation.validate_password(password, user)
        except ValidationError as exc:
            raise ValidationError({'password': exc.messages}) from None
        user.set_password(password)
    user.save()
    if profile is not None and profile.pk != user.profile_id:
        select_profile(user, profile)
    if groups is not None:
        user.groups.set(groups)
    if user_permissions is not None:
        user.user_permissions.set(user_permissions)
    return user


# ----------------------------- profiles ------------------------------------ #
@transaction.atomic
def select_profile(user: User, profile: Profile) -> User:
    """Switch `user` to `profile`. Only active profiles can be selected."""
    profile = Profile.objects.select_for_update().get(pk=profile.pk)  # serialises with deactivate_profile
    if not profile.is_active:
        raise ValidationError({'profile': 'This profile is not available.'})
    user.profile = profile
    user.save(update_fields=['profile'])
    return user


@transaction.atomic
def deactivate_profile(profile: Profile) -> int:
    """
    Make `profile` unselectable and move its users to the default profile, in one
    transaction. Refuses the default profile. Returns the number of users moved.

    Lives here, not in profiles.services: it writes users, and profiles never
    imports accounts.
    """
    # Lock the target first, then the default: select_profile and make_default
    # take the same locks, so neither can interleave with the move.
    profile = Profile.objects.select_for_update().get(pk=profile.pk)
    profile_services.ensure_can_deactivate(profile)  # re-checked under the lock
    default = Profile.objects.select_for_update().get(is_default=True)

    moved = User.objects.filter(profile=profile).update(profile=default)
    profile_services.mark_inactive(profile)
    return moved
