import uuid

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import Q

hex_color_validator = RegexValidator(
    regex=r'^#[0-9A-Fa-f]{6}$',
    message='Color must be a hex value like #728668.',
)


class ProfileQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def default(self):
        return self.filter(is_default=True).first()


class Profile(models.Model):
    """
    An application profile, configured by administrators: the colour, avatar and
    notification behaviour the apps apply. Each user selects one and can switch.
    """

    class NotificationStyle(models.TextChoices):
        BANNER = 'banner', 'Banner'
        ALERT = 'alert', 'Alert'
        BADGE = 'badge', 'Badge only'
        SILENT = 'silent', 'Silent'

    class NotificationSound(models.TextChoices):
        DEFAULT = 'default', 'Default'
        CHIME = 'chime', 'Chime'
        BELL = 'bell', 'Bell'
        DIGITAL = 'digital', 'Digital'
        SOFT = 'soft', 'Soft'
        NONE = 'none', 'None'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=255, blank=True)
    color = models.CharField(max_length=7, default='#728668', validators=[hex_color_validator])
    avatar = models.ImageField(upload_to='profiles/avatars/', blank=True)
    notification_style = models.CharField(
        max_length=20, choices=NotificationStyle, default=NotificationStyle.BANNER
    )
    notification_sound = models.CharField(
        max_length=20, choices=NotificationSound, default=NotificationSound.DEFAULT
    )
    notification_vibrate = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0, help_text='Order in which the apps list the profiles.')
    is_active = models.BooleanField(default=True, help_text='Inactive profiles cannot be selected.')
    is_default = models.BooleanField(
        default=False, help_text='Given to new users, and to users whose profile is deactivated.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProfileQuerySet.as_manager()

    class Meta:
        ordering = ['position', 'name']
        constraints = [
            models.UniqueConstraint(fields=['is_default'], condition=Q(is_default=True), name='one_default_profile'),
            models.CheckConstraint(
                condition=Q(is_default=False) | Q(is_active=True), name='default_profile_is_active'
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.is_default and not self.is_active:
            raise ValidationError({'is_active': 'The default profile cannot be deactivated.'})

    @transaction.atomic
    def make_default(self):
        """Make this the only default profile (and active). Callers use profiles.services.make_default."""
        Profile.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        self.is_default = True
        self.is_active = True
        self.save(update_fields=['is_default', 'is_active', 'updated_at'])
