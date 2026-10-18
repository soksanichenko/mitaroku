"""Account URLs."""

from accounts import views
from django.contrib.auth.views import LogoutView
from django.urls import path

urlpatterns = [
    path('login/', views.MitarokuLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('language/', views.set_language, name='set_language'),
]
