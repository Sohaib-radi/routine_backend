"""Read queries for profiles."""

from .models import Profile


def profiles_for(user):
    """Profiles `user` may see: all of them for profile managers, active ones otherwise."""
    if user.has_perm('profiles.change_profile'):
        return Profile.objects.all()
    return Profile.objects.active()


def default_profile() -> Profile | None:
    return Profile.objects.default()
