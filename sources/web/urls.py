"""Web URLs."""

from django.urls import path
from web import views

urlpatterns = [
    path('', views.index, name='index'),
    path('library/', views.library, name='library'),
    path('healthz/', views.healthz, name='healthz'),
]
