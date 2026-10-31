from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register('trackables', views.TrackableViewSet, basename='trackable')
router.register('checkins', views.CheckInViewSet, basename='checkin')
router.register('pauses', views.PauseViewSet, basename='pause')

urlpatterns = [
    path('today/', views.TodayView.as_view(), name='tracking-today'),
    path('', include(router.urls)),
]
