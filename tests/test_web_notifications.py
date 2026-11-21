import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

import pytest
from catalog.models import (
    Episode,
    EpisodeTranslation,
    Season,
    Title,
    TitleTranslation,
)
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.template import Context
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from notifications.models import Notification, dedupe_key
from notifications.moments import local_day
from web.templatetags.web_extras import unread_notifications

pytestmark = pytest.mark.django_db

Kind = Notification.Kind
ITEM = re.compile(r'<li class="mt-nt-item')
BADGE = re.compile(r'<span class="mt-bell-badge"[^>]*>([^<]*)</span>')


def today_of(user) -> date:
    """Return today's date in the zone of ``user`` (the view's idea of today)."""
    return local_day(timezone.now(), user.profile.timezone)


def make_title(name: str = 'Show', kind: str = Title.Kind.TV, **fields) -> Title:
    """Create a title."""
    return Title.objects.create(kind=kind, original_title=name, **fields)


def make_episodes(title: Title, count: int = 1) -> list[Episode]:
    """Create ``count`` episodes in season 1 of ``title``."""
    season = Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
    return [
        Episode.objects.create(season=season, number=number, air_date=date(2026, 1, 1))
        for number in range(1, count + 1)
    ]


def notify(
    user,
    title: Title | None = None,
    kind: str = Kind.EPISODE_AIRED,
    day: date | None = None,
    payload: dict | None = None,
    read: bool = False,
    created_at: datetime | None = None,
) -> Notification:
    """Create a notification row directly."""
    title = title or make_title()
    day = day or today_of(user)
    row = Notification.objects.create(
        user=user,
        kind=kind,
        title=title,
        day=day,
        payload=payload if payload is not None else {},
        dedupe_key=dedupe_key(kind, title.pk, day),
        read_at=timezone.now() if read else None,
    )
    if created_at is not None:
        Notification.objects.filter(pk=row.pk).update(created_at=created_at)
    return row


def page(client, headers, query: str = '', **extra) -> str:
    """Return the HTML of ``/notifications/``."""
    response = client.get(f'/notifications/{query}', **headers, **extra)
    assert response.status_code == 200
    return response.content.decode()


def fragment(client, headers, query: str) -> str:
    """Return the HTMX fragment of ``/notifications/``."""
    return page(client, headers, query, HTTP_HX_REQUEST='true')


def test_the_page_lists_only_own_notifications(client, portal_headers, alice, bob):
    notify(alice, make_title('Alices show'))
    notify(bob, make_title('Bobs show'))
    html = page(client, portal_headers)
    assert 'Alices show' in html
    assert 'Bobs show' not in html


def test_rows_are_grouped_under_local_day_headings(client, portal_headers, alice):
    today = today_of(alice)
    notify(alice, make_title('A'), day=today)
    notify(alice, make_title('B'), day=today - timedelta(days=1))
    notify(alice, make_title('C'), day=date(2020, 3, 5))
    html = page(client, portal_headers)
    headings = re.findall(r'<h2 id="nd-[^"]*"[^>]*>\s*([^<]*?)\s*</h2>', html)
    assert headings[:2] == ['Today', 'Yesterday']
    assert 'Mar 5, 2020' in headings[2]
    assert html.index('>A<') < html.index('>B<') < html.index('>C<')


def test_today_is_the_day_in_the_users_zone(client, portal_headers, alice):
    alice.profile.timezone = 'Pacific/Kiritimati'
    alice.profile.save()
    notify(alice, make_title('Ahead'), day=today_of(alice))
    html = page(client, portal_headers)
    assert '>Today<' in html.replace('\n', '')


def test_rows_of_a_day_are_newest_first(client, portal_headers, alice):
    now = timezone.now()
    notify(alice, make_title('Older'), created_at=now - timedelta(hours=2))
    notify(alice, make_title('Newer'), created_at=now)
    html = page(client, portal_headers)
    assert html.index('Newer') < html.index('Older')


def test_unread_only_filters_read_rows(client, portal_headers, alice):
    notify(alice, make_title('Fresh'))
    notify(alice, make_title('Seen'), read=True)
    everything = page(client, portal_headers)
    assert 'Fresh' in everything and 'Seen' in everything
    unread = page(client, portal_headers, '?unread=1')
    assert 'Fresh' in unread
    assert 'Seen' not in unread
    assert 'href="/notifications/"' in unread
    assert 'href="/notifications/?unread=1"' in everything


