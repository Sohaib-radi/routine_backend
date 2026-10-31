from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.response import Response

from .. import selectors, services
from .serializers import ProfileSerializer


class ProfileViewSet(viewsets.ModelViewSet):
    """
    Application profiles. Every signed-in user can list the active ones; creating,
    editing and deleting require the `profiles.*_profile` permissions.
    Selecting and deactivating a profile also change users, so those actions are
    served by accounts (same URLs: /profiles/{id}/select/ and /profiles/{id}/deactivate/).
    """

    serializer_class = ProfileSerializer
    permission_classes = [DjangoModelPermissions]
    pagination_class = None

    def get_queryset(self):
        return selectors.profiles_for(self.request.user)

    def perform_create(self, serializer):
        serializer.instance = services.create_profile(**serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.update_profile(serializer.instance, **serializer.validated_data)

    def perform_destroy(self, instance):
        services.delete_profile(instance)

    @extend_schema(request=None, responses=ProfileSerializer)
    @action(detail=True, methods=['post'], url_path='set-default')
    def set_default(self, request, pk=None):
        """Make this profile the one given to new users (admin)."""
        self._require_change_perm(request)
        profile = services.make_default(self.get_object())
        return Response(self.get_serializer(profile).data)

    @extend_schema(request=None, responses=ProfileSerializer)
    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        """Make an inactive profile selectable again (admin)."""
        self._require_change_perm(request)
        profile = services.activate_profile(self.get_object())
        return Response(self.get_serializer(profile).data)

    def _require_change_perm(self, request):
        if not request.user.has_perm('profiles.change_profile'):
            self.permission_denied(request)
