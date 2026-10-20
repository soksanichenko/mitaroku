import re
from datetime import timedelta

import pytest
from catalog.models import Episode, EpisodeTranslation, Season, Title
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import TODAY, first_episode, future_episode, make_movie, watch

pytestmark = pytest.mark.django_db

ACTION_URLS = (
    '/episodes/{ep}/watch/',
    '/episodes/{ep}/up-to/',
    '/seasons/{season}/watch/',
    '/titles/{title}/watch-all/',
    '/titles/{title}/movie/',
    '/titles/{title}/continue/',
)


def act(client, headers, path: str, **data):
    """POST ``data`` to an action URL."""
    return client.post(path, data, **headers)


def watch_episode(client, headers, episode, watched='1', **data):
    """POST the wanted state of one episode."""
    return act(
        client, headers, f'/episodes/{episode.pk}/watch/', watched=watched, **data
    )


def tag(html: str, element_id: str) -> str:
    """Return the opening tag of the element with ``element_id`` (empty if absent)."""
    found = re.search(rf'<[a-z0-9]+[^>]*\bid="{re.escape(element_id)}"[^>]*>', html)
    return found.group() if found else ''


def assert_unique_ids(html: str) -> None:
    """Assert that no DOM id appears twice in an HTML fragment."""
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def track(user, title, **fields) -> LibraryEntry:
    """Put ``title`` into the library of ``user``."""
    return LibraryEntry.objects.create(user=user, title=title, **fields)


def mains(title: Title) -> list[Episode]:
    """Return the main episodes of ``title`` in order."""
    return list(
        Episode.objects.filter(season__title=title, season__is_extra=False).order_by(
            'season__number', 'number'
        )
    )


