"""Undated episodes (an episode without an air date) and the episode codes."""

import re

import pytest
from catalog.models import Episode, Season
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import (
    TODAY,
    future_episode,
    make_ova_first_anime,
    make_undated_show,
    watch,
)
from web.templatetags.web_extras import episode_code

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def open_title(client, headers, title) -> str:
    response = client.get(f'/titles/{title.pk}/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def track(user, title, status: str = 'watching') -> LibraryEntry:
    return LibraryEntry.objects.create(user=user, title=title, status=status)


def row(html: str, episode: Episode) -> str:
    return re.search(rf'<li[^>]*id="ep-{episode.pk}".*?</li>', html, re.S).group()


def button(html: str, element_id: str) -> str:
    return re.search(rf'<button[^>]*id="{element_id}"[^>]*>', html, re.S).group()


# The check state of undated episodes ---------------------------------------


def test_the_check_of_an_undated_ova_is_enabled_for_an_ended_title(
    client, portal_headers, alice, today
):
    title, mains, ova = make_undated_show()
    track(alice, title)
    html = open_title(client, portal_headers, title)
    assert 'mt-check--unwatched' in row(html, ova[0])
    mark = button(html, f'mark-ep-{ova[0].pk}')
    assert 'disabled' not in mark and 'hx-post' in mark
    group = button(html, f'mark-grp-{ova[0].season_id}')
    assert 'disabled' not in group and 'hx-post' in group
    assert 'Air date not announced' not in group
    # the date line of the row still says that no date is known
    assert 'Date not announced' in row(html, ova[0])


def test_marking_an_undated_ova_of_an_ended_title_works_through_the_view(
    client, portal_headers, alice, today
):
    title, mains, ova = make_undated_show()
    response = client.post(
        f'/episodes/{ova[0].pk}/watch/', {'watched': '1'}, **portal_headers
    )
    assert response.status_code == 200
    assert 'mt-check--watched' in row(response.content.decode(), ova[0])
    season = ova[0].season
    response = client.post(
        f'/seasons/{season.pk}/watch/', {'watched': '1'}, **portal_headers
    )
    assert response.status_code == 200
    assert (
        WatchEvent.objects.for_user(alice).filter(episode__season=season).count() == 3
    )


def test_the_check_of_an_undated_ova_of_an_airing_title_is_locked_and_says_why(
    client, portal_headers, alice, today
):
    title, mains, ova = make_undated_show(status='airing')
    track(alice, title)
    html = open_title(client, portal_headers, title)
    assert 'mt-check--unaired' in row(html, ova[0])
    mark = button(html, f'mark-ep-{ova[0].pk}')
    assert 'disabled' in mark and 'hx-post' not in mark
    assert 'title="Air date not announced"' in mark
    assert 'Air date not announced' in re.search(r'aria-label="([^"]*)"', mark).group(1)
    assert 'Not aired yet' not in mark
    group = button(html, f'mark-grp-{ova[0].season_id}')
    assert 'disabled' in group and 'title="Air date not announced"' in group
    response = client.post(
        f'/episodes/{ova[0].pk}/watch/', {'watched': '1'}, **portal_headers
    )
    assert response.status_code == 409
    assert not WatchEvent.objects.exists()


def test_a_dated_future_episode_still_says_not_aired_yet(
    client, portal_headers, alice, synced_show
):
    html = open_title(client, portal_headers, synced_show)
    soon = button(html, f'mark-ep-{future_episode(synced_show).pk}')
    assert 'title="Not aired yet"' in soon and 'Air date not announced' not in soon
    undated = Episode.objects.get(season__title=synced_show, air_date=None)
    assert 'title="Air date not announced"' in button(html, f'mark-ep-{undated.pk}')


def test_the_whole_show_check_says_why_when_nothing_has_a_date(
    client, portal_headers, alice, today
):
    title, mains, _ = make_undated_show(status='airing', main_undated=True)
    html = open_title(client, portal_headers, title)
    whole = button(html, 'mark-all')
    assert 'disabled' in whole and 'title="Air date not announced"' in whole
    finished, _, _ = make_undated_show(main_undated=True)
    html = open_title(client, portal_headers, finished)
    assert 'disabled' not in button(html, 'mark-all')


def test_undated_main_episodes_of_an_ended_title_get_a_continue_button(
    client, portal_headers, alice, today
):
    title, mains, _ = make_undated_show(main_undated=True)
    track(alice, title)
    html = open_title(client, portal_headers, title)
    bar = re.search(r'<div[^>]*id="continue-bar".*?</div>', html, re.S).group()
    assert 'continue-btn' in bar and f'"episode": "{mains[0].pk}"' in bar
    response = client.post(
        f'/titles/{title.pk}/continue/', {'episode': mains[0].pk}, **portal_headers
    )
    assert response.status_code == 200
    assert WatchEvent.objects.for_user(alice).get().episode == mains[0]


def test_an_undated_next_episode_of_an_airing_title_has_no_continue_button(
    client, portal_headers, alice, today
):
    title, mains, _ = make_undated_show(status='airing', main_undated=True)
    track(alice, title)
    html = open_title(client, portal_headers, title)
    bar = re.search(r'<div[^>]*id="continue-bar".*?</div>', html, re.S).group()
    assert 'continue-btn' not in bar and 'date not announced' in bar
    response = client.post(
        f'/titles/{title.pk}/continue/', {'episode': mains[0].pk}, **portal_headers
    )
    assert response.status_code == 409


def test_the_progress_line_counts_main_episodes_only_and_says_so(
    client, portal_headers, alice, today
):
    title, mains, ova = make_undated_show()
    track(alice, title)
    watch(alice, mains[0], ova[0])
    html = open_title(client, portal_headers, title)
    progress = re.search(
        r'<div[^>]*id="title-progress".*?mt-progress-suffix.*?</span>', html, re.S
    )
    assert re.search(r'mt-progress-text">1/2</span>', progress.group())
    assert 'mt-progress-suffix">main episodes</span>' in progress.group()
    # the extras keep their own count
    assert re.search(r'mt-gh-count">1/3<', html)


def test_the_library_offers_a_finished_undated_next_episode(
    client, portal_headers, alice, today
):
    title, mains, _ = make_undated_show(main_undated=True)
    airing, _, _ = make_undated_show(status='airing', main_undated=True)
    track(alice, title)
    track(alice, airing)
    html = client.get('/library/', **portal_headers).content.decode()
    strip = re.search(r'<section id="continue-watching".*?</section>', html, re.S)
    strip = strip.group()
    assert f'hx-vals=\'{{"episode": "{mains[0].pk}", "ui": "library"}}\'' in strip
    assert strip.count('mt-cw-check') == 1  # only the finished title has a button
    assert 'S1E1 available' in html
    assert 'Next: date not announced' in html
    # no date is known, so no "Aired ..." line is made up
    assert 'Aired' not in strip


def test_a_season_that_has_not_started_is_not_open_for_a_finished_title(
    client, portal_headers, alice, today
):
    title, mains, ova = make_undated_show(start=None)
    html = open_title(client, portal_headers, title)
    assert 'mt-check--unaired' in row(html, ova[0])
    Season.objects.filter(pk=ova[0].season_id).update(start_date=TODAY)
    assert 'mt-check--unwatched' in row(
        open_title(client, portal_headers, title), ova[0]
    )


# Episode codes of anime ---------------------------------------------------


def test_codes_follow_the_tmdb_season_not_the_franchise_entry(
    client, portal_headers, alice, today
):
    title = make_ova_first_anime()
    track(alice, title)
    html = open_title(client, portal_headers, title)
    entry_two = Season.objects.get(title=title, number=2)
    first = Episode.objects.get(season=entry_two, number=1)
    assert '>S1E1<' in row(html, first)
    assert 'S2E1' not in row(html, first)
    # the continuation entry: the absolute number first, the TMDB code small
    entry_three = Season.objects.get(title=title, number=3)
    cont = Episode.objects.get(season=entry_three, number=1)
    assert re.search(r'>4</span><span class="mt-ep-num2"[^>]*>S2E1<', row(html, cont))
    # the OVA keeps its own label and the franchise index shows nowhere
    ova = Episode.objects.get(season__title=title, season__number=1)
    assert 'OVA 1' in row(html, ova)
    assert 'S3E' not in html and 'S1E4' not in html


def test_an_entry_without_tmdb_numbers_gets_the_plain_episode_number(
    client, portal_headers, alice, today
):
    title = make_ova_first_anime()
    Episode.objects.filter(season__title=title).update(
        tmdb_season_number=None, tmdb_episode_number=None
    )
    html = open_title(client, portal_headers, title)
    entry_two = Season.objects.get(title=title, number=2)
    first = Episode.objects.get(season=entry_two, number=1)
    assert '>E1<' in row(html, first)
    assert 'S2E1' not in html and 'S1E1' not in html


def test_the_number_cells_explain_themselves(client, portal_headers, alice, today):
    title = make_ova_first_anime()
    entry_three = Season.objects.get(title=title, number=3)
    html = open_title(client, portal_headers, title)
    cont = row(html, Episode.objects.get(season=entry_three, number=1))
    assert 'title="Episode number across the whole show"' in cont
    assert 'title="Season and episode number"' in cont


def test_the_library_and_the_strip_use_the_same_codes(
    client, portal_headers, alice, today
):
    title = make_ova_first_anime()
    track(alice, title)
    html = client.get('/library/', **portal_headers).content.decode()
    assert 'S1E1 available' in html
    assert 'S2E1' not in html


def test_the_upcoming_page_uses_the_same_codes(client, portal_headers, alice, today):
    title = make_ova_first_anime()
    track(alice, title)
    episodes = Episode.objects.filter(season__title=title, season__is_extra=False)
    watch(alice, *episodes.exclude(air_date__gt=TODAY))
    html = client.get('/upcoming/', **portal_headers).content.decode()
    assert 'S2E2 · Episode 2' in html
    assert 'S3E' not in html


def test_the_code_helper_for_series_and_anime(today):
    title = make_ova_first_anime()
    cont = Episode.objects.get(season__title=title, season__number=3, number=2)
    assert episode_code(cont, 'anime') == 'S2E2'
    ova = Episode.objects.get(season__title=title, season__number=1)
    assert episode_code(ova, 'anime') == 'E1'
    assert episode_code(cont, 'tv') == 'S3E2'  # a series uses its season number
