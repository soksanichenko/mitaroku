"""Web URLs."""

from django.urls import path
from web.views import library, search, shell, upcoming

urlpatterns = [
    path('', shell.index, name='index'),
    path('library/', library.library, name='library'),
    path('upcoming/', upcoming.upcoming, name='upcoming'),
    path('search/', search.search, name='search'),
    path('healthz/', shell.healthz, name='healthz'),
]
