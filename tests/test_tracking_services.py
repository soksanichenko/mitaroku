import pytest
from tracking import queries, services
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import TODAY, make_movie, make_show

pytestmark = pytest.mark.django_db


def test_marking_twice_with_the_same_state_adds_one_event(alice):
    title, eps, _ = make_show()
    for _ in range(2):
        services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert LibraryEntry.objects.for_user(alice).get(title=title).status == 'watching'


def test_unaired_and_undated_episodes_are_refused(alice):
    title, eps, _ = make_show()
    for episode in (eps[3], eps[4]):
        with pytest.raises(services.NotAired):
            services.set_episode_watched(alice, episode, True, today=TODAY)
    assert not WatchEvent.objects.exists() and not LibraryEntry.objects.exists()


def test_again_adds_a_rewatch_and_unwatch_removes_every_viewing(alice):
    title, eps, _ = make_show()
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    services.set_episode_watched(alice, eps[0], True, again=True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).count() == 2
    for _ in range(2):
        services.set_episode_watched(alice, eps[0], False)
        assert not WatchEvent.objects.for_user(alice).exists()


def test_mark_up_to_skips_unaired_and_other_users(alice, bob):
    title, eps, _ = make_show()
    assert services.mark_up_to(alice, eps[2], today=TODAY) == 3
    assert services.mark_up_to(alice, eps[2], today=TODAY) == 0
    assert not WatchEvent.objects.for_user(bob).exists()


def test_up_to_inside_an_extra_stays_in_its_season(alice):
    title, eps, extra = make_show()
    assert services.mark_up_to(alice, extra, today=TODAY) == 1
    assert queries.progress(alice, title).watched == 0


