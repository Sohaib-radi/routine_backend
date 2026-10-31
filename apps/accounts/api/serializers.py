from django.contrib.auth.models import Group, Permission
from rest_framework import serializers

from apps.profiles.api.serializers import ProfileSerializer
from apps.profiles.models import Profile

from ..models import User


class PermissionSerializer(serializers.ModelSerializer):
    app_label = serializers.CharField(source='content_type.app_label', read_only=True)
    model = serializers.CharField(source='content_type.model', read_only=True)

    class Meta:
        model = Permission
        fields = ['id', 'name', 'codename', 'app_label', 'model']


class GroupSerializer(serializers.ModelSerializer):
    """A role: a named bundle of permissions."""

    permissions = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Permission.objects.all(), required=False
    )

    class Meta:
        model = Group
        fields = ['id', 'name', 'permissions']


class UserSerializer(serializers.ModelSerializer):
    """
    The current user's own account (GET/PATCH /me/).
    Read `profile` (nested); switch it by writing `profile_id`.
    """

    groups = serializers.SlugRelatedField(many=True, slug_field='name', read_only=True)
    permissions = serializers.SerializerMethodField()
    profile = ProfileSerializer(read_only=True)
    profile_id = serializers.PrimaryKeyRelatedField(
        source='profile', queryset=Profile.objects.active(), write_only=True, required=False,
        help_text='Select one of the active profiles.',
    )

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'timezone', 'profile', 'profile_id',
            'is_staff', 'date_joined', 'groups', 'permissions',
        ]
        read_only_fields = ['id', 'email', 'is_staff', 'date_joined']

    def get_permissions(self, obj) -> list[str]:
        return sorted(obj.get_all_permissions())


class AdminUserSerializer(serializers.ModelSerializer):
    """Full user management, for staff with the right model permissions."""

    password = serializers.CharField(write_only=True, required=False)
    groups = serializers.PrimaryKeyRelatedField(many=True, queryset=Group.objects.all(), required=False)
    user_permissions = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Permission.objects.all(), required=False
    )
    profile = serializers.PrimaryKeyRelatedField(queryset=Profile.objects.active(), required=False)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'password', 'first_name', 'last_name', 'timezone', 'profile',
            'is_active', 'is_staff', 'date_joined', 'last_login',
            'groups', 'user_permissions',
        ]
        read_only_fields = ['id', 'date_joined', 'last_login']


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'password', 'first_name', 'last_name', 'timezone']
        read_only_fields = ['id']
        extra_kwargs = {'timezone': {'required': False}}


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
