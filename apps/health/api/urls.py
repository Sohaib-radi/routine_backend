from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.include_root_view = False
router.register('foods', views.FoodViewSet, basename='food')
router.register('meals', views.MealViewSet, basename='meal')

urlpatterns = [
    path('plan/', views.PlanView.as_view(), name='health-plan'),
    path('plan/preview/', views.PlanPreviewView.as_view(), name='health-plan-preview'),
    path('plan/history/', views.PlanHistoryView.as_view(), name='health-plan-history'),
    path('weight/', views.WeightView.as_view(), name='health-weight'),
    path('meals/<uuid:pk>/items/', views.MealItemsView.as_view(), name='meal-items'),
    path('meals/<uuid:pk>/items/<uuid:item_pk>/', views.MealItemDetailView.as_view(), name='meal-item-detail'),
    path('nutrition/', views.NutritionView.as_view(), name='health-nutrition'),
    path('', include(router.urls)),
]
