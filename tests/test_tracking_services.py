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


def test_again_adds_a_rewatch_and_unwatch_removes_the_latest(alice):
    title, eps, _ = make_show()
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    services.set_episode_watched(alice, eps[0], True, again=True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).count() == 2
    services.set_episode_watched(alice, eps[0], False)
    assert WatchEvent.objects.for_user(alice).count() == 1


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
