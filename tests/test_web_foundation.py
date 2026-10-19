import re
from pathlib import Path

import pytest
from catalog.models import Episode, EpisodeTranslation, Season, Title, TitleTranslation
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import translation
from tracking.models import LibraryEntry
from web import clients

pytestmark = pytest.mark.django_db

APP_CSS = Path(__file__).parents[1] / 'sources/web/static/web/app.css'


def render(source: str, **context) -> str:
    """Render an inline template with ``web_extras`` loaded."""
    return Template('{% load web_extras %}' + source).render(Context(context))


def request_for(user, language: str):
    """Return a request of ``user`` whose metadata language is ``language``."""
    user.profile.metadata_language = language
    user.profile.save()
    request = RequestFactory().get('/')
    request.user = user
    return request


@pytest.fixture
def title() -> Title:
    return Title.objects.create(
        kind=Title.Kind.TV, original_title='Original Name', poster_url=''
    )


def test_existing_pages_still_resolve_after_the_views_package(client, portal_headers):
    assert client.get('/', **portal_headers).status_code == 302
    assert client.get('/library/', **portal_headers).status_code == 200
    assert client.get('/upcoming/', **portal_headers).status_code == 200
    assert client.get('/healthz/').status_code == 200


def test_header_has_a_search_form_and_bottom_nav_a_search_item(client, portal_headers):
    html = client.get('/library/', **portal_headers).content.decode()
    form = re.search(r'<form[^>]*role="search"[^>]*>.*?</form>', html, re.S).group()
    assert 'action="/search/"' in form
    assert 'method="get"' in form
    assert 'name="q"' in form
    assert re.search(r'<label[^>]*for="mt-search-q"', form)
    bottom = re.search(r'<nav class="mt-bottom-nav".*?</nav>', html, re.S).group()
    assert 'href="/search/"' in bottom


def test_search_link_leads_to_a_page(client, portal_headers, patched_search):
    response = client.get('/search/', {'q': 'matrix'}, **portal_headers)
    assert response.status_code == 200
    assert 'value="matrix"' in response.content.decode()


def test_title_name_uses_the_metadata_language(alice, title):
    TitleTranslation.objects.create(
        title=title, language='ru', name='Русское имя', overview='Описание'
    )
    TitleTranslation.objects.create(
        title=title, language='en', name='English Name', overview='Overview'
    )
    ru = render(
        '{% title_name title %}|{% title_overview title %}',
        request=request_for(alice, 'ru'),
        title=title,
    )
    assert ru == 'Русское имя|Описание'
    en = render(
        '{% title_name title %}|{% title_overview title %}',
        request=request_for(alice, 'en'),
        title=title,
    )
    assert en == 'English Name|Overview'


def test_title_name_falls_back_to_the_original_title(alice, title):
    name = render(
        '{% title_name title %}', request=request_for(alice, 'ru'), title=title
    )
    assert name == 'Original Name'


def test_title_name_adds_no_queries_when_translations_are_prefetched(
    alice, title, django_assert_num_queries
):
    TitleTranslation.objects.create(title=title, language='ru', name='Имя')
    request = request_for(alice, 'ru')
    request.user.profile  # load the profile outside the measured block
    prefetched = Title.objects.prefetch_related('translations').get(pk=title.pk)
    with django_assert_num_queries(0):
        render(
            '{% title_name title %}{% title_overview title %}',
            request=request,
            title=prefetched,
        )


def test_title_name_without_a_signed_in_user_uses_english(title):
    TitleTranslation.objects.create(title=title, language='en', name='English Name')
    request = RequestFactory().get('/')
    assert render('{% title_name title %}', request=request, title=title) == (
        'English Name'
    )
    assert render('{% title_name title %}', title=title) == 'English Name'


def test_episode_name_uses_the_metadata_language(alice):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Show')
    season = Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
    episode = Episode.objects.create(season=season, number=1)
    EpisodeTranslation.objects.create(episode=episode, language='ru', name='Первая')
    EpisodeTranslation.objects.create(episode=episode, language='en', name='Pilot')
    assert (
        render(
            '{% episode_name episode %}',
            request=request_for(alice, 'ru'),
            episode=episode,
        )
        == 'Первая'
    )
    assert (
        render(
            '{% episode_name episode %}',
            request=request_for(alice, 'en'),
            episode=episode,
        )
        == 'Pilot'
    )


def test_poster_hue_is_stable_and_in_range():
    hues = {pk: render('{{ pk|poster_hue }}', pk=pk) for pk in range(1, 200)}
    assert all(0 <= int(hue) <= 359 for hue in hues.values())
    assert render('{{ pk|poster_hue }}', pk=7) == hues[7]
    assert len(set(hues.values())) > 50


def test_status_label_is_translated():
    assert render("{{ 'planned'|status_label }}") == 'Plan to watch'
    with translation.override('ru'):
        assert render("{{ 'completed'|status_label }}") == 'Просмотрено'
    assert render("{{ 'unknown'|status_label }}") == 'unknown'


