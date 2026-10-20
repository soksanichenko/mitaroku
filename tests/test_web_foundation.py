import json
import re
import shutil
import subprocess
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


def css_rule(selector: str) -> str:
    """Return the body of the first rule of ``app.css`` that starts with ``selector``."""
    match = re.search(
        rf'(?m)^{re.escape(selector)}\s*{{([^}}]*)}}', APP_CSS.read_text()
    )
    assert match, f'no rule for {selector}'
    return match.group(1)


@pytest.mark.parametrize(
    'selector', ['.mt-title', '.mt-lib-all', '.mt-lib-filters', '.mt-lib']
)
def test_grids_holding_scrolling_rows_have_a_shrinkable_column(selector):
    # An implicit `auto` column grows to the width of an overflow-x row inside it.
    assert 'grid-template-columns: minmax(0, 1fr)' in css_rule(selector)


def test_picker_menu_can_open_upward_above_the_bottom_nav(title, alice):
    html = picker(title, LibraryEntry(user=alice, title=title, status='watching'))
    assert 'x-ref="menu"' in html
    assert "'mt-picker-menu--up': up" in html
    assert 'mtMenuUp(' in html
    # The contract of the control is unchanged.
    assert 'aria-haspopup="menu"' in html
    assert '@keydown.escape="open = false; $refs.trigger.focus()"' in html
    assert '@click.outside="open = false"' in html
    assert 'bottom: calc(100% + 4px)' in css_rule('.mt-picker-menu--up')
    assert 'bottom:' in css_rule('.mt-ep-menu.mt-ep-menu--up')
    assert re.search(
        r'\.mt-page\s*{[^}]*padding-bottom:\s*calc\(88px', APP_CSS.read_text()
    )


def test_menu_placement_script_is_loaded_by_the_shell():
    script = (APP_CSS.parent / 'menu.js').read_text()
    assert 'window.mtMenuUp' in script
    assert '.mt-bottom-nav' in script
    assert 'getBoundingClientRect' in script
    html = render_to_string('web/base.html', {})
    assert 'web/menu.js' in html


def test_episode_menu_follows_the_placement_state():
    html = render_to_string('web/_episodes_section.html', {})
    assert 'menuUp' in html and 'mtMenuUp(' in html
    row = Path(APP_CSS.parents[2] / 'templates/web/_episode_row.html').read_text()
    assert "'mt-ep-menu--up': menuUp" in row


@pytest.mark.parametrize(
    'selector',
    [
        '.mt-cw-name::after',
        '.mt-result-name a::after',
        '.mt-lcard-link::after',
        '.mt-lrow-name::after',
    ],
)
def test_text_links_stretch_over_their_card_or_row(selector):
    rule = css_rule(selector)
    assert 'position: absolute' in rule
    assert 'inset: 0' in rule
    # The containing block is the card or row, not a smaller box.
    for container in ('.mt-cw-card', '.mt-result', '.mt-lcard', '.mt-lrow'):
        assert 'position: relative' in css_rule(container)
    # What must stay clickable sits above the stretched link.
    for control in ('.mt-cw-check', '.mt-result-act'):
        assert 'position: relative' in css_rule(control)
    assert 'position: relative' in css_rule('.mt-ltable .mt-col-status')


def test_group_header_is_one_row_that_wraps_only_the_name():
    toggle = css_rule('.mt-gh-toggle')
    assert 'display: flex' in toggle
    assert 'flex-wrap' not in toggle
    assert 'overflow-wrap' in css_rule('.mt-gh-name')
    assert 'flex: none' in css_rule('.mt-gh-toggle > :not(.mt-gh-name)')


def test_library_cards_are_columns_with_the_status_pinned_down():
    cell = css_rule('.mt-lcard-cell')
    assert 'display: flex' in cell
    card = css_rule('.mt-lcard')
    assert 'flex-direction: column' in card
    assert 'flex: 1' in css_rule('.mt-lcard-link')


def test_list_title_column_takes_the_width_the_hidden_columns_free():
    assert 'width: 100%' in css_rule('.mt-ltable th:first-child')


def test_posters_are_in_colour_where_there_is_no_hover():
    css = APP_CSS.read_text()
    assert re.search(
        r'@media \(hover: none\)\s*{\s*\.mt-poster-ph,\s*\.mt-poster-img\s*{\s*filter: none;',
        css,
    )


def test_movie_that_ended_is_released(title):
    from web.templatetags.web_extras import airing_label

    assert airing_label('ended', 'movie') == 'Released'
    assert airing_label('ended', 'tv') == 'Ended'
    assert airing_label('ended') == 'Ended'
    assert airing_label('unknown', 'movie') == ''
    with translation.override('ru'):
        assert airing_label('ended', 'movie') == 'Вышел'
        assert airing_label('ended', 'tv') == 'Завершён'


def test_the_shell_loads_the_session_script(client, portal_headers, alice):
    html = client.get('/library/', **portal_headers).content.decode()
    assert '/static/web/session.js' in html


HARNESS = """
const listeners = {};
const store = {};
let reloads = 0, now = 1000000;
global.document = {addEventListener: (name, fn) => { listeners[name] = fn; }};
global.window = {location: {reload: () => { reloads += 1; }}};
global.sessionStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
Date.now = () => now;
require(process.argv[1]);
const fire = (status) => listeners['htmx:responseError']({detail: {xhr: {status}}});
fire(500); fire(404);
const other = reloads;
fire(401); fire(401);
const first = reloads;
now += 11000; fire(401);
console.log(JSON.stringify({other, first, later: reloads}));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
def test_a_401_reloads_the_page_at_most_once_per_ten_seconds():
    result = subprocess.run(
        ['node', '-e', HARNESS, str(APP_CSS.parent / 'session.js')],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {'other': 0, 'first': 1, 'later': 2}


def test_the_session_script_survives_a_blocked_storage():
    script = (APP_CSS.parent / 'session.js').read_text()
    assert script.count('try {') >= 2 and 'catch' in script
