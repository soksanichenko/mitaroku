import re
from datetime import timedelta
from pathlib import Path

import pytest
from catalog.models import (
    Episode,
    EpisodeTranslation,
    Season,
    Title,
    TitleTranslation,
)
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import TODAY, first_episode, future_episode, make_movie, watch

pytestmark = pytest.mark.django_db


def open_title(client, headers, title) -> str:
    """Return the HTML of the title page."""
    response = client.get(f'/titles/{title.pk}/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def post(client, headers, title, action: str, **data):
    """POST ``data`` to a title action."""
    return client.post(f'/titles/{title.pk}/{action}/', data, **headers)


def track(user, title, **fields) -> LibraryEntry:
    """Put ``title`` into the library of ``user``."""
    return LibraryEntry.objects.create(user=user, title=title, **fields)


def make_anime() -> Title:
    """Create a synced anime franchise: a TV cour, an OVA and a movie."""
    title = Title.objects.create(
        kind=Title.Kind.ANIME,
        original_title='Franchise',
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    spec = [
        (1, Season.Kind.TV, 'Cour One', False, 3),
        (2, Season.Kind.TV, '', False, 2),
        (3, Season.Kind.OVA, '', True, 1),
        (4, Season.Kind.MOVIE, 'The Movie', True, 1),
    ]
    absolute = 0
    for number, kind, name, extra, count in spec:
        season = Season.objects.create(
            title=title,
            number=number,
            kind=kind,
            name=name,
            is_extra=extra,
            episode_count=count,
            episode_source=Season.EpisodeSource.TMDB,
        )
        for index in range(1, count + 1):
            absolute += 0 if extra else 1
            Episode.objects.create(
                season=season,
                number=index,
                absolute_number=None if extra else absolute,
                air_date=TODAY - timedelta(days=50),
            )
    return title


def test_title_page_never_shows_another_users_note(
    client, portal_headers, alice, bob, synced_show
):
    LibraryEntry.objects.create(
        user=bob, title=synced_show, status='watching', note='bob secret', score=9
    )
    html = open_title(client, portal_headers, synced_show)
    assert 'bob secret' not in html
    assert 'Add' in html  # alice is not tracking it
    assert 'library-panel' in html
    assert 'name="note"' not in html


def test_title_page_shows_the_header_and_the_users_own_entry(
    client, portal_headers, alice, synced_show
):
    Title.objects.filter(pk=synced_show.pk).update(
        original_title='Hōrō',
        release_date=TODAY - timedelta(days=400),
        runtime_minutes=48,
        rating=7.9,
        rating_source='tmdb',
    )
    TitleTranslation.objects.create(
        title=synced_show, language='en', name='Wanderer', overview='A long road.'
    )
    track(alice, synced_show, status='watching', score=6, note='my own note')
    html = open_title(client, portal_headers, synced_show)
    assert '<h1' in html and 'Hōrō' in html
    assert 'A long road.' in html
    assert '48' in html and '2025' in html
    assert 'TMDB rating 7.9' in html
    assert 'my own note' in html
    assert 'Watching' in html
    assert 'Show' in html  # the kind tag


def test_title_page_for_a_degraded_entry(client, portal_headers, alice):
    title = Title.objects.create(
        kind=Title.Kind.ANIME,
        original_title='Degraded',
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    season = Season.objects.create(
        title=title,
        number=1,
        kind=Season.Kind.TV,
        episode_count=2,
        episode_source=Season.EpisodeSource.ANILIST,
    )
    dated = Episode.objects.create(
        season=season, number=1, absolute_number=1, air_date=TODAY - timedelta(days=9)
    )
    undated = Episode.objects.create(season=season, number=2, absolute_number=2)
    html = open_title(client, portal_headers, title)
    assert 'Episode 1' in html and 'Episode 2' in html
    assert 'Date not announced' in html
    assert html.count('Episode titles are not available for this entry') == 1
    assert f'id="ep-{dated.pk}"' in html and f'id="ep-{undated.pk}"' in html


def test_title_page_for_an_announced_title(client, portal_headers, alice):
    title = Title.objects.create(
        kind=Title.Kind.TV,
        original_title='Announced',
        status='upcoming',
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    html = open_title(client, portal_headers, title)
    assert 'No episodes announced yet' in html


def test_title_page_for_a_source_none_season_has_no_checks(
    client, portal_headers, alice, today
):
    title = Title.objects.create(
        kind=Title.Kind.ANIME,
        original_title='Unknown episodes',
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    Season.objects.create(
        title=title,
        number=1,
        kind=Season.Kind.TV,
        episode_source=Season.EpisodeSource.NONE,
    )
    html = open_title(client, portal_headers, title)
    assert 'Episodes are not known yet' in html
    assert 'mt-check' not in html
    assert 'No episodes announced yet' not in html


def test_never_synced_title_shows_the_polling_skeleton(
    client, portal_headers, alice, queued_language
):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Fresh')
    html = open_title(client, portal_headers, title)
    body = re.search(r'<div[^>]*id="title-body"[^>]*>', html).group()
    assert 'hx-trigger="every 3s"' in body
    assert f'hx-get="/titles/{title.pk}/"' in body
    assert 'hx-select="#title-body"' in body
    assert 'hx-target="#title-body"' in body
    assert 'hx-swap="outerHTML"' in body
    assert 'aria-busy="true"' in html
    assert 'Fresh' in html
    assert 'library-panel' not in html
    assert queued_language.sync == [(title.pk, False)]


def test_the_poll_stops_once_the_title_is_synced(
    client, portal_headers, alice, queued_language
):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Fresh')
    Title.objects.filter(pk=title.pk).update(synced_at=timezone.now())
    html = open_title(client, portal_headers, title)
    body = re.search(r'<div[^>]*id="title-body"[^>]*>', html).group()
    assert 'hx-trigger' not in body and 'every 3s' not in html


def test_stuck_title_offers_a_retry(client, portal_headers, alice, queued_language):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Stuck')
    Title.objects.filter(pk=title.pk).update(
        created_at=timezone.now() - timedelta(minutes=20)
    )
    html = open_title(client, portal_headers, title)
    assert 'every 3s' not in html
    assert 'role="alert"' in html
    assert f'hx-post="/titles/{title.pk}/refresh/"' in html
    assert 'Try again' in html


def test_a_title_younger_than_fifteen_minutes_is_still_syncing(
    client, portal_headers, alice, queued_language
):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Young')
    Title.objects.filter(pk=title.pk).update(
        created_at=timezone.now() - timedelta(minutes=14)
    )
    html = open_title(client, portal_headers, title)
    assert 'every 3s' in html and 'role="alert"' not in html


def test_refresh_queues_one_forced_sync_and_returns_the_syncing_block(
    client, portal_headers, alice, queued_language
):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Stuck')
    Title.objects.filter(pk=title.pk).update(
        created_at=timezone.now() - timedelta(minutes=20)
    )
    response = post(client, portal_headers, title, 'refresh')
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="title-body"' in html and 'every 3s' in html
    assert '<html' not in html
    assert queued_language.sync == [(title.pk, True)]
    again = post(client, portal_headers, title, 'refresh')
    assert again.status_code == 200 and 'every 3s' in again.content.decode()
    assert queued_language.sync == [(title.pk, True)]  # the claim holds
    # while the refresh runs the page does not show the error block again
    assert 'every 3s' in open_title(client, portal_headers, title)


def test_refresh_survives_a_redis_outage(
    client, portal_headers, alice, queued_language, monkeypatch
):
    def down(*args, **kwargs):
        raise OSError('redis is down')

    monkeypatch.setattr('catalog.tasks.connect_redis', down)
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Stuck')
    response = post(client, portal_headers, title, 'refresh')
    assert response.status_code == 200
    assert queued_language.sync == []
    assert client.get(f'/titles/{title.pk}/', **portal_headers).status_code == 200


def test_refresh_is_post_only_and_unknown_titles_are_404(
    client, portal_headers, alice, queued_language
):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='T')
    assert (
        client.get(f'/titles/{title.pk}/refresh/', **portal_headers).status_code == 405
    )
    assert client.post('/titles/99999/refresh/', **portal_headers).status_code == 404


def test_movie_page_has_no_episode_section(client, portal_headers, alice, today):
    movie = make_movie()
    Title.objects.filter(pk=movie.pk).update(
        synced_at=timezone.now(), next_sync_at=timezone.now() + timedelta(hours=1)
    )
    html = open_title(client, portal_headers, movie)
    assert 'id="episodes"' not in html
    assert 'No episodes announced yet' not in html
    assert 'library-panel' in html


def test_series_page_groups_episodes_by_season(
    client, portal_headers, alice, synced_show
):
    main = first_episode(synced_show)
    upcoming = future_episode(synced_show)
    html = open_title(client, portal_headers, synced_show)
    assert 'id="episodes"' in html
    seasons = Season.objects.filter(title=synced_show)
    assert f'id="grp-{seasons[0].pk}"' in html and f'id="grp-{seasons[1].pk}"' in html
    for episode in Episode.objects.filter(season__title=synced_show):
        assert f'id="ep-{episode.pk}"' in html
    assert 'S1E1' in html
    assert 'Season 1' in html
    assert 'Episode 1' in html  # no translations: the generic name
    assert 'Date not announced' in html  # the undated episode
    assert 'Airs in 7 days' in html
    assert main.pk != upcoming.pk
    # the extra season is of the special kind
    assert 'Specials' in html and 'SP 1' in html


def test_episode_rows_show_state_icons(client, portal_headers, alice, bob, synced_show):
    watched = first_episode(synced_show)
    track(alice, synced_show, status='watching')
    track(bob, synced_show, status='watching')
    watch(alice, watched)
    html = open_title(client, portal_headers, synced_show)
    row = re.search(rf'<li[^>]*id="ep-{watched.pk}".*?</li>', html, re.S).group()
    assert 'mt-check--watched' in row
    plain = Episode.objects.filter(season__title=synced_show, air_date=None).get()
    undated = re.search(rf'<li[^>]*id="ep-{plain.pk}".*?</li>', html, re.S).group()
    assert 'mt-check--unaired' in undated
    future = future_episode(synced_show)
    row = re.search(rf'<li[^>]*id="ep-{future.pk}".*?</li>', html, re.S).group()
    assert 'mt-check--unaired' in row
    second = Episode.objects.get(season__title=synced_show, number=2, season__number=1)
    row = re.search(rf'<li[^>]*id="ep-{second.pk}".*?</li>', html, re.S).group()
    assert 'mt-check--unwatched' in row
    group = re.search(r'<span[^>]*id="check-grp-\d+"[^>]*>', html).group()
    assert 'mt-check--partly' in group
    # bob's viewings never colour alice's page
    assert html.count('mt-check--watched') == 1


def test_the_check_has_a_stable_dom_contract(
    client, portal_headers, alice, synced_show
):
    episode = first_episode(synced_show)
    html = open_title(client, portal_headers, synced_show)
    check = re.search(rf'<span[^>]*id="check-ep-{episode.pk}"[^>]*>', html).group()
    assert 'class="mt-check mt-check--unwatched"' in check
    assert 'data-scope="episode"' in check
    assert f'data-id="{episode.pk}"' in check
    assert 'aria-hidden="true"' in check  # the button around it is the labelled control


def test_anime_page_has_franchise_chips_and_kind_tags(client, portal_headers, alice):
    title = make_anime()
    html = open_title(client, portal_headers, title)
    chips = re.search(r'<nav[^>]*class="mt-chips".*?</nav>', html, re.S).group()
    assert 'Cour One' in chips
    assert 'Season 2' in chips  # an unnamed TV entry
    assert 'OVA' in chips  # an unnamed OVA shows its kind
    assert 'The Movie' in chips
    for season in Season.objects.filter(title=title):
        assert f'href="#grp-{season.pk}"' in chips
        assert f'id="grp-{season.pk}"' in html
    ova = Season.objects.get(title=title, kind='ova')
    group = re.search(
        rf'<section[^>]*id="grp-{ova.pk}".*?</section>', html, re.S
    ).group()
    assert 'tag' in group and 'OVA' in group
    movie = Season.objects.get(title=title, kind='movie')
    group = re.search(
        rf'<section[^>]*id="grp-{movie.pk}".*?</section>', html, re.S
    ).group()
    assert 'tag' in group and 'Movie' in group
    # a continuation season shows the absolute number and the season code
    cont = Episode.objects.get(season__number=2, number=1)
    row = re.search(rf'<li[^>]*id="ep-{cont.pk}".*?</li>', html, re.S).group()
    assert '4' in row and 'S2E1' in row


def test_series_page_has_no_chip_row(client, portal_headers, alice, synced_show):
    assert 'mt-chips' not in open_title(client, portal_headers, synced_show)


def test_localized_text_and_language_job(
    client, portal_headers, alice, synced_show, queued_language
):
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    TitleTranslation.objects.create(
        title=synced_show, language='en', name='English name', overview='English text.'
    )
    Title.objects.filter(pk=synced_show.pk).update(original_title='Original')
    html = open_title(client, portal_headers, synced_show)
    assert 'English text.' in html  # the best available text
    assert queued_language.language == [(synced_show.pk, 'ru')]
    open_title(client, portal_headers, synced_show)
    assert queued_language.language == [(synced_show.pk, 'ru')]  # claimed once


def test_a_present_translation_is_shown_and_queues_no_job(
    client, portal_headers, alice, synced_show, queued_language
):
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    TitleTranslation.objects.create(
        title=synced_show, language='ru', name='Имя', overview='Описание на русском.'
    )
    episode = first_episode(synced_show)
    EpisodeTranslation.objects.create(episode=episode, language='ru', name='Первая')
    html = open_title(client, portal_headers, synced_show)
    assert 'Описание на русском.' in html and 'Первая' in html
    assert queued_language.language == []


def test_english_viewers_queue_no_language_job(
    client, portal_headers, alice, synced_show, queued_language
):
    open_title(client, portal_headers, synced_show)
    assert queued_language.language == [] and queued_language.sync == []


def test_a_due_title_queues_a_sync(client, portal_headers, alice, queued_language):
    title = Title.objects.create(
        kind=Title.Kind.TV, original_title='Stale', synced_at=timezone.now()
    )
    open_title(client, portal_headers, title)
    assert queued_language.sync == [(title.pk, False)]


def test_queueing_failures_never_break_the_page(
    client, portal_headers, alice, queued_language, monkeypatch
):
    def down(*args, **kwargs):
        raise OSError('redis is down')

    monkeypatch.setattr('catalog.tasks.connect_redis', down)
    title = Title.objects.create(
        kind=Title.Kind.TV, original_title='Stale', synced_at=timezone.now()
    )
    assert open_title(client, portal_headers, title)


def test_unknown_title_is_404(client, portal_headers, alice):
    assert client.get('/titles/99999/', **portal_headers).status_code == 404
    for action in ('status', 'score', 'note'):
        response = client.post(f'/titles/99999/{action}/', {}, **portal_headers)
        assert response.status_code == 404


def test_anonymous_visitors_do_not_reach_the_page(client, synced_show):
    assert client.get(f'/titles/{synced_show.pk}/').status_code in (302, 401, 403)


def test_text_is_escaped(client, portal_headers, alice, synced_show):
    Title.objects.filter(pk=synced_show.pk).update(original_title='<b>bold</b>')
    track(alice, synced_show, note='<script>alert(1)</script>')
    TitleTranslation.objects.create(
        title=synced_show, language='en', overview='<i>over</i>'
    )
    episode = first_episode(synced_show)
    EpisodeTranslation.objects.create(
        episode=episode, language='en', name='<u>name</u>'
    )
    html = open_title(client, portal_headers, synced_show)
    for raw in ('<b>bold', '<script>alert', '<i>over', '<u>name'):
        assert raw not in html
    assert '&lt;b&gt;bold&lt;/b&gt;' in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


def test_the_page_costs_the_same_queries_for_a_long_show(
    client, portal_headers, alice, synced_show
):
    open_title(client, portal_headers, synced_show)  # warm up the profile
    with CaptureQueriesContext(connection) as short:
        open_title(client, portal_headers, synced_show)
    season = Season.objects.filter(title=synced_show, number=1).get()
    Episode.objects.bulk_create(
        Episode(season=season, number=n, air_date=TODAY - timedelta(days=n))
        for n in range(10, 60)
    )
    with CaptureQueriesContext(connection) as long:
        open_title(client, portal_headers, synced_show)
    assert len(long) == len(short)


def test_status_score_note_update_the_panel(client, portal_headers, alice, synced_show):
    response = post(client, portal_headers, synced_show, 'status', status='watching')
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="library-panel"' in html and '<html' not in html
    assert 'Watching' in html
    entry = LibraryEntry.objects.for_user(alice).get(title=synced_show)
    assert entry.status == 'watching'

    response = post(client, portal_headers, synced_show, 'score', score='8')
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count('mt-star--full') == 4
    assert re.search(r'<option value="8" selected', html)
    assert LibraryEntry.objects.for_user(alice).get().score == 8

    response = post(client, portal_headers, synced_show, 'score', score='7')
    html = response.content.decode()
    assert html.count('mt-star--full') == 3 and html.count('mt-star--half') == 1

    response = post(client, portal_headers, synced_show, 'note', note='good so far')
    assert response.status_code == 200
    assert 'good so far' in response.content.decode()
    assert LibraryEntry.objects.for_user(alice).get().note == 'good so far'


def test_the_panel_is_built_for_the_entry_actions(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, status='planned')
    html = open_title(client, portal_headers, synced_show)
    panel = re.search(
        r'<section[^>]*id="library-panel".*?</section>', html, re.S
    ).group()
    assert f'hx-post="/titles/{synced_show.pk}/status/"' in panel
    assert 'hx-target="#library-panel"' in panel
    assert 'hx-swap="outerHTML"' in panel
    assert re.search(r'hx-confirm="[^"]+"', panel)  # removal asks first
    assert f'hx-post="/titles/{synced_show.pk}/score/"' in panel
    assert f'hx-post="/titles/{synced_show.pk}/note/"' in panel
    assert 'maxlength="5000"' in panel
    assert panel.count('<option') == 11
    assert 'Not rated' in panel and '10/10' in panel
    assert '<label' in panel and 'for="score-select"' in panel
    assert 'for="note-text"' in panel


def test_the_panel_without_an_entry_has_no_score_or_note(
    client, portal_headers, alice, synced_show
):
    html = open_title(client, portal_headers, synced_show)
    panel = re.search(
        r'<section[^>]*id="library-panel".*?</section>', html, re.S
    ).group()
    assert '<select' not in panel and '<textarea' not in panel


def test_status_completed_marks_the_aired_episodes_in_the_same_response(
    client, portal_headers, alice, synced_show
):
    response = post(client, portal_headers, synced_show, 'status', status='completed')
    html = response.content.decode()
    assert 'id="library-panel"' in html
    assert re.search(r'<section[^>]*id="episodes"[^>]*hx-swap-oob="true"', html)
    assert html.count('mt-check--watched') >= 3


def test_status_remove_deletes_the_entry_and_keeps_events(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, status='watching', score=4, note='n')
    episode = first_episode(synced_show)
    watch(alice, episode)
    response = post(client, portal_headers, synced_show, 'status', status='remove')
    assert response.status_code == 200
    assert 'id="library-panel"' in response.content.decode()
    assert not LibraryEntry.objects.for_user(alice).filter(title=synced_show).exists()
    assert WatchEvent.objects.for_user(alice).filter(episode=episode).count() == 1


def test_actions_never_touch_another_users_entry(
    client, portal_headers, alice, bob, synced_show
):
    track(bob, synced_show, status='watching', score=9, note='bob')
    post(client, portal_headers, synced_show, 'status', status='dropped')
    assert (
        post(client, portal_headers, synced_show, 'score', score='2').status_code == 200
    )
    post(client, portal_headers, synced_show, 'note', note='alice')
    mine = LibraryEntry.objects.for_user(alice).get(title=synced_show)
    assert (mine.status, mine.score, mine.note) == ('dropped', 2, 'alice')
    post(client, portal_headers, synced_show, 'status', status='remove')
    theirs = LibraryEntry.objects.for_user(bob).get(title=synced_show)
    assert (theirs.status, theirs.score, theirs.note) == ('watching', 9, 'bob')


@pytest.mark.parametrize('status', ['', 'bogus', 'WATCHING'])
def test_invalid_status_is_400(client, portal_headers, alice, synced_show, status):
    response = post(client, portal_headers, synced_show, 'status', status=status)
    assert response.status_code == 400
    assert not LibraryEntry.objects.for_user(alice).exists()


def test_missing_status_is_400(client, portal_headers, alice, synced_show):
    assert post(client, portal_headers, synced_show, 'status').status_code == 400


@pytest.mark.parametrize(
    'score', ['', 'x', '-1', '11', '3.5', '999999999999', '9' * 5000]
)
def test_invalid_score_is_400(client, portal_headers, alice, synced_show, score):
    track(alice, synced_show)
    response = post(client, portal_headers, synced_show, 'score', score=score)
    assert response.status_code == 400
    assert LibraryEntry.objects.for_user(alice).get().score == 0


def test_invalid_note_is_400(client, portal_headers, alice, synced_show):
    track(alice, synced_show)
    assert post(client, portal_headers, synced_show, 'note').status_code == 400
    long_note = 'x' * 5001
    response = post(client, portal_headers, synced_show, 'note', note=long_note)
    assert response.status_code == 400
    assert LibraryEntry.objects.for_user(alice).get().note == ''
    ok = post(client, portal_headers, synced_show, 'note', note='x' * 5000)
    assert ok.status_code == 200


def test_score_and_note_without_an_entry_are_409(
    client, portal_headers, alice, synced_show
):
    assert (
        post(client, portal_headers, synced_show, 'score', score='5').status_code == 409
    )
    assert (
        post(client, portal_headers, synced_show, 'note', note='n').status_code == 409
    )
    assert not LibraryEntry.objects.for_user(alice).exists()


def test_actions_are_post_only(client, portal_headers, alice, synced_show):
    for action in ('status', 'score', 'note'):
        url = f'/titles/{synced_show.pk}/{action}/'
        assert client.get(url, **portal_headers).status_code == 405


def test_title_page_is_translated_to_russian(
    client, portal_headers, alice, synced_show
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    track(alice, synced_show, status='watching')
    html = open_title(client, portal_headers, synced_show)
    for text in (
        'Описание',
        'Серии',
        'Моя оценка',
        'Личная заметка',
        'Сохранить',
        'Не оценено',
        'Дата не объявлена',
        'Сезон 1',
        'Серия 1',
        'Отметить просмотренной: S1E1',
        'Эфир через 7 дн',
    ):
        assert text in html, text
    for text in ('Description', 'Not rated', 'Save note', 'Date not announced'):
        assert text not in html, text


def test_syncing_and_empty_states_are_translated_to_russian(
    client, portal_headers, alice, queued_language
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Fresh')
    html = open_title(client, portal_headers, title)
    assert 'Загружаем данные' in html
    Title.objects.filter(pk=title.pk).update(
        created_at=timezone.now() - timedelta(minutes=20)
    )
    html = open_title(client, portal_headers, title)
    assert 'Повторить' in html and 'Try again' not in html


def star_buttons(html: str) -> list[dict[str, str]]:
    """Return the attributes of the star buttons of the library panel."""
    group = re.search(
        r'<span[^>]*role="group"[^>]*aria-label="My rating".*?</span>\s*<select',
        html,
        re.S,
    )
    assert group, 'no star group'
    buttons = re.findall(r'<button[^>]*>', group.group())
    return [dict(re.findall(r'([\w-]+)="([^"]*)"', tag)) for tag in buttons]


@pytest.mark.parametrize(
    ('score', 'values', 'pressed', 'classes'),
    [
        (6, ['2', '4', '0', '8', '10'], 'ttt' + 'ff', 'full full full empty empty'),
        (7, ['2', '4', '6', '8', '10'], 'ttt' + 'ff', 'full full full half empty'),
        (0, ['2', '4', '6', '8', '10'], 'fffff', 'empty empty empty empty empty'),
    ],
)
def test_star_buttons_rate_the_title(
    client, portal_headers, alice, synced_show, score, values, pressed, classes
):
    track(alice, synced_show, score=score)
    html = open_title(client, portal_headers, synced_show)
    panel = re.search(
        r'<section[^>]*id="library-panel".*?</section>', html, re.S
    ).group()
    form = re.search(r'<form[^>]*class="mt-lib-score"[^>]*>', panel).group()
    assert f'hx-post="/titles/{synced_show.pk}/score/"' in form
    buttons = star_buttons(panel)
    assert [b['type'] for b in buttons] == ['submit'] * 5
    assert [b['name'] for b in buttons] == ['score'] * 5
    assert [b['value'] for b in buttons] == values  # the current whole star clears
    assert (
        ''.join('t' if b['aria-pressed'] == 'true' else 'f' for b in buttons) == pressed
    )
    assert [b['class'].split('--')[1] for b in buttons] == classes.split()
    assert all(b['aria-label'] for b in buttons)
    assert 'mt-touch' in buttons[0]['class']
    assert '<select id="score-select"' in panel  # the fine-grained fallback stays


def test_clicking_the_current_star_clears_the_score(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, score=6)
    response = post(client, portal_headers, synced_show, 'score', score='0')
    assert response.status_code == 200
    assert LibraryEntry.objects.for_user(alice).get().score == 0


def test_the_star_row_is_shown_from_600_px_only():
    css = (Path(__file__).parents[1] / 'sources/web/static/web/app.css').read_text()
    assert re.search(r'\.mt-stars \{[^}]*display: none', css)
    assert re.search(
        r'@media \(min-width: 600px\) \{[^@]*\.mt-stars \{[^}]*display: inline-flex',
        css,
    )


def test_progress_survives_removing_the_entry(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, status='watching')
    watch(alice, first_episode(synced_show))
    post(client, portal_headers, synced_show, 'status', status='remove')
    html = open_title(client, portal_headers, synced_show)
    assert 'mt-progress-bar' in html
    assert '1/5' in html
    assert 'mt-check--watched' in html


def test_no_progress_bar_without_an_entry_or_a_viewing(
    client, portal_headers, alice, synced_show
):
    assert 'mt-progress-bar' not in open_title(client, portal_headers, synced_show)


def test_an_empty_name_falls_back_to_untitled(
    client, portal_headers, alice, synced_show
):
    Title.objects.filter(pk=synced_show.pk).update(original_title='')
    html = open_title(client, portal_headers, synced_show)
    assert '<h1 class="mt-th-title">Untitled</h1>' in html
    assert '<title>Untitled · mitaroku</title>' in html
    assert 'mt-th-original' not in html
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    assert 'Без названия' in open_title(client, portal_headers, synced_show)


def test_a_tmdb_season_without_rows_says_so(client, portal_headers, alice, synced_show):
    Season.objects.create(
        title=synced_show,
        number=3,
        kind=Season.Kind.TV,
        episode_source=Season.EpisodeSource.TMDB,
    )
    html = open_title(client, portal_headers, synced_show)
    assert html.count('No episodes yet') == 1
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    assert 'Серий пока нет' in open_title(client, portal_headers, synced_show)


def test_status_of_a_never_synced_title_is_409_and_writes_nothing(
    client, portal_headers, alice, queued_language
):
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Fresh')
    response = post(client, portal_headers, title, 'status', status='watching')
    assert response.status_code == 409
    assert not LibraryEntry.objects.for_user(alice).exists()


def test_the_panel_is_named_by_its_heading(client, portal_headers, alice, synced_show):
    html = open_title(client, portal_headers, synced_show)
    section = re.search(r'<section[^>]*id="library-panel"[^>]*>', html).group()
    assert (
        'aria-labelledby="library-heading"' in section and 'aria-label=' not in section
    )
    assert 'id="library-heading"' in html
