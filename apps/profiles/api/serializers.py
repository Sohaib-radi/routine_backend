from rest_framework import serializers

from ..models import Profile


class ProfileSerializer(serializers.ModelSerializer):
    """
    A profile preset. `is_active` and `is_default` change only through the
    activate / deactivate / set-default actions, which keep users consistent.
    """

    class Meta:
        model = Profile
        fields = [
            'id', 'name', 'description', 'color', 'avatar',
            'notification_style', 'notification_sound', 'notification_vibrate',
            'position', 'is_active', 'is_default', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'is_active', 'is_default', 'created_at', 'updated_at']