def make_two_seasons() -> tuple[Title, Season, Season]:
    """Create a synced show with two aired main seasons of two episodes each."""
    title = Title.objects.create(
        kind=Title.Kind.TV,
        original_title='Two seasons',
        status=Title.Status.ENDED,
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    seasons = []
    for number in (1, 2):
        season = Season.objects.create(
            title=title,
            number=number,
            kind=Season.Kind.TV,
            episode_source=Season.EpisodeSource.TMDB,
        )
        for index in (1, 2):
            Episode.objects.create(
                season=season,
                number=index,
                air_date=TODAY - timedelta(days=100 - number * 10 - index),
            )
        seasons.append(season)
    return title, seasons[0], seasons[1]


@pytest.fixture
def movie(db, today) -> Title:
    """Return a synced movie released before ``TODAY``."""
    title = make_movie('Released', release_offset=-10)
    now = timezone.now()
    Title.objects.filter(pk=title.pk).update(
        synced_at=now, next_sync_at=now + timedelta(hours=1)
    )
    title.refresh_from_db()
    return title


def open_title(client, headers, title) -> str:
    """Return the HTML of the title page."""
    response = client.get(f'/titles/{title.pk}/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def test_actions_only_touch_the_requesting_user(
    client, portal_headers, other_headers, alice, bob, synced_show
):
    ep = first_episode(synced_show)
    watch_episode(client, portal_headers, ep)
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert WatchEvent.objects.for_user(bob).count() == 0
    watch_episode(client, other_headers, ep, watched='0')  # bob unwatches
    assert WatchEvent.objects.for_user(alice).count() == 1  # alice is untouched
    assert not LibraryEntry.objects.for_user(bob).exists()


def test_season_and_title_actions_only_touch_the_requesting_user(
    client, portal_headers, other_headers, alice, bob, synced_show
):
    season = Season.objects.get(title=synced_show, number=1)
    act(client, portal_headers, f'/seasons/{season.pk}/watch/', watched='1')
    act(client, other_headers, f'/titles/{synced_show.pk}/watch-all/', watched='0')
    assert WatchEvent.objects.for_user(alice).count() == 3
    assert WatchEvent.objects.for_user(bob).count() == 0


def test_unaired_episode_is_refused_by_the_endpoint(
    client, portal_headers, alice, synced_show
):
    response = watch_episode(client, portal_headers, future_episode(synced_show))
    assert response.status_code == 409
    assert '<' not in response.content.decode()
    assert not WatchEvent.objects.exists()
    assert not LibraryEntry.objects.exists()


def test_an_unaired_episode_cannot_be_rewatched_or_marked_up_to(
    client, portal_headers, alice, synced_show
):
    future = future_episode(synced_show)
    assert watch_episode(client, portal_headers, future, again='1').status_code == 409
    response = act(client, portal_headers, f'/episodes/{future.pk}/up-to/')
    assert response.status_code == 409
    assert not WatchEvent.objects.exists()


def test_posting_the_same_state_twice_adds_one_event(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    for _ in range(2):
        assert watch_episode(client, portal_headers, ep).status_code == 200
    assert WatchEvent.objects.for_user(alice).count() == 1
    for _ in range(2):
        assert watch_episode(client, portal_headers, ep, watched='0').status_code == 200
    assert WatchEvent.objects.for_user(alice).count() == 0


def test_watch_fragment_has_the_group_and_out_of_band_progress(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    html = watch_episode(client, portal_headers, ep).content.decode()
    group = Season.objects.get(title=synced_show, number=1)
    assert '<html' not in html
    assert f'id="grp-{group.pk}"' in html
    assert 'hx-swap-oob' not in tag(html, f'grp-{group.pk}')
    for oob in ('title-progress', 'continue-bar'):
        assert 'hx-swap-oob="true"' in tag(html, oob), oob
    assert 'mt-check--watched' in html
    assert '1/5' in html  # the progress is rebuilt from the new state
    assert_unique_ids(html)


def test_the_check_states_come_back_wanted_not_toggled(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    html = watch_episode(client, portal_headers, ep).content.decode()
    button = tag(html, f'mark-ep-{ep.pk}')
    assert 'aria-pressed="true"' in button
    assert '"watched": "0"' in button  # the next click asks to unwatch
    html = watch_episode(client, portal_headers, ep, watched='0').content.decode()
    button = tag(html, f'mark-ep-{ep.pk}')
    assert 'aria-pressed="false"' in button and '"watched": "1"' in button


def test_watching_resumes_a_planned_title_and_updates_the_panel(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, status='planned')
    html = watch_episode(client, portal_headers, first_episode(synced_show)).content
    html = html.decode()
    assert 'hx-swap-oob="true"' in tag(html, 'library-panel')
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'
    assert_unique_ids(html)


def test_watching_an_untracked_title_creates_the_entry_and_the_panel(
    client, portal_headers, alice, synced_show
):
    html = watch_episode(client, portal_headers, first_episode(synced_show)).content
    html = html.decode()
    assert 'hx-swap-oob="true"' in tag(html, 'library-panel')
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'


def test_the_panel_is_left_alone_when_the_status_does_not_change(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, status='watching', note='unsaved edits live here')
    html = watch_episode(client, portal_headers, first_episode(synced_show)).content
    assert 'library-panel' not in html.decode()


def test_the_last_episode_completes_an_ended_show_and_updates_the_panel(
    client, portal_headers, alice
):
    title, first, second = make_two_seasons()
    track(alice, title, status='watching')
    episodes = mains(title)
    watch(alice, *episodes[:-1])
    html = watch_episode(client, portal_headers, episodes[-1]).content.decode()
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'
    assert 'hx-swap-oob="true"' in tag(html, 'library-panel')
    assert 'Completed' in html


def test_again_adds_a_viewing_and_shows_the_count(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    watch_episode(client, portal_headers, ep)
    html = watch_episode(client, portal_headers, ep, again='1').content.decode()
    assert WatchEvent.objects.for_user(alice).filter(episode=ep).count() == 2
    row = re.search(rf'<li[^>]*id="ep-{ep.pk}".*?</li>', html, re.S).group()
    assert '×2' in row
    # unwatching deletes every viewing
    watch_episode(client, portal_headers, ep, watched='0')
    assert WatchEvent.objects.for_user(alice).count() == 0


def test_a_single_viewing_shows_no_count(client, portal_headers, alice, synced_show):
    ep = first_episode(synced_show)
    html = watch_episode(client, portal_headers, ep).content.decode()
    assert '×' not in html


def test_up_to_marks_earlier_aired_episodes_only(
    client, portal_headers, alice, synced_show
):
    target = mains(synced_show)[2]
    response = act(client, portal_headers, f'/episodes/{target.pk}/up-to/')
    assert response.status_code == 200
    watched = set(
        WatchEvent.objects.for_user(alice).values_list('episode__number', flat=True)
    )
    assert watched == {1, 2, 3}
    html = response.content.decode()
    assert 'hx-swap-oob="true"' in tag(html, 'title-progress')
    assert 'hx-swap-oob="true"' in tag(html, 'continue-bar')
    # repeating changes nothing
    act(client, portal_headers, f'/episodes/{target.pk}/up-to/')
    assert WatchEvent.objects.for_user(alice).count() == 3


def test_up_to_sends_the_earlier_seasons_out_of_band(client, portal_headers, alice):
    title, first, second = make_two_seasons()
    target = Episode.objects.get(season=second, number=1)
    html = act(client, portal_headers, f'/episodes/{target.pk}/up-to/').content
    html = html.decode()
    assert 'hx-swap-oob' not in tag(html, f'grp-{second.pk}')
    assert 'hx-swap-oob="true"' in tag(html, f'grp-{first.pk}')
    # first season: 2 rows and its group mark; second: 1 row (its group mark is partly)
    assert html.count('mt-check--watched') == 4
    assert WatchEvent.objects.for_user(alice).count() == 3
    assert_unique_ids(html)


def test_up_to_never_adds_the_hint_mark_but_a_single_mark_does(
    client, portal_headers, alice, synced_show
):
    one, two = mains(synced_show)[:2]
    single = watch_episode(client, portal_headers, one).content.decode()
    assert 'hx-swap-oob="true"' in tag(single, 'mark-up-to-hint')
    assert 'data-armed="true"' in tag(single, 'mark-up-to-hint')
    up_to = act(client, portal_headers, f'/episodes/{two.pk}/up-to/').content.decode()
    assert 'mark-up-to-hint' not in up_to
    again = watch_episode(client, portal_headers, one, again='1').content.decode()
    assert 'mark-up-to-hint' not in again
    undone = watch_episode(client, portal_headers, one, watched='0')
    assert 'mark-up-to-hint' not in undone.content.decode()


def test_the_hint_is_on_the_page_and_hidden_by_css_not_by_alpine_state(
    client, portal_headers, alice, synced_show
):
    """The tip is shown or hidden by data-armed and by a flag on <html>.

    An out-of-band swap resets the attributes of the element after Alpine started,
    so no state (x-show, a data object, inline style) may live on the element.
    """
    html = open_title(client, portal_headers, synced_show)
    hint = re.search(r'<div[^>]*id="mark-up-to-hint".*?</div>', html, re.S).group()
    assert 'data-armed="false"' in hint
    for state in ('x-show', 'x-cloak', 'dismissed', 'armed:', 'style='):
        assert state not in hint
    assert 'Tip: use the menu to mark everything up to an episode' in hint
    assert 'Dismiss' in hint
    assert 'mtDismissMarkUpToHint()' in hint
    css = (settings.BASE_DIR / 'web/static/web/app.css').read_text()
    assert '.mt-hint[data-armed="false"]' in css
    assert ':root[data-hint-mark-up-to="dismissed"] .mt-hint' in css
    assert '/static/web/hint.js' in html


def test_the_hint_script_keeps_the_flag_on_the_root_and_guards_storage():
    script = (settings.BASE_DIR / 'web/static/web/hint.js').read_text()
    assert "'mitaroku.hint.markUpTo'" in script
    assert script.count('try {') == 2  # read and write are both guarded
    assert "dataset.hintMarkUpTo = 'dismissed'" in script
    assert 'window.mtDismissMarkUpToHint' in script


def test_an_armed_hint_is_only_sent_after_a_single_mark(
    client, portal_headers, alice, synced_show
):
    first, second = mains(synced_show)[:2]
    track(alice, synced_show, status='watching')
    armed = watch_episode(client, portal_headers, first).content.decode()
    assert 'data-armed="true"' in tag(armed, 'mark-up-to-hint')
    # the whole-show action sends the section with an unarmed tip
    section = act(
        client, portal_headers, f'/titles/{synced_show.pk}/watch-all/', watched='1'
    )
    assert 'data-armed="false"' in tag(section.content.decode(), 'mark-up-to-hint')


def test_using_the_up_to_action_dismisses_the_hint(
    client, portal_headers, alice, synced_show
):
    html = open_title(client, portal_headers, synced_show)
    assert '@mt-up-to.window="mtDismissMarkUpToHint()"' in html
    assert "$dispatch('mt-up-to')" in html


def test_season_and_whole_title_actions(client, portal_headers, alice, synced_show):
    season = Season.objects.get(title=synced_show, number=1)
    response = act(client, portal_headers, f'/seasons/{season.pk}/watch/', watched='1')
    assert response.status_code == 200
    html = response.content.decode()
    assert f'id="grp-{season.pk}"' in html
    assert 'hx-swap-oob="true"' in tag(html, 'title-progress')
    assert_unique_ids(html)
    # aired episodes only: 3 of the 5
    assert WatchEvent.objects.for_user(alice).count() == 3
    act(client, portal_headers, f'/seasons/{season.pk}/watch/', watched='0')
    assert WatchEvent.objects.for_user(alice).count() == 0

    response = act(
        client, portal_headers, f'/titles/{synced_show.pk}/watch-all/', watched='1'
    )
    html = response.content.decode()
    # 3 aired main episodes and the aired extra one
    assert WatchEvent.objects.for_user(alice).count() == 4
    assert 'id="episodes"' in html
    assert 'hx-swap-oob' not in tag(html, 'episodes')
    assert html.count('id="title-progress"') == 1
    assert_unique_ids(html)
    act(client, portal_headers, f'/titles/{synced_show.pk}/watch-all/', watched='0')
    assert WatchEvent.objects.for_user(alice).count() == 0


def test_a_season_action_without_aired_episodes_changes_nothing(
    client, portal_headers, alice
):
    title = Title.objects.create(
        kind=Title.Kind.TV,
        original_title='Soon',
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    season = Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
    Episode.objects.create(season=season, number=1, air_date=TODAY + timedelta(days=3))
    response = act(client, portal_headers, f'/seasons/{season.pk}/watch/', watched='1')
    assert response.status_code == 200
    assert not WatchEvent.objects.exists() and not LibraryEntry.objects.exists()


def test_extras_can_be_checked_without_changing_progress(
    client, portal_headers, alice, synced_show
):
    extra = Episode.objects.get(season__title=synced_show, season__is_extra=True)
    html = watch_episode(client, portal_headers, extra).content.decode()
    assert WatchEvent.objects.for_user(alice).filter(episode=extra).count() == 1
    assert re.search(rf'<li[^>]*id="ep-{extra.pk}".*?mt-check--watched', html, re.S)
    progress = re.search(
        r'<[^>]*id="title-progress".*?mt-progress-text">([^<]*)<', html, re.S
    )
    assert progress.group(1) == '0/5'
    # the next episode is still the first main one
    assert 'S1E1' in html[html.index('id="continue-bar"') :]


def test_movie_watch_and_rewatch(client, portal_headers, alice, movie):
    url = f'/titles/{movie.pk}/movie/'
    html = act(client, portal_headers, url, watched='1').content.decode()
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert 'id="movie-watch"' in html
    assert 'Watched 1 time' in html
    assert 'hx-swap-oob="true"' in tag(html, 'library-panel')  # became Completed
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'
    act(client, portal_headers, url, watched='1')  # same state: nothing added
    assert WatchEvent.objects.for_user(alice).count() == 1
    html = act(client, portal_headers, url, watched='1', again='1').content.decode()
    assert WatchEvent.objects.for_user(alice).count() == 2
    assert 'Watched 2 times' in html
    assert 'library-panel' not in html  # the status did not change this time
    assert_unique_ids(html)
    html = act(client, portal_headers, url, watched='0').content.decode()
    assert WatchEvent.objects.for_user(alice).count() == 0
    assert 'Mark as watched' in html and 'Watched 2 times' not in html


def test_an_unreleased_movie_is_refused_and_its_control_is_disabled(
    client, portal_headers, alice, today
):
    movie = make_movie('Soon', release_offset=20, status='upcoming')
    now = timezone.now()
    Title.objects.filter(pk=movie.pk).update(
        synced_at=now, next_sync_at=now + timedelta(hours=1)
    )
    response = act(client, portal_headers, f'/titles/{movie.pk}/movie/', watched='1')
    assert response.status_code == 409
    assert not WatchEvent.objects.exists()
    html = open_title(client, portal_headers, movie)
    block = re.search(r'<section[^>]*id="movie-watch".*?</section>', html, re.S).group()
    assert 'disabled' in block and 'aria-disabled="true"' in block
    assert 'Not released yet' in block


def test_the_movie_block_is_on_the_movie_page_only(
    client, portal_headers, alice, movie, synced_show
):
    html = open_title(client, portal_headers, movie)
    block = re.search(r'<section[^>]*id="movie-watch".*?</section>', html, re.S).group()
    assert 'Mark as watched' in block and 'mt-touch' in block
    assert 'aria-pressed="false"' in block
    assert 'id="movie-watch"' not in open_title(client, portal_headers, synced_show)
    assert 'id="episodes"' not in html


def test_movie_and_series_actions_do_not_cross_over(
    client, portal_headers, alice, movie, synced_show
):
    for path in (
        f'/titles/{synced_show.pk}/movie/',
        f'/titles/{movie.pk}/watch-all/',
        f'/titles/{movie.pk}/continue/',
    ):
        assert act(client, portal_headers, path, watched='1').status_code == 404, path
    assert not WatchEvent.objects.exists()


def go_on(client, headers, title, episode=None, **data):
    """POST the continue action with the episode the button showed as next."""
    if episode is not None:
        data['episode'] = getattr(episode, 'pk', episode)
    return act(client, headers, f'/titles/{title.pk}/continue/', **data)


def test_continue_marks_the_next_aired_episode(
    client, portal_headers, alice, synced_show
):
    first, second, third, fourth = mains(synced_show)[:4]
    response = go_on(client, portal_headers, synced_show, first)
    assert response.status_code == 200
    html = response.content.decode()
    assert WatchEvent.objects.for_user(alice).get().episode_id == first.pk
    assert f'id="grp-{first.season_id}"' in html
    assert 'hx-swap-oob="true"' in tag(html, 'title-progress')
    assert 'hx-swap-oob="true"' in tag(html, 'continue-bar')
    assert 'hx-swap-oob="true"' in tag(html, 'library-panel')
    assert 'data-armed="true"' in tag(html, 'mark-up-to-hint')
    assert_unique_ids(html)
    go_on(client, portal_headers, synced_show, second)
    go_on(client, portal_headers, synced_show, third)
    assert WatchEvent.objects.for_user(alice).count() == 3
    # the next one (S1E4) has not aired: refused, nothing changes
    assert go_on(client, portal_headers, synced_show, fourth).status_code == 409
    assert WatchEvent.objects.for_user(alice).count() == 3


def test_continue_is_idempotent(client, portal_headers, alice, synced_show):
    first = mains(synced_show)[0]
    for _ in range(2):
        assert go_on(client, portal_headers, synced_show, first).status_code == 200
    assert WatchEvent.objects.for_user(alice).count() == 1


def test_continue_after_another_device_advanced_writes_nothing(
    client, portal_headers, alice, synced_show
):
    first, second, third = mains(synced_show)[:3]
    track(alice, synced_show, status='watching')
    watch(alice, first, second)  # the page still shows the first as next
    response = go_on(client, portal_headers, synced_show, first)
    assert response.status_code == 200
    html = response.content.decode()
    assert WatchEvent.objects.for_user(alice).count() == 2
    assert f'id="grp-{first.season_id}"' in html
    assert 'hx-swap-oob' not in tag(html, f'grp-{first.season_id}')
    assert 'Continue: S1E3' in html[html.index('id="continue-bar"') :]
    assert 'mark-up-to-hint' not in html  # nothing was marked
    assert_unique_ids(html)


def test_continue_after_an_unwatch_marks_nothing_and_keeps_the_posted_group(
    client, portal_headers, alice
):
    title, first, second = make_two_seasons()
    track(alice, title, status='watching')
    s1e1, s1e2, s2e1, _ = mains(title)
    watch(alice, s1e1, s1e2)  # the page shows s2e1 as next
    watch_episode(client, portal_headers, s1e2, watched='0')  # another device
    response = go_on(client, portal_headers, title, s2e1)
    assert response.status_code == 200
    html = response.content.decode()
    assert WatchEvent.objects.for_user(alice).count() == 1  # no write
    assert f'id="grp-{second.pk}"' in html
    assert 'hx-swap-oob' not in tag(html, f'grp-{second.pk}')
    assert f'id="grp-{first.pk}"' not in html  # the real next group is not sent
    assert 'Continue: S1E2' in html[html.index('id="continue-bar"') :]
    assert_unique_ids(html)


def test_continue_with_nothing_left_is_a_200_without_a_write(
    client, portal_headers, alice
):
    title, first, second = make_two_seasons()
    watch(alice, *mains(title))
    last = mains(title)[-1]
    response = go_on(client, portal_headers, title, last)
    assert response.status_code == 200
    assert WatchEvent.objects.for_user(alice).count() == 4
    assert f'id="grp-{second.pk}"' in response.content.decode()


def test_continue_validates_the_episode(client, portal_headers, alice, synced_show):
    other, _, _ = make_two_seasons()
    foreign = mains(other)[0]
    extra = Episode.objects.get(season__title=synced_show, season__is_extra=True)
    future = future_episode(synced_show)
    assert go_on(client, portal_headers, synced_show).status_code == 400
    for bad in ('', 'x', '1.5', '-1', '１'):
        assert (
            go_on(client, portal_headers, synced_show, episode=bad).status_code == 400
        )
    assert go_on(client, portal_headers, synced_show, foreign).status_code == 404
    assert go_on(client, portal_headers, synced_show, extra).status_code == 404
    assert go_on(client, portal_headers, synced_show, episode=99999).status_code == 404
    assert go_on(client, portal_headers, synced_show, future).status_code == 409
    assert not WatchEvent.objects.exists()


def test_the_continue_button_names_the_episode(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, status='watching')
    first = mains(synced_show)[0]
    html = open_title(client, portal_headers, synced_show)
    button = tag(html, 'continue-btn')
    assert f'hx-vals=\'{{"episode": "{first.pk}"}}\'' in button
    assert f'hx-target="#grp-{first.season_id}"' in button


def test_only_up_to_resends_the_earlier_groups(client, portal_headers, alice):
    title, first, second = make_two_seasons()
    s1e1, s1e2, s2e1, s2e2 = mains(title)
    earlier = f'id="grp-{first.pk}"'
    for path, data in (
        (f'/episodes/{s2e1.pk}/watch/', {'watched': '1'}),
        (f'/episodes/{s2e2.pk}/watch/', {'watched': '1', 'again': '1'}),
        (f'/seasons/{second.pk}/watch/', {'watched': '1'}),
        (f'/seasons/{second.pk}/watch/', {'watched': '0'}),
        (f'/titles/{title.pk}/continue/', {'episode': s1e1.pk}),
    ):
        html = act(client, portal_headers, path, **data).content.decode()
        if 'continue' in path:
            assert 'hx-swap-oob' not in tag(html, f'grp-{first.pk}')  # the posted one
        else:
            assert earlier not in html, path
    up_to = act(client, portal_headers, f'/episodes/{s2e1.pk}/up-to/')
    assert 'hx-swap-oob="true"' in tag(up_to.content.decode(), f'grp-{first.pk}')


def test_unknown_objects_are_404_and_get_is_405(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    season = ep.season
    urls = [
        u.format(ep=ep.pk, season=season.pk, title=synced_show.pk) for u in ACTION_URLS
    ]
    for url in urls:
        assert client.get(url, **portal_headers).status_code == 405, url
        assert client.put(url, **portal_headers).status_code == 405, url
    for url in ACTION_URLS:
        missing = url.format(ep=999999, season=999999, title=999999)
        response = act(client, portal_headers, missing, watched='1')
        assert response.status_code == 404, missing
    assert not WatchEvent.objects.exists()


@pytest.mark.parametrize('bad', [None, '', 'true', '2', '-1', 'yes', '１'])
def test_watched_must_be_one_or_zero(
    client, portal_headers, alice, synced_show, movie, bad
):
    ep = first_episode(synced_show)
    data = {} if bad is None else {'watched': bad}
    for url in (
        f'/episodes/{ep.pk}/watch/',
        f'/seasons/{ep.season_id}/watch/',
        f'/titles/{synced_show.pk}/watch-all/',
        f'/titles/{movie.pk}/movie/',
    ):
        assert client.post(url, data, **portal_headers).status_code == 400, url
    assert not WatchEvent.objects.exists()


def test_actions_require_login_and_csrf(client, alice, synced_show, portal_headers):
    ep = first_episode(synced_show)
    for url in ACTION_URLS:
        path = url.format(ep=ep.pk, season=ep.season_id, title=synced_show.pk)
        assert client.post(path, {'watched': '1'}).status_code == 401, path
    strict = Client(enforce_csrf_checks=True)
    for url in ACTION_URLS:
        path = url.format(ep=ep.pk, season=ep.season_id, title=synced_show.pk)
        response = strict.post(path, {'watched': '1'}, **portal_headers)
        assert response.status_code == 403, path
    assert not WatchEvent.objects.exists()


def test_controls_are_44px_and_labelled(client, portal_headers, alice, synced_show):
    watched, plain = mains(synced_show)[:2]
    watch(alice, watched)
    html = open_title(client, portal_headers, synced_show)
    button = tag(html, f'mark-ep-{watched.pk}')
    assert 'mt-touch' in button and 'type="button"' in button
    assert 'aria-pressed="true"' in button
    assert 'aria-label="Mark as unwatched: S1E1 · Episode 1"' in button
    assert f'hx-post="/episodes/{watched.pk}/watch/"' in button
    assert f'hx-target="#grp-{watched.season_id}"' in button
    assert 'hx-swap="outerHTML"' in button
    button = tag(html, f'mark-ep-{plain.pk}')
    assert 'aria-pressed="false"' in button
    assert 'aria-label="Mark as watched: S1E2 · Episode 2"' in button
    assert '"watched": "1"' in button
    future = future_episode(synced_show)
    button = tag(html, f'mark-ep-{future.pk}')
    assert ' disabled' in button and 'aria-disabled="true"' in button
    assert 'hx-post' not in button
    row = re.search(rf'<li[^>]*id="ep-{future.pk}"[^>]*>', html).group()
    assert 'mt-ep--unaired' in row


def test_the_check_keeps_its_dom_contract(client, portal_headers, alice, synced_show):
    episode = first_episode(synced_show)
    html = open_title(client, portal_headers, synced_show)
    check = re.search(rf'<span[^>]*id="check-ep-{episode.pk}"[^>]*>', html).group()
    assert 'class="mt-check mt-check--unwatched"' in check
    assert 'data-scope="episode"' in check and f'data-id="{episode.pk}"' in check


def test_the_group_header_check_is_mixed_when_partly_watched(
    client, portal_headers, alice
):
    title, first, second = make_two_seasons()
    watch(alice, Episode.objects.get(season=first, number=1))
    html = open_title(client, portal_headers, title)
    button = tag(html, f'mark-grp-{first.pk}')
    assert 'aria-pressed="mixed"' in button
    assert f'hx-post="/seasons/{first.pk}/watch/"' in button
    assert f'hx-target="#grp-{first.pk}"' in button
    assert '"watched": "1"' in button  # a click completes the group
    assert 'aria-label="Mark all aired episodes as watched: Season 1"' in button
    watch(alice, Episode.objects.get(season=first, number=2))
    html = open_title(client, portal_headers, title)
    button = tag(html, f'mark-grp-{first.pk}')
    assert 'aria-pressed="true"' in button and '"watched": "0"' in button
    assert 'Mark all aired episodes as unwatched: Season 1' in button
    assert 'aria-pressed="false"' in tag(html, f'mark-grp-{second.pk}')


def test_a_group_without_aired_episodes_has_a_disabled_check(
    client, portal_headers, alice, today
):
    title = Title.objects.create(
        kind=Title.Kind.TV,
        original_title='Announced',
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    for number in (1, 2):
        season = Season.objects.create(title=title, number=number, kind='tv')
        Episode.objects.create(
            season=season, number=1, air_date=TODAY + timedelta(days=number)
        )
    html = open_title(client, portal_headers, title)
    button = tag(html, f'mark-grp-{season.pk}')
    assert ' disabled' in button and 'aria-disabled="true"' in button


def test_the_whole_show_control(client, portal_headers, alice, synced_show):
    html = open_title(client, portal_headers, synced_show)
    button = tag(html, 'mark-all')
    assert 'aria-pressed="false"' in button and 'mt-touch' in button
    assert f'hx-post="/titles/{synced_show.pk}/watch-all/"' in button
    assert 'hx-target="#episodes"' in button and 'hx-swap="outerHTML"' in button
    assert 'aria-label="Mark whole show as watched"' in button
    watch(alice, mains(synced_show)[0])
    assert 'aria-pressed="mixed"' in tag(
        open_title(client, portal_headers, synced_show), 'mark-all'
    )
    act(client, portal_headers, f'/titles/{synced_show.pk}/watch-all/', watched='1')
    html = open_title(client, portal_headers, synced_show)
    button = tag(html, 'mark-all')
    assert 'aria-pressed="true"' in button
    assert 'aria-label="Mark whole show as unwatched"' in button
    assert '"watched": "0"' in button


def test_the_whole_show_control_is_missing_without_episodes(
    client, portal_headers, alice
):
    title = Title.objects.create(
        kind=Title.Kind.TV,
        original_title='Nothing yet',
        synced_at=timezone.now(),
        next_sync_at=timezone.now() + timedelta(hours=1),
    )
    html = open_title(client, portal_headers, title)
    assert 'mark-all' not in html and 'id="title-progress"' not in html


def test_rows_have_an_overflow_menu(client, portal_headers, alice, synced_show):
    watched, plain = mains(synced_show)[:2]
    watch(alice, watched)
    future = future_episode(synced_show)
    html = open_title(client, portal_headers, synced_show)

    def row(ep) -> str:
        return re.search(rf'<li[^>]*id="ep-{ep.pk}".*?</li>', html, re.S).group()

    done = row(watched)
    assert 'Mark all up to here as watched' in done
    assert f'hx-post="/episodes/{watched.pk}/up-to/"' in done
    assert 'Watched again' in done and '"again": "1"' in done
    assert 'aria-haspopup="menu"' in done and 'role="menu"' in done
    todo = row(plain)
    assert 'Mark all up to here as watched' in todo and 'Watched again' not in todo
    assert '@touchstart' in todo  # long press opens the menu
    soon = row(future)
    assert 'up-to' not in soon and 'Watched again' not in soon
    assert 'role="menu"' not in soon


def test_the_continue_bar_states(client, portal_headers, alice, synced_show):
    track(alice, synced_show, status='watching')
    html = open_title(client, portal_headers, synced_show)
    bar = re.search(r'<div[^>]*id="continue-bar".*?</div>', html, re.S).group()
    first = mains(synced_show)[0]
    assert 'Continue: S1E1 · Episode 1' in bar
    assert f'hx-post="/titles/{synced_show.pk}/continue/"' in bar
    assert f'"episode": "{first.pk}"' in bar
    assert f'hx-target="#grp-{first.season_id}"' in bar
    # a disabled button would lose the keyboard focus, so a second click is dropped
    assert 'hx-sync="this:drop"' in bar and 'hx-disabled-elt' not in bar

    watch(alice, *mains(synced_show)[:3])
    html = open_title(client, portal_headers, synced_show)
    bar = re.search(r'<div[^>]*id="continue-bar".*?</div>', html, re.S).group()
    assert 'Next: S1E4 · airs Oct 26' in bar and 'hx-post' not in bar

    title, first_season, second = make_two_seasons()
    track(alice, title, status='watching')
    watch(alice, *mains(title))
    html = open_title(client, portal_headers, title)
    assert 'You&#x27;re all caught up' in html or "You're all caught up" in html
    assert 'Continue:' not in html


def test_an_undated_next_episode_has_no_date_in_the_bar(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, status='watching')
    watch(alice, *mains(synced_show)[:3])
    Episode.objects.filter(pk=future_episode(synced_show).pk).update(air_date=None)
    html = open_title(client, portal_headers, synced_show)
    assert 'Next: S1E4 · date not announced' in html


def test_names_are_escaped_in_labels_and_responses(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    EpisodeTranslation.objects.create(
        episode=ep, language='en', name='"><script>alert(1)</script>'
    )
    html = open_title(client, portal_headers, synced_show)
    assert '<script>alert' not in html
    html = watch_episode(client, portal_headers, ep).content.decode()
    assert '<script>alert' not in html
    assert '&lt;script&gt;' in html


def test_responses_and_labels_are_translated(
    client, portal_headers, alice, synced_show
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    track(alice, synced_show, status='watching')
    ep = first_episode(synced_show)
    html = open_title(client, portal_headers, synced_show)
    assert 'Отметить просмотренной: S1E1' in html
    assert 'Отметить весь сериал просмотренным' in html
    assert 'Продолжить: S1E1' in html
    html = watch_episode(client, portal_headers, ep).content.decode()
    assert 'Снять отметку о просмотре: S1E1' in html


def test_an_action_runs_a_constant_number_of_queries(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    watch_episode(client, portal_headers, ep, watched='0')  # warm up the profile
    with CaptureQueriesContext(connection) as short:
        watch_episode(client, portal_headers, ep)
    season = Season.objects.filter(title=synced_show, number=1).get()
    Episode.objects.bulk_create(
        Episode(season=season, number=n, air_date=TODAY - timedelta(days=n))
        for n in range(10, 50)
    )
    assert Episode.objects.filter(season=season).count() >= 40
    watch_episode(client, portal_headers, ep, watched='0')
    with CaptureQueriesContext(connection) as long:
        watch_episode(client, portal_headers, ep)
    assert len(long) == len(short)
    assert len(long) <= 30
    for path in (
        f'/seasons/{season.pk}/watch/',
        f'/titles/{synced_show.pk}/watch-all/',
        f'/episodes/{ep.pk}/up-to/',
        f'/titles/{synced_show.pk}/continue/',
    ):
        with CaptureQueriesContext(connection) as ctx:
            act(client, portal_headers, path, watched='1', episode=ep.pk)
        assert len(ctx) <= 30, path


def test_pages_and_responses_never_repeat_a_dom_id(
    client, portal_headers, alice, synced_show, movie
):
    watch(alice, mains(synced_show)[0])
    assert_unique_ids(open_title(client, portal_headers, synced_show))
    assert_unique_ids(open_title(client, portal_headers, movie))
    html = act(
        client, portal_headers, f'/titles/{synced_show.pk}/status/', status='completed'
    ).content.decode()
    # the panel is the main fragment; only the episode section travels out of band
    assert html.count('hx-swap-oob') == 1
    assert 'hx-swap-oob="true"' in tag(html, 'episodes')
    assert_unique_ids(html)


def test_status_completed_refreshes_the_movie_block(
    client, portal_headers, alice, movie
):
    html = act(
        client, portal_headers, f'/titles/{movie.pk}/status/', status='completed'
    ).content.decode()
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert 'hx-swap-oob="true"' in tag(html, 'movie-watch')
    assert 'Watched 1 time' in html
    assert html.count('hx-swap-oob') == 1  # the panel is the main fragment
    assert_unique_ids(html)


def test_status_on_an_unreleased_movie_keeps_the_not_released_state(
    client, portal_headers, alice, today
):
    soon = make_movie('Soon', release_offset=20, status='upcoming')
    now = timezone.now()
    Title.objects.filter(pk=soon.pk).update(
        synced_at=now, next_sync_at=now + timedelta(hours=1)
    )
    html = act(
        client, portal_headers, f'/titles/{soon.pk}/status/', status='completed'
    ).content.decode()
    assert not WatchEvent.objects.exists()
    assert 'Not released yet' in html and 'disabled' in html
    assert 'Watched 1 time' not in html


def test_status_on_a_series_does_not_send_a_movie_block(
    client, portal_headers, alice, synced_show
):
    html = act(
        client, portal_headers, f'/titles/{synced_show.pk}/status/', status='planned'
    ).content.decode()
    assert 'movie-watch' not in html


def test_the_group_and_row_checks_replace_their_older_request(
    client, portal_headers, alice
):
    title, first, second = make_two_seasons()
    watch(alice, mains(title)[0])
    html = open_title(client, portal_headers, title)
    for button_id in (
        f'mark-ep-{mains(title)[1].pk}',
        f'mark-grp-{first.pk}',
    ):
        button = tag(html, button_id)
        assert 'hx-sync="closest section.mt-grp:replace"' in button, button_id
    # the whole-show control is not inside a group: no group sync there
    assert 'hx-sync' not in tag(html, 'mark-all')
    assert html.count('hx-sync="closest section.mt-grp:replace"') >= 8


def test_menu_actions_hand_the_focus_to_the_row_check(
    client, portal_headers, alice, synced_show
):
    ep = first_episode(synced_show)
    watch(alice, ep)
    html = open_title(client, portal_headers, synced_show)
    row = re.search(rf'<li[^>]*id="ep-{ep.pk}".*?</li>', html, re.S).group()
    assert row.count(f"focusAfter = 'mark-ep-{ep.pk}'") == 2  # up to here, again
    root = tag(html, 'episodes') + html[html.index('id="episodes"') :][:2500]
    assert '@htmx:after-settle=' in root
    assert 'focusAfter' in root
    assert '@htmx:response-error=' in root


def test_movie_and_continue_buttons_have_stable_ids(
    client, portal_headers, alice, movie
):
    html = open_title(client, portal_headers, movie)
    assert 'id="movie-watch-toggle"' in html
    watch_url = f'/titles/{movie.pk}/movie/'
    html = act(client, portal_headers, watch_url, watched='1').content.decode()
    assert 'id="movie-watch-toggle"' in html and 'id="movie-watch-again"' in html
    assert 'hx-disabled-elt' not in html and 'hx-sync="this:drop"' in html
