import uuid
from functools import cache
from zoneinfo import available_timezones

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models

from apps.profiles.models import Profile


@cache
def _iana_timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_timezone(value):
    """Accept IANA zone names only (e.g. 'Africa/Cairo')."""
    if value not in _iana_timezones():
        raise ValidationError('“%(value)s” is not a valid IANA time zone.', params={'value': value})


class UserManager(BaseUserManager):
    """Manager for a user model that logs in with email instead of username."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('The email must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Account owner. Authenticates with email; holds roles (groups) and permissions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField('email address', unique=True)
    timezone = models.CharField(
        max_length=64, default='UTC', validators=[validate_timezone],
        help_text='IANA time zone, e.g. Africa/Cairo. Defines the user\'s "today".',
    )
    profile = models.ForeignKey(
        Profile, on_delete=models.PROTECT, related_name='users',
        help_text='The application profile this user selected. Always an active profile. '
                  'New users get the default profile.',
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ['email']

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # Not a field default: a callable default would query the database on every
        # User() instantiation, including Django's system checks before migrate.
        if self._state.adding and self.profile_id is None:
            self.profile = Profile.objects.default()
        super().save(*args, **kwargs)
