from django.contrib import admin, messages
from django.shortcuts import redirect
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import action, display

from . import services
from .models import Profile


@admin.register(Profile)
class ProfileAdmin(ModelAdmin):
    list_display = [
        'name', 'color_swatch', 'notification_style', 'notification_sound', 'notification_vibrate',
        'position', 'is_active', 'is_default', 'user_count',
    ]
    list_editable = ['position']
    list_filter = ['is_active', 'is_default', 'notification_style', 'notification_sound']
    search_fields = ['name', 'description']
    # Activation and the default flag change only through actions, so users stay consistent.
    # The "Deactivate" action is added by accounts/admin.py: it moves users too.
    readonly_fields = ['is_active', 'is_default', 'created_at', 'updated_at']
    actions_detail = ['make_default_detail', 'activate_detail']
    fieldsets = (
        (None, {'fields': ('name', 'description', 'position', 'is_active', 'is_default')}),
        (_('Appearance'), {'fields': ('color', 'avatar')}),
        (_('Notifications'), {'fields': ('notification_style', 'notification_sound', 'notification_vibrate')}),
        (_('Dates'), {'fields': ('created_at', 'updated_at')}),
    )

    @display(description=_('Color'))
    def color_swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:1rem;height:1rem;border-radius:9999px;'
            'background:{};vertical-align:middle"></span> {}', obj.color, obj.color,
        )

    @display(description=_('Users'))
    def user_count(self, obj):
        return obj.users.count()

    @action(description=_('Make default'), url_path='make-default', permissions=['change'])
    def make_default_detail(self, request, object_id):
        profile = services.make_default(self.get_object(request, object_id))
        messages.success(request, _('“%s” is now the default profile.') % profile.name)
        return redirect('admin:profiles_profile_change', object_id)

    @action(description=_('Activate'), url_path='activate', permissions=['change'])
    def activate_detail(self, request, object_id):
        profile = services.activate_profile(self.get_object(request, object_id))
        messages.success(request, _('“%s” can be selected again.') % profile.name)
        return redirect('admin:profiles_profile_change', object_id)

    def save_model(self, request, obj, form, change):
        fields = {name: value for name, value in form.cleaned_data.items() if name in services.EDITABLE_FIELDS}
        if change:
            services.update_profile(obj, **fields)
        else:
            created = services.create_profile(**fields)
            obj.pk, obj._state.adding = created.pk, False  # the admin logs and redirects to `obj`

    def delete_model(self, request, obj):
        services.delete_profile(obj)

    def get_actions(self, request, action_location=admin.ActionLocation.CHANGE_LIST):
        # Bulk delete would bypass the default/in-use checks below.
        actions = super().get_actions(request, action_location)
        actions.pop('delete_selected', None)
        return actions

    def has_delete_permission(self, request, obj=None):
        # In use or default: deactivate instead, so users keep a valid profile.
        if obj is not None and (obj.is_default or obj.users.exists()):
            return False
        return super().has_delete_permission(request, obj)
