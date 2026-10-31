from django.contrib.auth.models import Group, Permission
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response

from apps.profiles import selectors as profile_selectors
from apps.profiles.api.serializers import ProfileSerializer

from .. import services
from ..models import User
from .permissions import StrictDjangoModelPermissions
from .serializers import (
    AdminUserSerializer,
    ChangePasswordSerializer,
    GroupSerializer,
    PermissionSerializer,
    RegisterSerializer,
    UserSerializer,
)


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def perform_create(self, serializer):
        serializer.instance = services.register_user(**serializer.validated_data)


class MeView(generics.RetrieveUpdateDestroyAPIView):
    """The authenticated user's own account: read it, change name/timezone/profile, delete it."""

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user

    def perform_update(self, serializer):
        serializer.instance = services.update_me(self.request.user, **serializer.validated_data)

    def perform_destroy(self, instance):
        services.delete_account(instance)


class ChangePasswordView(generics.GenericAPIView):
    serializer_class = ChangePasswordSerializer

    @extend_schema(responses={204: None})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_password(request.user, **serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserViewSet(viewsets.ModelViewSet):
    """User administration, gated by the `accounts.*_user` model permissions."""

    queryset = User.objects.select_related('profile').prefetch_related('groups', 'user_permissions')
    serializer_class = AdminUserSerializer
    permission_classes = [StrictDjangoModelPermissions]
    search_fields = ['email', 'first_name', 'last_name']

    def perform_create(self, serializer):
        serializer.instance = services.create_user(**serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.update_user(serializer.instance, **serializer.validated_data)


class GroupViewSet(viewsets.ModelViewSet):
    """Roles, gated by the `auth.*_group` model permissions."""

    queryset = Group.objects.prefetch_related('permissions')
    serializer_class = GroupSerializer
    permission_classes = [StrictDjangoModelPermissions]


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """Catalog of available permissions, for building role editors."""

    queryset = Permission.objects.select_related('content_type')
    serializer_class = PermissionSerializer
    permission_classes = [StrictDjangoModelPermissions]
    pagination_class = None


# Profile actions that write users. Served by accounts because profiles never
# imports accounts; the URLs sit with the other profile endpoints.

class ProfileSelectView(generics.GenericAPIView):
    """POST /profiles/{id}/select/: switch the signed-in user to this profile."""

    serializer_class = ProfileSerializer

    def get_queryset(self):
        return profile_selectors.profiles_for(self.request.user)

    @extend_schema(request=None)
    def post(self, request, pk):
        profile = self.get_object()
        services.select_profile(request.user, profile)
        return Response(self.get_serializer(profile).data)


class CanChangeProfiles(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm('profiles.change_profile')


class ProfileDeactivateView(generics.GenericAPIView):
    """
    POST /profiles/{id}/deactivate/ (admin): make the profile unselectable and move
    its users to the default profile. The default profile cannot be deactivated.
    """

    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated, CanChangeProfiles]

    def get_queryset(self):
        return profile_selectors.profiles_for(self.request.user)

    @extend_schema(request=None)
    def post(self, request, pk):
        profile = self.get_object()
        services.deactivate_profile(profile)
        profile.refresh_from_db()
        return Response(self.get_serializer(profile).data)
