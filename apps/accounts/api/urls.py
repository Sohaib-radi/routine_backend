from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView

from . import views

router = DefaultRouter()
router.register('users', views.UserViewSet, basename='user')
router.register('roles', views.GroupViewSet, basename='role')
router.register('permissions', views.PermissionViewSet, basename='permission')

urlpatterns = [
    path('auth/register/', views.RegisterView.as_view(), name='register'),
    path('auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', TokenBlacklistView.as_view(), name='token_blacklist'),
    path('me/', views.MeView.as_view(), name='me'),
    path('me/change-password/', views.ChangePasswordView.as_view(), name='change_password'),
    path('profiles/<uuid:pk>/select/', views.ProfileSelectView.as_view(), name='profile-select'),
    path('profiles/<uuid:pk>/deactivate/', views.ProfileDeactivateView.as_view(), name='profile-deactivate'),
    path('', include(router.urls)),
]
