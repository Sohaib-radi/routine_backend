"""
Writes for profiles (admin-defined presets). Other apps and the views call these.

profiles is the lowest app: it never imports accounts. Anything that also writes
users (selecting a profile, deactivating one and moving its users) lives in
accounts.services.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError

from .models import Profile

# Fields an administrator may set through create_profile / update_profile.
EDITABLE_FIELDS = {
    'name', 'description', 'color', 'avatar', 'position',
    'notification_style', 'notification_sound', 'notification_vibrate',
}


def _apply(profile, fields):
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise ValidationError(f'Unknown or read-only profile fields: {", ".join(sorted(unknown))}.')
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.full_clean()
    profile.save()
    return profile


def create_profile(**fields) -> Profile:
    """Create an active, non-default profile."""
    return _apply(Profile(), fields)


def update_profile(profile: Profile, **fields) -> Profile:
    """Edit a profile's presets. Activation state and the default flag have their own services."""
    return _apply(profile, fields)


def make_default(profile: Profile) -> Profile:
    """Make `profile` the one given to new users (and to users of deactivated profiles)."""
    profile.make_default()  # atomic, see Profile.make_default
    return profile


@transaction.atomic
def activate_profile(profile: Profile) -> Profile:
    """Make an inactive profile selectable again. Deactivation: accounts.services.deactivate_profile."""
    if not profile.is_active:
        profile.is_active = True
        profile.save(update_fields=['is_active', 'updated_at'])
    return profile


def ensure_can_deactivate(profile: Profile) -> None:
    """Raise if `profile` must stay active. Used by accounts.services.deactivate_profile."""
    if profile.is_default:
        raise ValidationError('The default profile cannot be deactivated.')


@transaction.atomic
def mark_inactive(profile: Profile) -> Profile:
    """
    Flip the flag only. Call accounts.services.deactivate_profile instead, which
    also moves the profile's users to the default profile in the same transaction.
    """
    ensure_can_deactivate(profile)
    profile.is_active = False
    profile.save(update_fields=['is_active', 'updated_at'])
    return profile


@transaction.atomic
def delete_profile(profile: Profile) -> None:
    """Delete an unused, non-default profile. Profiles in use must be deactivated instead."""
    if profile.is_default:
        raise ValidationError('The default profile cannot be deleted.')
    try:
        profile.delete()
    except ProtectedError:
        raise ValidationError('Users have selected this profile; deactivate it instead.') from None