def test_the_empty_states_differ_with_the_filter(client, portal_headers, alice):
    for query in ('', '?unread=1'):
        caught_up = page(client, portal_headers, query)
        assert 'You’re all caught up' in caught_up
        assert 'Turn off' not in caught_up
    notify(alice, make_title('Seen'), read=True)
    hidden = page(client, portal_headers, '?unread=1')
    assert 'Turn off “Unread only” to see earlier notifications.' in hidden
    assert 'You’re all caught up' not in hidden
    assert 'Seen' not in hidden


@pytest.mark.parametrize(
    ('kind', 'label', 'icon'),
    [
        (Kind.EPISODE_AIRED, 'New episode', 'aired'),
        (Kind.PREMIERE, 'Premiere', 'premiere'),
        (Kind.MOVIE_RELEASED, 'Released', 'released'),
        (Kind.RELEASE_ANNOUNCED, 'Date announced', 'announced'),
        (Kind.RELEASE_CHANGED, 'Date changed', 'changed'),
    ],
)
def test_each_kind_renders_its_label_and_text(
    client, portal_headers, alice, kind, label, icon
):
    title = make_title('Subject')
    episode = make_episodes(title)[0]
    EpisodeTranslation.objects.create(episode=episode, language='en', name='Pilot')
    payload = {
        Kind.EPISODE_AIRED: {'episodes': [episode.pk]},
        Kind.PREMIERE: {'episodes': [episode.pk]},
        Kind.MOVIE_RELEASED: {},
        Kind.RELEASE_ANNOUNCED: {'to': '2030-04-02'},
        Kind.RELEASE_CHANGED: {'from': '2030-04-02', 'to': '2030-06-09'},
    }[kind]
    notify(alice, title, kind=kind, payload=payload)
    html = page(client, portal_headers)
    assert f'>{label}<' in html.replace('\n', '')
    assert f'mt-nt-icon--{icon}' in html
    assert 'Subject' in html
    if kind in (Kind.EPISODE_AIRED, Kind.PREMIERE):
        assert 'S1E1 · Pilot' in html
    if kind == Kind.MOVIE_RELEASED:
        assert 'The movie was released.' in html
    if kind == Kind.RELEASE_ANNOUNCED:
        assert '<strong>Apr 2, 2030</strong>' in html
        assert '→' not in html
    if kind == Kind.RELEASE_CHANGED:
        assert '<s>Apr 2, 2030</s>' in html
        assert '→' in html
        assert '<strong>Jun 9, 2030</strong>' in html


def test_several_episodes_list_their_codes(client, portal_headers, alice):
    title = make_title('Binge')
    episodes = make_episodes(title, 3)
    notify(alice, title, payload={'episodes': [e.pk for e in episodes]})
    html = page(client, portal_headers)
    assert 'S1E1, S1E2, S1E3' in html
    assert '·' not in html.split('mt-nt-text')[1].split('</span>')[0]


def test_a_deleted_episode_is_skipped(client, portal_headers, alice):
    title = make_title('Pruned')
    kept, gone = make_episodes(title, 2)
    notify(alice, title, payload={'episodes': [kept.pk, gone.pk]})
    gone.delete()
    html = page(client, portal_headers)
    assert 'S1E1' in html
    assert 'S1E2' not in html
    only_gone = make_title('Empty')
    ghost = make_episodes(only_gone)[0]
    notify(alice, only_gone, payload={'episodes': [ghost.pk]})
    ghost.delete()
    html = page(client, portal_headers)
    assert 'Empty' in html
    assert 'New episode' in html


@pytest.mark.parametrize(
    'payload',
    [
        {},
        {'to': 'garbage'},
        {'from': None, 'to': 5},
        {'episodes': 'x'},
        {'episodes': ['a', None]},
        [],
    ],
)
def test_a_malformed_payload_never_breaks_the_page(
    client, portal_headers, alice, payload
):
    for kind in Kind:
        notify(alice, make_title(f'T-{kind}'), kind=kind, payload=payload)
    html = page(client, portal_headers)
    assert html.count('mt-nt-item') >= len(Kind)