def test_last_episode_of_an_ended_show_completes_it(alice):
    title, eps, _ = make_show(status='ended', dated=(-30, -23, -16))
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'
    services.mark_up_to(alice, eps[2], today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'


@pytest.mark.parametrize('before', ['planned', 'dropped'])
def test_watching_a_planned_or_dropped_title_resumes_it(alice, before):
    title, eps, _ = make_show()
    services.set_status(alice, title, before, today=TODAY)
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'


def test_completed_status_marks_every_aired_main_episode(alice):
    title, eps, extra = make_show()
    services.set_status(alice, title, 'completed', today=TODAY)
    assert queries.watched_episode_ids(alice, title) == {
        eps[0].pk,
        eps[1].pk,
        eps[2].pk,
    }
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'


def test_removing_from_the_library_keeps_the_events(alice):
    title, eps, _ = make_show()
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert services.set_status(alice, title, None) is None
    assert not LibraryEntry.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(alice).count() == 1


def test_set_season_watched_false_deletes_only_this_users_events(alice, bob):
    title, eps, _ = make_show()
    season = eps[0].season
    for user in (alice, bob):
        services.set_season_watched(user, season, True, today=TODAY)
    assert services.set_season_watched(alice, season, False) == 3
    assert not WatchEvent.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(bob).count() == 3


def test_score_and_note_validation(alice):
    title, _, _ = make_show()
    with pytest.raises(services.NotInLibrary):
        services.set_score(alice, title, 5)
    services.set_status(alice, title, 'planned')
    for bad in (11, -1):
        with pytest.raises(ValueError):
            services.set_score(alice, title, bad)
    with pytest.raises(ValueError):
        services.set_note(alice, title, 'x' * 5001)
    assert services.set_score(alice, title, 7).score == 7


def test_movie_watched_completes_and_rewatch_counts(alice):
    future = make_movie(release_offset=5)
    with pytest.raises(services.NotAired):
        services.set_movie_watched(alice, future, True, today=TODAY)
    movie = make_movie(release_offset=-5)
    services.set_movie_watched(alice, movie, True, today=TODAY)
    services.set_movie_watched(alice, movie, True, again=True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).filter(title=movie).count() == 2
    assert LibraryEntry.objects.for_user(alice).get(title=movie).status == 'completed'


def test_mark_next_marks_the_first_unwatched_aired_episode(alice):
    title, eps, _ = make_show()
    assert services.mark_next(alice, title, today=TODAY) == eps[0]
    assert services.mark_next(alice, title, today=TODAY) == eps[1]
    services.mark_up_to(alice, eps[2], today=TODAY)
    assert services.mark_next(alice, title, today=TODAY) is None


def test_mark_next_stops_at_an_undated_next_episode(alice):
    title, eps, _ = make_show(dated=(-30, None, -16))
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert services.mark_next(alice, title, today=TODAY) is None
    assert queries.watched_episode_ids(alice, title) == {eps[0].pk}


def test_mark_next_leaves_no_entry_when_it_marks_nothing(alice):
    title, eps, _ = make_show(dated=(7, -16))
    assert services.mark_next(alice, title, today=TODAY) is None
    assert not LibraryEntry.objects.exists() and not WatchEvent.objects.exists()


def test_mark_episodes_refuses_an_episode_of_another_title(alice):
    title, _, _ = make_show()
    _, other_eps, _ = make_show('Other')
    for episode in (other_eps[0], other_eps[3]):
        with pytest.raises(ValueError):
            services.mark_episodes(alice, title, [episode], today=TODAY)
    assert not WatchEvent.objects.exists() and not LibraryEntry.objects.exists()


def test_a_refused_watch_leaves_no_entry_and_no_event(alice):
    movie = make_movie(release_offset=5)
    with pytest.raises(services.NotAired):
        services.set_movie_watched(alice, movie, True, today=TODAY)
    with pytest.raises(services.NotAired):
        services.set_title_watched(alice, movie, True, today=TODAY)
    assert not WatchEvent.objects.exists() and not LibraryEntry.objects.exists()


def test_rechecking_a_watched_episode_of_a_removed_title_creates_no_entry(alice):
    title, eps, _ = make_show()
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    services.set_status(alice, title, None)
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert services.mark_episodes(alice, title, eps[:1], today=TODAY) == 0
    services.mark_up_to(alice, eps[0], today=TODAY)
    assert not LibraryEntry.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(alice).count() == 1


def test_a_recheck_that_adds_nothing_leaves_the_entry_alone(alice):
    title, eps, _ = make_show()
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    services.set_status(alice, title, 'planned')
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    services.mark_up_to(alice, eps[0], today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'planned'


def test_rechecking_a_watched_movie_of_a_removed_title_creates_no_entry(alice):
    movie = make_movie()
    services.set_movie_watched(alice, movie, True, today=TODAY)
    services.set_status(alice, movie, None)
    services.set_movie_watched(alice, movie, True, today=TODAY)
    assert not LibraryEntry.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(alice).count() == 1


def test_an_airing_show_with_every_aired_episode_watched_stays_watching(alice):
    title, eps, _ = make_show(status='airing')
    services.mark_up_to(alice, eps[2], today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'


def test_a_completed_entry_stays_completed_after_a_rewatch(alice):
    title, eps, _ = make_show(status='ended', dated=(-30, -23, -16))
    services.set_status(alice, title, 'completed', today=TODAY)
    services.set_episode_watched(alice, eps[0], True, again=True, today=TODAY)
    services.mark_next(alice, title, today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'


def test_completed_on_a_released_movie_adds_exactly_one_viewing(alice):
    movie = make_movie(release_offset=-5)
    for _ in range(2):
        services.set_status(alice, movie, 'completed', today=TODAY)
    assert WatchEvent.objects.for_user(alice).filter(title=movie).count() == 1


def test_completed_on_an_unreleased_movie_sets_the_status_only(alice):
    movie = make_movie(release_offset=5)
    entry = services.set_status(alice, movie, 'completed', today=TODAY)
    assert entry.status == 'completed'
    assert not WatchEvent.objects.exists()


def test_set_title_watched_marks_aired_episodes_of_every_season(alice):
    title, eps, extra = make_show()
    assert services.set_title_watched(alice, title, True, today=TODAY) == 4
    assert services.set_title_watched(alice, title, True, today=TODAY) == 0
    assert queries.watched_episode_ids(alice, title) == {
        eps[0].pk,
        eps[1].pk,
        eps[2].pk,
        extra.pk,
    }
    assert services.set_title_watched(alice, title, False) == 4
    assert not WatchEvent.objects.exists()


def test_set_title_watched_for_a_movie_returns_one_or_zero(alice):
    movie = make_movie(release_offset=-5)
    results = [
        services.set_title_watched(alice, movie, True, today=TODAY),
        services.set_title_watched(alice, movie, True, today=TODAY),
        services.set_title_watched(alice, movie, False),
        services.set_title_watched(alice, movie, False),
    ]
    assert results == [1, 0, 1, 0]


def test_unwatching_a_movie_deletes_every_viewing_and_keeps_the_status(alice):
    movie = make_movie()
    services.set_movie_watched(alice, movie, True, today=TODAY)
    services.set_movie_watched(alice, movie, True, again=True, today=TODAY)
    for _ in range(2):
        services.set_movie_watched(alice, movie, False)
        assert not WatchEvent.objects.exists()
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'


# Another user acts first in the tests below, so an unscoped query would change
# alice's result.


def test_other_users_viewings_do_not_affect_an_episode_watch(alice, bob):
    title, eps, _ = make_show()
    services.set_episode_watched(bob, eps[0], True, today=TODAY)
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'
    services.set_episode_watched(alice, eps[0], False)
    assert not WatchEvent.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(bob).count() == 1


def test_other_users_viewings_do_not_affect_mark_up_to_and_mark_episodes(alice, bob):
    title, eps, _ = make_show()
    services.mark_up_to(bob, eps[2], today=TODAY)
    assert services.mark_up_to(alice, eps[2], today=TODAY) == 3
    other, other_eps, _ = make_show('Other')
    services.mark_episodes(bob, other, other_eps[:3], today=TODAY)
    assert services.mark_episodes(alice, other, other_eps[:3], today=TODAY) == 3
    assert WatchEvent.objects.for_user(alice).count() == 6
    assert WatchEvent.objects.for_user(bob).count() == 6


def test_other_users_viewings_do_not_affect_season_and_title_actions(alice, bob):
    title, eps, extra = make_show()
    season = eps[0].season
    services.set_season_watched(bob, season, True, today=TODAY)
    assert services.set_season_watched(alice, season, True, today=TODAY) == 3
    services.set_title_watched(bob, title, True, today=TODAY)
    assert services.set_title_watched(alice, title, True, today=TODAY) == 1
    assert services.set_title_watched(alice, title, False) == 4
    assert not WatchEvent.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(bob).count() == 4


def test_other_users_viewings_do_not_affect_movie_actions(alice, bob):
    movie = make_movie()
    services.set_movie_watched(bob, movie, True, today=TODAY)
    services.set_movie_watched(alice, movie, True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'
    services.set_movie_watched(alice, movie, False)
    assert not WatchEvent.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(bob).count() == 1


def test_removing_an_entry_keeps_the_other_users_entry(alice, bob):
    title, _, _ = make_show()
    services.set_status(bob, title, 'planned')
    services.set_status(alice, title, 'planned')
    assert services.set_status(alice, title, None) is None
    assert not LibraryEntry.objects.for_user(alice).exists()
    assert LibraryEntry.objects.for_user(bob).get().status == 'planned'


def test_score_and_note_need_the_users_own_entry(alice, bob):
    title, _, _ = make_show()
    services.set_status(bob, title, 'planned')
    with pytest.raises(services.NotInLibrary):
        services.set_score(alice, title, 5)
    with pytest.raises(services.NotInLibrary):
        services.set_note(alice, title, 'x')
    assert LibraryEntry.objects.for_user(bob).get().score == 0


def test_other_users_viewings_do_not_affect_mark_next(alice, bob):
    title, eps, _ = make_show()
    assert services.mark_next(bob, title, today=TODAY) == eps[0]
    assert services.mark_next(alice, title, today=TODAY) == eps[0]
    assert WatchEvent.objects.for_user(alice).count() == 1
