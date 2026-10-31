from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.include_root_view = False
router.register('meal-drafts', views.MealDraftViewSet, basename='meal-draft')

urlpatterns = [path('', include(router.urls))]