def test_names_follow_the_metadata_language(client, portal_headers, alice):
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    title = make_title('Path')
    TitleTranslation.objects.create(title=title, language='ru', name='Путь')
    episode = make_episodes(title)[0]
    EpisodeTranslation.objects.create(episode=episode, language='ru', name='Возврат')
    notify(alice, title, payload={'episodes': [episode.pk]})
    html = page(client, portal_headers)
    assert 'Путь' in html
    assert 'S1E1 · Возврат' in html


def test_the_page_renders_in_russian(client, portal_headers, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    labels = {
        Kind.EPISODE_AIRED: 'Новая серия',
        Kind.PREMIERE: 'Премьера',
        Kind.MOVIE_RELEASED: 'Вышел',
        Kind.RELEASE_ANNOUNCED: 'Дата объявлена',
        Kind.RELEASE_CHANGED: 'Дата изменена',
    }
    for kind in labels:
        notify(alice, make_title(f'K-{kind}'), kind=kind)
    html = page(client, portal_headers)
    for label in labels.values():
        assert f'>{label}<' in html.replace('\n', ''), label
    for text in ('Сегодня', 'Уведомления', '5 непрочитанных', 'Только непрочитанные'):
        assert text in html, text
    Notification.objects.update(read_at=timezone.now())
    hidden = page(client, portal_headers, '?unread=1')
    assert 'Нет непрочитанных уведомлений' in hidden
    assert 'Отключите «Только непрочитанные»' in hidden
    assert 'Notifications' not in hidden


def test_load_more_pages_by_thirty(client, portal_headers, alice):
    for index in range(31):
        notify(alice, make_title(f'Title {index:02d}'))
    first = page(client, portal_headers)
    assert len(ITEM.findall(first)) == 30
    assert 'Load more' in first
    assert 'href="/notifications/?page=2"' in first
    rest = fragment(client, portal_headers, '?page=2')
    assert len(ITEM.findall(rest)) == 1
    assert 'Load more' not in rest
    assert '<html' not in rest
    # Without JavaScript the link is a plain page that shows everything so far.
    plain = page(client, portal_headers, '?page=2')
    assert len(ITEM.findall(plain)) == 31
    assert 'Load more' not in plain


def test_load_more_continues_a_day_without_a_second_heading(
    client, portal_headers, alice
):
    today = today_of(alice)
    now = timezone.now()
    for index in range(31):
        notify(
            alice,
            make_title(f'Today {index:02d}'),
            day=today,
            created_at=now - timedelta(minutes=index),
        )
    notify(alice, make_title('Old'), day=today - timedelta(days=5))
    first = page(client, portal_headers)
    assert 'Today 30' not in first
    rest = fragment(client, portal_headers, '?page=2')
    # Today continues from page 1: its row joins the list that is already there,
    # and only the older day brings a heading.
    assert rest.count('<h2') == 1
    assert f'<ul id="nl-{today.isoformat()}" hx-swap-oob="beforeend">' in rest
    assert 'Today 30' in rest
    assert 'Old' in rest
    assert 'Load more' not in rest


def test_the_unread_filter_survives_in_the_load_more_link(
    client, portal_headers, alice
):
    for index in range(31):
        notify(alice, make_title(f'Title {index:02d}'))
    html = page(client, portal_headers, '?unread=1')
    assert 'href="/notifications/?unread=1&amp;page=2"' in html


@pytest.mark.parametrize('query', ['?page=abc', '?page=-3', '?page=99999', '?page='])
def test_page_clamps_garbage(client, portal_headers, alice, query):
    notify(alice, make_title('One'))
    assert 'One' in page(client, portal_headers, query)


def test_a_fragment_past_the_end_adds_nothing(client, portal_headers, alice):
    notify(alice, make_title('One'))
    rest = fragment(client, portal_headers, '?page=99999')
    assert 'mt-nt-item' not in rest
    assert 'Load more' not in rest


def test_the_bell_shows_the_unread_count_only_for_the_user(
    client, portal_headers, alice, bob
):
    for index in range(2):
        notify(alice, make_title(f'A{index}'))
    notify(alice, make_title('Read'), read=True)
    for index in range(5):
        notify(bob, make_title(f'B{index}'))
    html = page(client, portal_headers)
    assert BADGE.findall(html) == ['2']
    assert 'Notifications, 2 unread' in html
    for index in range(100):
        notify(alice, make_title(f'More {index}'))
    assert BADGE.findall(page(client, portal_headers)) == ['99+']
    Notification.objects.filter(user=alice).update(read_at=timezone.now())
    html = page(client, portal_headers)
    assert BADGE.findall(html) == []
    assert 'href="/notifications/"' in html


def test_the_bell_is_on_other_pages_too(client, portal_headers, alice):
    notify(alice, make_title('Any'))
    html = client.get('/upcoming/', **portal_headers).content.decode()
    assert BADGE.findall(html) == ['1']


def test_the_bell_count_is_one_query(alice, bob):
    notify(alice, make_title('A'))
    notify(bob, make_title('B'))
    with CaptureQueriesContext(connection) as queries:
        count = unread_notifications(Context({'user': alice}))
    assert count == 1
    assert len(queries) == 1


def test_the_bell_count_for_anonymous_is_zero_and_free(client):
    with CaptureQueriesContext(connection) as queries:
        assert unread_notifications(Context({'user': AnonymousUser()})) == 0
    assert len(queries) == 0


def test_anonymous_is_sent_to_login(client):
    # Like every other page: the portal middleware refuses without a login.
    assert client.get('/notifications/').status_code == 401


def test_the_page_query_count_is_constant(client, portal_headers, alice):
    def add(count: int) -> None:
        for index in range(count):
            title = make_title(f'T{Notification.objects.count()}')
            episodes = make_episodes(title, 2)
            for episode in episodes:
                EpisodeTranslation.objects.create(
                    episode=episode, language='en', name=f'E{episode.pk}'
                )
            TitleTranslation.objects.create(title=title, language='en', name='X')
            notify(
                alice,
                title,
                kind=Kind.PREMIERE if index % 2 else Kind.EPISODE_AIRED,
                payload={'episodes': [e.pk for e in episodes]},
            )

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get('/notifications/', **portal_headers).status_code == 200
        return len(queries)

    add(1)
    count()
    add(9)
    ten = count()
    add(20)
    assert count() == ten


def test_unread_rows_carry_a_hidden_label(client, portal_headers, alice):
    notify(alice, make_title('New one'))
    notify(alice, make_title('Old one'), read=True)
    html = page(client, portal_headers)
    assert html.count('<span class="mt-sr-only"> · unread</span>') == 1


def test_the_states_are_in_the_page(client, portal_headers, alice):
    notify(alice, make_title('Any'))
    html = page(client, portal_headers)
    assert 'mt-state--loading' in html
    assert 'Loading notifications' in html
    assert 'role="alert"' in html
    assert 'Couldn’t load notifications' in html
    assert 'Try again' in html


def test_the_subtitle_counts_only_own_unread(client, portal_headers, alice, bob):
    notify(bob, make_title('B'))
    assert 'No unread notifications' in page(client, portal_headers)
    notify(alice, make_title('A1'))
    notify(alice, make_title('A2'))
    html = page(client, portal_headers)
    assert '2 unread' in html
    assert '>1 unread<' not in html


def post(client, headers, url: str, **data):
    """POST ``data`` to ``url`` as the user of ``headers``."""
    return client.post(url, data, **headers)


def open_url(row: Notification) -> str:
    """Return the URL that opens ``row``."""
    return f'/notifications/{row.pk}/open/'


def read_url(row: Notification) -> str:
    """Return the URL that marks ``row`` read."""
    return f'/notifications/{row.pk}/read/'


def test_opening_marks_read_and_redirects_to_the_title(client, portal_headers, alice):
    title = make_title('Opened')
    row = notify(alice, title)
    response = post(client, portal_headers, open_url(row))
    assert response.status_code == 303
    assert response['Location'] == f'/titles/{title.pk}/'
    row.refresh_from_db()
    assert row.read_at is not None


def test_opening_keeps_the_first_read_time(client, portal_headers, alice):
    row = notify(alice, read=True)
    first = timezone.now() - timedelta(days=3)
    Notification.objects.filter(pk=row.pk).update(read_at=first)
    response = post(client, portal_headers, open_url(row))
    assert response.status_code == 303
    row.refresh_from_db()
    assert row.read_at == first
    # Marking one that is already read keeps the time too.
    post(client, portal_headers, read_url(row))
    row.refresh_from_db()
    assert row.read_at == first


def test_marking_one_keeps_the_user_on_the_list(client, portal_headers, alice):
    row = notify(alice)
    response = post(client, portal_headers, read_url(row))
    assert response.status_code == 303
    assert response['Location'] == '/notifications/'
    row.refresh_from_db()
    assert row.read_at is not None
    other = notify(alice)
    response = post(
        client,
        portal_headers,
        read_url(other),
        next='/notifications/?unread=1&page=2',
    )
    assert response['Location'] == '/notifications/?unread=1&page=2'
    other.refresh_from_db()
    assert other.read_at is not None


@pytest.mark.parametrize(
    'target',
    [
        'https://evil.example/',
        'https://evil.example/notifications/',
        '//evil.example/',
        '/\\evil.example/',
        '\\\\evil.example',
        'javascript:alert(1)',
        'notifications/',
        '/library/',
        '',
    ],
)
def test_next_must_be_a_local_path(client, portal_headers, alice, target):
    row = notify(alice)
    response = post(client, portal_headers, read_url(row), next=target)
    assert response.status_code == 303
    assert response['Location'] == '/notifications/'
    row.refresh_from_db()
    assert row.read_at is not None


def test_mark_all_read_marks_only_own_rows(client, portal_headers, alice, bob):
    mine = [notify(alice, make_title(f'A{index}')) for index in range(3)]
    already = notify(alice, make_title('Seen'), read=True)
    first = timezone.now() - timedelta(days=2)
    Notification.objects.filter(pk=already.pk).update(read_at=first)
    theirs = [notify(bob, make_title(f'B{index}')) for index in range(2)]
    response = post(client, portal_headers, '/notifications/read-all/')
    assert response.status_code == 303
    assert response['Location'] == '/notifications/'
    for row in mine:
        row.refresh_from_db()
        assert row.read_at is not None
    already.refresh_from_db()
    assert already.read_at == first
    for row in theirs:
        row.refresh_from_db()
        assert row.read_at is None


def test_mark_all_is_one_update(client, portal_headers, alice):
    for index in range(5):
        notify(alice, make_title(f'T{index}'))
    with CaptureQueriesContext(connection) as queries:
        post(client, portal_headers, '/notifications/read-all/')
    updates = [q for q in queries if q['sql'].startswith('UPDATE "notifications_')]
    assert len(updates) == 1


def test_another_users_row_is_a_404_for_open_and_read(
    client, portal_headers, alice, bob
):
    row = notify(bob, make_title('Secret'))
    # Control: the same URLs work for the owner of a row.
    own = notify(alice, make_title('Mine'))
    assert post(client, portal_headers, read_url(own)).status_code == 303
    assert post(client, portal_headers, open_url(own)).status_code == 303
    assert post(client, portal_headers, open_url(row)).status_code == 404
    assert post(client, portal_headers, read_url(row)).status_code == 404
    row.refresh_from_db()
    assert row.read_at is None
    assert post(client, portal_headers, '/notifications/0/open/').status_code == 404


def test_get_is_not_allowed_on_the_mutating_urls(client, portal_headers, alice):
    row = notify(alice)
    for url in (open_url(row), read_url(row), '/notifications/read-all/'):
        assert client.get(url, **portal_headers).status_code == 405, url
    row.refresh_from_db()
    assert row.read_at is None


def test_post_without_csrf_is_rejected(portal_headers, alice):
    strict = Client(enforce_csrf_checks=True)
    row = notify(alice)
    for url in (open_url(row), read_url(row), '/notifications/read-all/'):
        assert strict.post(url, {}, **portal_headers).status_code == 403, url
    row.refresh_from_db()
    assert row.read_at is None


def test_an_anonymous_post_is_refused(client, alice):
    row = notify(alice)
    assert client.post(open_url(row), {}).status_code == 401
    row.refresh_from_db()
    assert row.read_at is None


def test_mark_all_is_disabled_when_nothing_is_unread(client, portal_headers, alice):
    pattern = re.compile(
        r'<form[^>]*action="/notifications/read-all/"[^>]*>.*?</form>', re.S
    )
    empty = pattern.search(page(client, portal_headers)).group(0)
    assert 'disabled' in empty
    notify(alice, read=True)
    assert 'disabled' in pattern.search(page(client, portal_headers)).group(0)
    notify(alice, make_title('Fresh'))
    html = pattern.search(page(client, portal_headers)).group(0)
    assert 'disabled' not in html
    assert 'Mark all as read' in html
    assert 'csrfmiddlewaretoken' in html


def test_the_bell_updates_after_marking(client, portal_headers, alice):
    rows = [notify(alice, make_title(f'T{index}')) for index in range(3)]
    assert BADGE.findall(page(client, portal_headers)) == ['3']
    post(client, portal_headers, read_url(rows[0]))
    assert BADGE.findall(page(client, portal_headers)) == ['2']
    post(client, portal_headers, open_url(rows[1]))
    assert BADGE.findall(page(client, portal_headers)) == ['1']
    post(client, portal_headers, '/notifications/read-all/')
    assert BADGE.findall(page(client, portal_headers)) == []


class FormCheck(HTMLParser):
    """Collect the structure mistakes of a page: nested forms, nested controls."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.problems: list[str] = []
        self.forms: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag in ('input', 'br', 'img', 'path', 'rect', 'circle', 'polyline'):
            if tag == 'input' and self.forms:
                self.forms[-1]['inputs'].append(dict(attrs))
            return
        if tag == 'form':
            if 'form' in self.stack:
                self.problems.append('form in form')
            self.forms.append({'attrs': dict(attrs), 'inputs': [], 'buttons': 0})
        if tag in ('button', 'a') and any(t in ('button', 'a') for t in self.stack):
            self.problems.append(f'{tag} inside an interactive element')
        if tag == 'button' and self.forms and 'form' in self.stack:
            self.forms[-1]['buttons'] += 1
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack.pop() != tag:
                pass


def row_forms(html: str) -> FormCheck:
    """Parse ``html`` and return what the structure check found."""
    check = FormCheck()
    check.feed(html)
    return check


def test_rows_are_form_buttons_without_nesting(client, portal_headers, alice):
    unread = notify(alice, make_title('New one'))
    seen = notify(alice, make_title('Old one'), read=True)
    html = page(client, portal_headers)
    check = row_forms(html)
    assert check.problems == []
    actions = [form['attrs'].get('action') for form in check.forms]
    assert open_url(unread) in actions
    assert open_url(seen) in actions
    # Only the unread row has the small button.
    assert read_url(unread) in actions
    assert read_url(seen) not in actions
    assert html.count('Mark as read') == 1
    for form in check.forms:
        if form['attrs'].get('action', '').startswith('/notifications/'):
            assert form['attrs'].get('method') == 'post'
            names = [i.get('name') for i in form['inputs']]
            assert 'csrfmiddlewaretoken' in names
            assert form['buttons'] == 1
    assert 'class="mt-nt-link"' in html
    assert '<a class="mt-nt-link"' not in html


def test_the_mark_button_carries_the_current_list(client, portal_headers, alice):
    notify(alice, make_title('New one'))
    html = page(client, portal_headers, '?unread=1')
    assert '<input type="hidden" name="next" value="/notifications/?unread=1">' in html
    html = page(client, portal_headers, '?page=abc')
    assert '<input type="hidden" name="next" value="/notifications/">' in html


def test_appended_rows_carry_working_forms(client, portal_headers, alice):
    today = today_of(alice)
    now = timezone.now()
    for index in range(31):
        notify(
            alice,
            make_title(f'Today {index:02d}'),
            day=today,
            created_at=now - timedelta(minutes=index),
        )
    notify(alice, make_title('Old'), day=today - timedelta(days=5))
    rest = fragment(client, portal_headers, '?page=2')
    check = row_forms(rest)
    assert check.problems == []
    posts = [f for f in check.forms if f['attrs'].get('action')]
    # Three rows, each with an open form and a mark form.
    assert len(posts) == 4
    for form in posts:
        assert 'csrfmiddlewaretoken' in [i.get('name') for i in form['inputs']]
    assert '<input type="hidden" name="next" value="/notifications/?page=2">' in rest


def test_the_new_labels_render_in_russian(client, portal_headers, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    notify(alice, make_title('Any'))
    html = page(client, portal_headers)
    assert 'Отметить как прочитанное' in html
    assert 'Отметить все как прочитанные' in html
    assert 'Mark as read' not in html
    assert 'Mark all as read' not in html
