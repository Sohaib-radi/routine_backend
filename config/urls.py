from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1 = [
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('', include('apps.accounts.api.urls')),
    path('', include('apps.profiles.api.urls')),
]

urlpatterns = [
    # The site root opens the admin (its login page when signed out).
    path('', RedirectView.as_view(pattern_name='admin:index'), name='root'),
    path('admin/', admin.site.urls),
    path('i18n/', include('django.conf.urls.i18n')),  # language switcher in the admin
    path('api/v1/', include(api_v1)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
