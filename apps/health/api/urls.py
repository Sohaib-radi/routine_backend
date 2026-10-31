from django.urls import path

from . import views

urlpatterns = [
    path('plan/', views.PlanView.as_view(), name='health-plan'),
    path('plan/preview/', views.PlanPreviewView.as_view(), name='health-plan-preview'),
    path('plan/history/', views.PlanHistoryView.as_view(), name='health-plan-history'),
    path('weight/', views.WeightView.as_view(), name='health-weight'),
]
