"""Web URLs."""

from django.urls import path
from web.views import library, search, shell, titles, upcoming

urlpatterns = [
    path('', shell.index, name='index'),
    path('library/', library.library, name='library'),
    path('upcoming/', upcoming.upcoming, name='upcoming'),
    path('search/', search.search_page, name='search'),
    path('search/add/', search.search_add, name='search_add'),
    path('add/<int:pk>/', search.add_status, name='add_status'),
    path('titles/<int:pk>/', titles.title_page, name='title'),
    path('titles/<int:pk>/refresh/', titles.title_refresh, name='title_refresh'),
    path('titles/<int:pk>/status/', titles.title_status, name='title_status'),
    path('titles/<int:pk>/score/', titles.title_score, name='title_score'),
    path('titles/<int:pk>/note/', titles.title_note, name='title_note'),
    path('healthz/', shell.healthz, name='healthz'),
]
