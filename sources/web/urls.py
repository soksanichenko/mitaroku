"""Web URLs."""

from django.urls import path
from web.views import (
    actions,
    browse,
    collections,
    email,
    library,
    notifications,
    opening,
    search,
    settings,
    shell,
    titles,
    upcoming,
)

urlpatterns = [
    path('', shell.index, name='index'),
    path('library/', library.library, name='library'),
    path('browse/', browse.browse_page, name='browse'),
    path('collections/<int:tmdb_id>/', collections.collection_page, name='collection'),
    path('notifications/', notifications.notifications_page, name='notifications'),
    path(
        'notifications/read-all/',
        notifications.notifications_read_all,
        name='notifications_read_all',
    ),
    path(
        'notifications/<int:pk>/open/',
        notifications.notification_open,
        name='notification_open',
    ),
    path(
        'notifications/<int:pk>/read/',
        notifications.notification_read,
        name='notification_read',
    ),
    path('upcoming/', upcoming.upcoming, name='upcoming'),
    path('search/', search.search_page, name='search'),
    path('search/add/', search.search_add, name='search_add'),
    path('add/<int:pk>/', search.add_status, name='add_status'),
    path(
        'open/<str:provider>/<int:external_id>/', opening.open_title, name='open_title'
    ),
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
    path('settings/timezone/', settings.settings_timezone, name='settings_timezone'),
    path('settings/email/', settings.settings_email, name='settings_email'),
    path(
        'email/unsubscribe/<str:token>/',
        email.email_unsubscribe,
        name='email_unsubscribe',
    ),
    path('email/confirm/<str:token>/', email.email_confirm, name='email_confirm'),
    path('healthz/', shell.healthz, name='healthz'),
]
