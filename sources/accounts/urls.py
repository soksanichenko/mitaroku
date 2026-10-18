"""Account URLs."""

from accounts import views
from django.urls import path

urlpatterns = [
    path('language/', views.set_language, name='set_language'),
]
