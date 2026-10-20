"""Web URLs."""

from django.urls import path
from web.views import actions, library, search, settings, shell, titles, upcoming

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
    path('titles/<int:pk>/watch-all/', actions.title_watch_all, name='title_watch_all'),
    path('titles/<int:pk>/movie/', actions.title_movie_watch, name='title_movie_watch'),
    path('titles/<int:pk>/continue/', actions.title_continue, name='title_continue'),
    path('episodes/<int:pk>/watch/', actions.episode_watch, name='episode_watch'),
    path('episodes/<int:pk>/up-to/', actions.episode_up_to, name='episode_up_to'),
    path('seasons/<int:pk>/rows/', titles.season_rows, name='season_rows'),
    path('seasons/<int:pk>/watch/', actions.season_watch, name='season_watch'),
    path('settings/', settings.settings_page, name='settings'),
    path('healthz/', shell.healthz, name='healthz'),
]