def test_poster_without_url_renders_a_tinted_placeholder(title):
    html = render_to_string('web/components/_poster.html', {'title': title})
    assert 'mt-poster' in html
    assert f'--mt-hue: {render("{{ pk|poster_hue }}", pk=title.pk)}' in html
    assert '<img' not in html


def test_poster_with_url_is_lazy_and_sends_no_referrer(title):
    title.poster_url = 'https://image.example/p.jpg'
    html = render_to_string('web/components/_poster.html', {'title': title})
    image = re.search(r'<img[^>]*>', html).group()
    assert 'src="https://image.example/p.jpg"' in image
    assert 'loading="lazy"' in image
    assert 'referrerpolicy="no-referrer"' in image


def test_rating_badge_is_empty_without_a_rating(title):
    html = render_to_string('web/components/_rating_badge.html', {'title': title})
    assert html.strip() == ''


def test_rating_badge_shows_source_and_value(title):
    title.rating = '7.9'
    title.rating_source = 'tmdb'
    html = render_to_string('web/components/_rating_badge.html', {'title': title})
    assert '7.9' in html
    assert 'TMDB' in html
    assert 'role="img"' in html


def picker(title, entry, request=None, **extra) -> str:
    context = {
        'title': title,
        'entry': entry,
        'action': '/library/titles/1/status/',
        'fields': {'next': '/library/'},
        'target': '#card-1',
        **extra,
    }
    return render_to_string('web/components/_status_picker.html', context, request)


def test_status_picker_offers_add_when_not_in_the_library(title):
    html = picker(title, None)
    button = re.search(r'<button[^>]*aria-haspopup[^>]*>.*?</button>', html, re.S)
    assert 'Add' in button.group()
    assert 'aria-expanded="false"' in button.group()
    options = re.findall(r'<button[^>]*role="menuitemradio"[^>]*>', html)
    assert len(options) == 4
    assert 'aria-checked="true"' not in html
    assert 'role="menuitem"' not in html
    assert 'hx-post="/library/titles/1/status/"' in options[0]
    assert 'hx-target="#card-1"' in options[0]
    assert '"status": "watching"' in options[0].replace('&quot;', '"')
    assert 'name="next" value="/library/"' in html


def test_status_picker_marks_the_current_status(title, alice):
    entry = LibraryEntry(user=alice, title=title, status=LibraryEntry.Status.PLANNED)
    html = picker(title, entry, align='right')
    options = re.findall(r'<button[^>]*role="menuitemradio"[^>]*>', html)
    assert [o for o in options if 'aria-checked="true"' in o] == [options[1]]
    assert html.count('aria-checked="true"') == 1
    remove = re.search(r'<button[^>]*role="menuitem"[^>]*>', html).group()
    assert '"status": "remove"' in remove.replace('&quot;', '"')
    assert 'hx-post="/library/titles/1/status/"' in remove
    assert 'mt-picker-menu--right' in html
    # Every item is a 44 px touch target.
    assert all('mt-touch' in o for o in [*options, remove])
    assert re.search(r'\.mt-touch\s*{[^}]*min-height:\s*44px', APP_CSS.read_text())


def test_status_picker_is_translated(title):
    with translation.override('ru'):
        html = picker(title, None)
    assert 'Добавить' in html
    assert 'Буду смотреть' in html
    assert 'Add with status' not in html


def test_status_picker_escapes_user_text(alice):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='<b>x</b>')
    html = picker(title, None, request=request_for(alice, 'en'))
    assert '<b>x</b>' not in html
    assert '&lt;b&gt;x&lt;/b&gt;' in html


def test_progress_with_no_episodes_renders_zero():
    html = render_to_string('web/components/_progress.html', {'watched': 0, 'total': 0})
    assert '0/0' in html
    assert '--mt-pct: 0%' in html
    assert 'aria-valuenow="0"' in html


def test_progress_shows_the_share_watched():
    html = render_to_string(
        'web/components/_progress.html', {'watched': 1, 'total': 4, 'label': 'Show'}
    )
    assert '1/4' in html
    assert '--mt-pct: 25%' in html
    assert 'aria-label="Show"' in html


def test_state_blocks():
    loading = render_to_string('web/components/_state_loading.html')
    assert 'aria-busy="true"' in loading
    empty = render_to_string(
        'web/components/_state_empty.html',
        {'heading': 'Nothing here', 'action_url': '/search/', 'action_label': 'Search'},
    )
    assert 'Nothing here' in empty
    assert 'href="/search/"' in empty
    error = render_to_string(
        'web/components/_state_error.html', {'retry_url': '/library/grid/'}
    )
    assert 'role="alert"' in error
    assert 'hx-get="/library/grid/"' in error
    no_retry = render_to_string('web/components/_state_error.html')
    assert 'hx-get' not in no_retry


def test_shared_clients_is_built_once(monkeypatch):
    built = []

    def build():
        built.append(object())
        return built[-1]

    clients.shared_clients.cache_clear()
    monkeypatch.setattr(clients, 'default_clients', build)
    try:
        assert clients.shared_clients() is clients.shared_clients()
        assert len(built) == 1
    finally:
        clients.shared_clients.cache_clear()
