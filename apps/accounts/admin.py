"""
Admin for accounts, styled with Unfold.

Every admin uses Unfold's classes. ``Group`` and SimpleJWT's token models are
registered by Django and SimpleJWT with the plain ``ModelAdmin``, so they are
unregistered and registered again with Unfold, keeping their behaviour.
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.token_blacklist import admin as jwt_admin
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from unfold.admin import ModelAdmin
from unfold.decorators import action

from apps.profiles.admin import ProfileAdmin as BaseProfileAdmin
from apps.profiles.models import Profile

from . import services

from .forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from .models import User

admin.site.unregister(Group)
admin.site.unregister(OutstandingToken)
admin.site.unregister(BlacklistedToken)
admin.site.unregister(Profile)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    ordering = ['email']
    list_display = ['email', 'first_name', 'last_name', 'timezone', 'profile', 'is_staff', 'is_active']
    list_filter = ['is_active', 'is_staff', 'is_superuser', 'profile', 'groups']
    list_select_related = ['profile']
    search_fields = ['email', 'first_name', 'last_name']
    filter_horizontal = ['groups', 'user_permissions']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'timezone', 'profile')}),
        (_('Permissions'), {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
            'classes': ['collapse'],
        }),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'usable_password', 'password1', 'password2')}),
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'profile':
            kwargs['queryset'] = Profile.objects.active()  # inactive profiles cannot be selected
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Profile)
class ProfileAdmin(BaseProfileAdmin):
    """profiles' admin plus "Deactivate", which moves users and so belongs to accounts."""

    actions_detail = [*BaseProfileAdmin.actions_detail, 'deactivate_detail']

    @action(description=_('Deactivate'), url_path='deactivate', permissions=['change'])
    def deactivate_detail(self, request, object_id):
        profile = self.get_object(request, object_id)
        try:
            moved = services.deactivate_profile(profile)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(
                request, _('“%(name)s” deactivated; %(count)d user(s) moved to the default profile.')
                % {'name': profile.name, 'count': moved},
            )
        return redirect('admin:profiles_profile_change', object_id)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    """Roles: named bundles of permissions."""


@admin.register(OutstandingToken)
class OutstandingTokenAdmin(jwt_admin.OutstandingTokenAdmin, ModelAdmin):
    """Refresh tokens issued to users (read-only, from SimpleJWT)."""


@admin.register(BlacklistedToken)
class BlacklistedTokenAdmin(jwt_admin.BlacklistedTokenAdmin, ModelAdmin):
    """Refresh tokens revoked by logout or rotation (from SimpleJWT)."""
