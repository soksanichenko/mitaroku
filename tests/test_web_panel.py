"""The library panel on the title page: focus, "Saved", the Continue bar of a removed
title, and the Account menu keyboard contract."""

import re
from pathlib import Path

import pytest
from tracking.models import LibraryEntry
from tracking_helpers import first_episode, make_undated_show, watch

pytestmark = pytest.mark.django_db

STATIC = Path(__file__).parents[1] / 'sources/web/static/web'


def open_title(client, headers, title) -> str:
    response = client.get(f'/titles/{title.pk}/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def post(client, headers, title, action: str, **data) -> str:
    response = client.post(f'/titles/{title.pk}/{action}/', data, **headers)
    assert response.status_code == 200
    return response.content.decode()


def track(user, title, status: str = 'watching', **fields) -> LibraryEntry:
    return LibraryEntry.objects.create(user=user, title=title, status=status, **fields)


def panel(html: str) -> str:
    return re.search(r'<section id="library-panel".*?</section>', html, re.S).group()


# Focus ---------------------------------------------------------------------


def test_every_control_of_the_panel_says_where_the_focus_goes(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, score=4)
    html = panel(open_title(client, portal_headers, synced_show))
    for number in range(1, 6):
        assert f'id="star-{number}"' in html
        assert f"mtFocusAfter('star-{number}', 'library-heading')" in html
    assert "@change=\"mtFocusAfter('score-select', 'library-heading')\"" in html
    assert "@submit=\"mtFocusAfter('note-save', 'library-heading')\"" in html
    assert 'id="note-save"' in html
    # a status pick: the item is gone with the menu, so the trigger gets the focus
    assert 'id="library-status"' in html
    assert "mtFocusAfter('library-status', 'library-heading')" in html
    # the heading is the fallback and must be focusable
    heading = re.search(r'<h2 id="library-heading"[^>]*>', html).group()
    assert 'tabindex="-1"' in heading


def test_the_panel_ids_the_script_aims_at_survive_every_answer(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show)
    for action, data in (
        ('status', {'status': 'completed'}),
        ('score', {'score': '6'}),
        ('note', {'note': 'hello'}),
    ):
        html = panel(post(client, portal_headers, synced_show, action, **data))
        for element_id in ('library-status', 'note-save', 'score-select', 'star-3'):
            assert f'id="{element_id}"' in html, (action, element_id)
    # removing leaves the trigger (now "Add") and the heading to receive the focus
    html = panel(post(client, portal_headers, synced_show, 'status', status='remove'))
    assert 'id="library-status"' in html and 'id="library-heading"' in html
    assert 'id="star-1"' not in html


def test_the_focus_script_moves_focus_after_the_swap_and_falls_back():
    script = (STATIC / 'focus.js').read_text()
    assert 'window.mtFocusAfter' in script
    assert "'htmx:afterSettle'" in script
    assert 'fallbackId' in script and '.focus()' in script
    # a failed request must not leave a stale target behind
    assert "'htmx:responseError'" in script and 'pending = null' in script


def test_the_shell_loads_the_focus_script(client, portal_headers, alice, synced_show):
    assert '/static/web/focus.js' in open_title(client, portal_headers, synced_show)


def test_the_account_menu_returns_focus_on_escape_and_closes_on_tab_out(
    client, portal_headers, alice, synced_show
):
    html = open_title(client, portal_headers, synced_show)
    user = re.search(r'<div class="mt-user"[^>]*>', html).group()
    assert '@keydown.escape="if (open) { open = false; $refs.btn.focus() }"' in user
    assert (
        '@focusin.window="if (open && !$el.contains($event.target)) open = false"'
        in user
    )
    assert re.search(r'<button[^>]*x-ref="btn"[^>]*class="mt-user-btn"', html)


# "Saved" ---------------------------------------------------------------------


def test_a_change_of_score_note_or_status_says_saved(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show)
    assert 'role="status"' not in panel(open_title(client, portal_headers, synced_show))
    for action, data in (
        ('score', {'score': '8'}),
        ('note', {'note': 'good'}),
        ('status', {'status': 'completed'}),
    ):
        html = panel(post(client, portal_headers, synced_show, action, **data))
        saved = re.search(
            r'<p class="mt-lib-saved" role="status"[^>]*>([^<]*)</p>', html
        )
        assert saved and saved.group(1) == 'Saved', action


def test_saved_is_cleared_by_the_next_edit(client, portal_headers, alice, synced_show):
    track(alice, synced_show)
    html = panel(post(client, portal_headers, synced_show, 'note', note='x'))
    section = re.search(r'<section id="library-panel"[^>]*>', html).group()
    # a constant scope: the swap resets the attributes, so it must not differ per answer
    assert 'x-data="{ saved: true }"' in section
    assert '@input="saved = false"' in section and '@change="saved = false"' in section
    assert 'x-show="saved"' in html


def test_removing_the_title_and_episode_actions_do_not_say_saved(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show)
    html = panel(post(client, portal_headers, synced_show, 'status', status='remove'))
    assert 'mt-lib-saved' not in html
    # the status changes by itself when an episode is watched: not a user's save
    episode = first_episode(synced_show)
    response = client.post(
        f'/episodes/{episode.pk}/watch/', {'watched': '1'}, **portal_headers
    )
    assert 'library-panel' in response.content.decode()
    assert 'mt-lib-saved' not in response.content.decode()


def test_saved_is_translated(client, portal_headers, alice, synced_show):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    track(alice, synced_show)
    html = post(client, portal_headers, synced_show, 'score', score='6')
    assert 'role="status" x-show="saved">Сохранено</p>' in html


# The Continue bar of a title that is not in the library -----------------------


def test_a_removed_title_keeps_its_progress_but_not_its_continue_bar(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show)
    watch(alice, first_episode(synced_show))
    assert 'id="continue-btn"' in open_title(client, portal_headers, synced_show)
    removed = post(client, portal_headers, synced_show, 'status', status='remove')
    assert 'id="continue-btn"' not in removed
    assert 'id="continue-bar"' in removed  # the target stays, empty
    html = open_title(client, portal_headers, synced_show)
    assert 'id="continue-btn"' not in html and 'Continue:' not in html
    assert 'mt-check--watched' in html  # the watched episodes stay (Decision 6)
    assert re.search(r'mt-progress-text">1/5<', html)
    # adding it again brings the bar back
    track(alice, synced_show)
    assert 'id="continue-btn"' in open_title(client, portal_headers, synced_show)


def test_the_all_caught_up_line_needs_an_entry_too(
    client, portal_headers, alice, today
):
    title, mains, _ = make_undated_show()
    watch(alice, *mains)
    html = open_title(client, portal_headers, title)
    assert 'caught up' not in html
    track(alice, title)
    assert 'caught up' in open_title(client, portal_headers, title)


# The metadata language help --------------------------------------------------


def test_the_metadata_language_help_says_titles_are_translated_when_opened(
    client, portal_headers, alice
):
    html = client.get('/settings/', **portal_headers).content.decode()
    help_text = re.search(r'id="id_metadata_language_help"[^>]*>([^<]*)<', html).group(
        1
    )
    assert 'translated when you open it' in help_text
    assert 'may need a moment' in help_text
    interface = re.search(r'id="id_interface_language_help"[^>]*>([^<]*)<', html).group(
        1
    )
    assert 'translated' not in interface


def test_the_metadata_language_help_is_translated(client, portal_headers, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    html = client.get('/settings/', **portal_headers).content.decode()
    help_text = re.search(r'id="id_metadata_language_help"[^>]*>([^<]*)<', html).group(
        1
    )
    assert 'переводится, когда вы его открываете' in help_text
    assert 'translated' not in help_text
