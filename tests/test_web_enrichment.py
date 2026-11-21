"""The enrichment of the title page: tags, See also, cast, trailers, collection."""

import re
from datetime import date
from html import unescape

import pytest
from catalog.models import ExternalId, Title, TitleDetails, TitleTranslation
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from tracking.models import LibraryEntry
from web.views.titles import enrichment

pytestmark = pytest.mark.django_db

KEY = 'dQw4w9WgXcQ'


def page(client, headers, title) -> str:
    """Return the HTML of the title page."""
    response = client.get(f'/titles/{title.pk}/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def details(title: Title, **fields) -> TitleDetails:
    """Give ``title`` a details row with ``fields``."""
    return TitleDetails.objects.create(title=title, **fields)


def trailer(key: str = KEY, name: str = 'Official Trailer', **fields) -> dict:
    """Build a trailer entry."""
    return {
        'key': key,
        'name': name,
        'kind': 'Trailer',
        'language': 'en',
        'official': True,
        **fields,
    }


def person(index: int, photo: str = '') -> dict:
    """Build a cast entry."""
    return {'name': f'Actor {index}', 'role': f'Role {index}', 'photo_url': photo}


def similar_item(external_id: int, name: str, provider: str = 'tmdb_movie') -> dict:
    """Build a similar entry."""
    return {
        'provider': provider,
        'external_id': str(external_id),
        'kind': 'movie',
        'title': name,
        'original_title': f'{name} (orig)',
        'year': 2001,
        'poster_url': f'https://image.tmdb.org/t/p/w185/{external_id}.jpg',
        'rating': 7.4,
    }


def hrefs(html: str, section_id: str) -> list[str]:
    """Return the unescaped ``href`` of every link in the section ``section_id``."""
    start = html.index(f'id="{section_id}"')
    end = html.index('</section>', start)
    return [unescape(h) for h in re.findall(r'href="([^"]+)"', html[start:end])]


def test_header_links_genres_and_countries_into_browse(
    client, portal_headers, alice, synced_show
):
    details(synced_show, genres=['drama', 'sci-fi'], countries=['IS'])
    html = page(client, portal_headers, synced_show)
    assert 'href="/browse/?type=tv&amp;genre=drama"' in html
    assert 'href="/browse/?type=tv&amp;genre=sci-fi"' in html
    assert 'href="/browse/?type=tv&amp;country=IS"' in html
    assert '>Drama</span></a>' in html and '>Iceland</span></a>' in html


def test_header_labels_follow_the_interface_language(
    client, portal_headers, alice, synced_movie
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    details(synced_movie, genres=['drama'], countries=['JP'])
    html = page(client, portal_headers, synced_movie)
    assert 'href="/browse/?type=movie&amp;genre=drama"' in html
    assert '>Драма</span></a>' in html
    assert '>Япония</span></a>' in html


def test_the_new_sections_are_translated(client, portal_headers, alice, synced_movie):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    Title.objects.filter(pk=synced_movie.pk).update(release_date=date(1999, 3, 31))
    details(
        synced_movie,
        genres=['drama'],
        cast=[person(1)],
        trailers=[trailer()],
        similar=[similar_item(5, 'Sim')],
        collection={'id': 9, 'name': 'Col', 'poster_url': ''},
    )
    html = page(client, portal_headers, synced_movie)
    for text in (
        'Смотрите также',
        'В ролях',
        'Трейлеры',
        'aria-label="Смотреть: Official Trailer"',
        'Остановить',
        'Коллекции',
        'Похожие',
    ):
        assert text in html


def test_the_label_filters_follow_the_active_language():
    from web.templatetags.web_extras import country_label, genre_label

    with translation.override('en'):
        assert genre_label('drama') == 'Drama'
        assert country_label('JP') == 'Japan'
        assert genre_label('nope') == 'nope'
    with translation.override('ru'):
        assert genre_label('drama') == 'Драма'
        assert country_label('JP') == 'Япония'


def test_a_country_without_a_browse_filter_is_a_plain_tag(
    client, portal_headers, alice, synced_movie
):
    details(synced_movie, countries=['CZ'])
    html = page(client, portal_headers, synced_movie)
    assert 'Czechia' in html
    assert 'country=CZ' not in html


def test_see_also_links_and_limit(client, portal_headers, alice, synced_movie):
    Title.objects.filter(pk=synced_movie.pk).update(release_date=date(1999, 3, 31))
    details(synced_movie, genres=['sci-fi', 'drama'], countries=['US'])
    html = page(client, portal_headers, synced_movie)
    assert 'See also' in html
    assert hrefs(html, 'see-also') == [
        '/browse/?type=movie&genre=sci-fi',
        '/browse/?type=movie&country=US',
        '/browse/?type=movie&genre=sci-fi&country=US',
        '/browse/?type=movie&genre=sci-fi&year=1999',
        '/browse/?type=movie&genre=drama',
        '/browse/?type=movie&year=1999',
    ]
    start = html.index('id="see-also"')
    section = html[start : html.index('</section>', start)]
    assert 'Movies · Science fiction · United States' in section


def test_see_also_reads_like_the_browse_heading(
    client, portal_headers, alice, synced_show
):
    details(synced_show, genres=['drama'], countries=['IS'])
    html = page(client, portal_headers, synced_show)
    assert 'Shows · Drama · Iceland' in html


def test_see_also_is_absent_with_fewer_than_two_links(
    client, portal_headers, alice, synced_movie
):
    details(synced_movie, genres=['drama'])
    html = page(client, portal_headers, synced_movie)
    assert 'See also' not in html
    assert 'id="see-also"' not in html


def test_cast_shows_eight_and_hides_the_rest_behind_a_toggle(
    client, portal_headers, alice, synced_show
):
    photo = 'https://image.tmdb.org/t/p/w185/a.jpg'
    details(
        synced_show, cast=[person(i, photo if i == 1 else '') for i in range(1, 13)]
    )
    html = page(client, portal_headers, synced_show)
    start = html.index('id="cast"')
    section = html[start : html.index('</section>', start)]
    assert '>Cast<' in section
    items = re.findall(r'<li class="mt-cast-item"[^>]*>', section)
    assert len(items) == 12
    assert sum('x-show="all"' in item for item in items) == 4
    assert 'Actor 12' in section
    assert 'Show all 12' in section
    assert 'aria-expanded' in section
    assert f'src="{photo}"' in section
    assert 'referrerpolicy="no-referrer"' in section and 'loading="lazy"' in section
    assert '>A2<' in section  # initials placeholder when there is no photo


def test_a_short_cast_has_no_toggle(client, portal_headers, alice, synced_show):
    details(synced_show, cast=[person(i) for i in range(1, 4)])
    html = page(client, portal_headers, synced_show)
    assert 'Actor 3' in html
    assert 'Show all' not in html


def test_anime_cast_heading_is_voice_cast(client, portal_headers, alice, synced_show):
    Title.objects.filter(pk=synced_show.pk).update(kind=Title.Kind.ANIME)
    details(synced_show, cast=[person(1)])
    html = page(client, portal_headers, synced_show)
    assert 'Voice cast' in html


def test_trailers_load_nothing_before_the_click(
    client, portal_headers, alice, synced_movie
):
    details(synced_movie, trailers=[trailer(), trailer('abcdef_1234', 'Teaser')])
    html = page(client, portal_headers, synced_movie)
    assert '<iframe' not in html
    assert 'ytimg' not in html
    assert 'youtube.com' not in html.replace('youtube-nocookie.com/embed/', '')
    assert html.count(KEY) == 1 and html.count('abcdef_1234') == 1
    assert f'data-key="{KEY}"' in html
    assert 'aria-label="Play Official Trailer"' in html
    # The player is built by Alpine on click, with exactly these attributes.
    assert 'https://www.youtube-nocookie.com/embed/' in html
    assert "'?autoplay=1'" in html
    assert 'autoplay; encrypted-media; picture-in-picture' in html
    assert 'strict-origin-when-cross-origin' in html
    assert 'allowFullscreen' in html
    assert 'Stop' in html


def test_trailers_are_ordered_by_the_metadata_language_and_capped(
    client, portal_headers, alice, synced_movie
):
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    entries = [trailer(f'key{n:05d}', f'T{n}', language='en') for n in range(1, 10)]
    entries[4] = trailer('key00005', 'Русский', language='ru')
    details(synced_movie, trailers=entries)
    html = page(client, portal_headers, synced_movie)
    keys = re.findall(r'data-key="([^"]+)"', html)
    assert keys[0] == 'key00005'
    assert keys[1:] == [f'key{n:05d}' for n in (1, 2, 3, 4, 6, 7, 8)]


def test_an_anime_trailer_without_a_name_gets_a_generic_one(
    client, portal_headers, alice, synced_show
):
    Title.objects.filter(pk=synced_show.pk).update(kind=Title.Kind.ANIME)
    details(synced_show, trailers=[trailer(name='', language='')])
    html = page(client, portal_headers, synced_show)
    assert 'aria-label="Play Trailer"' in html


@pytest.mark.parametrize(
    'bad', ['short', 'x' * 21, 'abc"def', 'a b c d e', '<b>bold1</b>', 123456, None]
)
def test_a_bad_trailer_key_is_not_rendered(
    client, portal_headers, alice, synced_movie, bad
):
    details(synced_movie, trailers=[trailer(bad, 'Evil One'), trailer()])
    html = page(client, portal_headers, synced_movie)
    assert 'Evil One' not in html
    assert html.count('data-key=') == 1
    assert KEY in html


def test_only_bad_trailer_keys_hide_the_section(
    client, portal_headers, alice, synced_movie
):
    details(synced_movie, trailers=[trailer('bad key!')])
    html = page(client, portal_headers, synced_movie)
    assert 'id="trailers"' not in html


def test_collection_card_links_to_the_collection_page(
    client, portal_headers, alice, synced_movie
):
    details(
        synced_movie,
        collection={
            'id': 2344,
            'name': 'The Matrix Collection',
            'poster_url': 'https://image.tmdb.org/t/p/w185/c.jpg',
        },
    )
    html = page(client, portal_headers, synced_movie)
    assert hrefs(html, 'collections') == ['/collections/2344/']
    assert 'The Matrix Collection' in html
    assert 'https://image.tmdb.org/t/p/w185/c.jpg' in html


def test_no_collection_no_card(client, portal_headers, alice, synced_movie):
    details(synced_movie, genres=['drama'])
    html = page(client, portal_headers, synced_movie)
    assert 'id="collections"' not in html


def test_similar_links_to_known_titles_and_open_for_unknown(
    client, portal_headers, alice, synced_movie
):
    known = Title.objects.create(kind='movie', original_title='Known Film')
    ExternalId.objects.create(title=known, provider='tmdb_movie', external_id='700')
    TitleTranslation.objects.create(title=known, language='en', name='Known Local')
    details(
        synced_movie,
        similar=[
            similar_item(700, 'Provider Name'),
            similar_item(701, 'Unknown Film'),
            {**similar_item(30, 'An Anime', 'anilist'), 'kind': 'anime'},
        ],
    )
    html = page(client, portal_headers, synced_movie)
    assert hrefs(html, 'similar') == [
        f'/titles/{known.pk}/',
        '/open/tmdb_movie/701/',
        '/open/anilist/30/',
    ]
    assert 'Known Local' in html
    assert 'Provider Name' not in html
    assert 'Unknown Film' in html and 'Unknown Film (orig)' in html
    assert 'Known Film' in html  # the original name of a known title
    assert '7.4' in html
    assert 'referrerpolicy="no-referrer"' in html


def test_similar_skips_entries_with_a_bad_id(
    client, portal_headers, alice, synced_movie
):
    bad = {**similar_item(1, 'Odd'), 'external_id': '12x'}
    unknown_provider = {**similar_item(2, 'Other'), 'provider': 'mal'}
    details(synced_movie, similar=[bad, unknown_provider, similar_item(3, 'Fine')])
    html = page(client, portal_headers, synced_movie)
    assert hrefs(html, 'similar') == ['/open/tmdb_movie/3/']


def test_title_page_without_details(client, portal_headers, alice, synced_show):
    assert not TitleDetails.objects.filter(title=synced_show).exists()
    TitleTranslation.objects.create(
        title=synced_show, language='en', name='Plain', overview='A plain show.'
    )
    html = page(client, portal_headers, synced_show)
    assert 'A plain show.' in html
    for heading in ('See also', 'Cast', 'Trailers', 'Collections', 'Similar'):
        assert f'>{heading}<' not in html


def test_empty_details_hide_every_new_section(
    client, portal_headers, alice, synced_movie
):
    details(synced_movie)
    html = page(client, portal_headers, synced_movie)
    for section in ('see-also', 'cast', 'trailers', 'collections', 'similar'):
        assert f'id="{section}"' not in html


def test_sections_come_in_the_stated_order(client, portal_headers, alice, synced_movie):
    Title.objects.filter(pk=synced_movie.pk).update(release_date=date(1999, 3, 31))
    details(
        synced_movie,
        genres=['drama'],
        countries=['US'],
        cast=[person(1)],
        trailers=[trailer()],
        similar=[similar_item(5, 'Sim')],
        collection={'id': 9, 'name': 'Col', 'poster_url': ''},
    )
    html = page(client, portal_headers, synced_movie)
    order = [
        html.index(marker)
        for marker in (
            'id="desc-heading"',
            'id="see-also"',
            'id="cast"',
            'id="movie-watch"',
            'id="trailers"',
            'id="collections"',
            'id="similar"',
        )
    ]
    assert order == sorted(order)


def test_the_syncing_poll_delivers_the_enrichment_with_the_body(
    client, portal_headers, alice, synced_movie
):
    Title.objects.filter(pk=synced_movie.pk).update(synced_at=None)
    details(synced_movie, genres=['drama'], cast=[person(1)])
    response = client.get(
        f'/titles/{synced_movie.pk}/', HTTP_HX_REQUEST='true', **portal_headers
    )
    assert 'Actor 1' not in response.content.decode()  # still fetching
    Title.objects.filter(pk=synced_movie.pk).update(synced_at=timezone.now())
    response = client.get(
        f'/titles/{synced_movie.pk}/', HTTP_HX_REQUEST='true', **portal_headers
    )
    html = response.content.decode()
    assert 'id="title-body"' in html and 'Actor 1' in html


def test_title_page_query_count_is_constant(
    client, portal_headers, alice, synced_movie
):
    known = [
        Title.objects.create(kind='movie', original_title=f'Known {n}')
        for n in range(6)
    ]
    for n, title in enumerate(known):
        ExternalId.objects.create(
            title=title, provider='tmdb_movie', external_id=str(900 + n)
        )
        TitleTranslation.objects.create(title=title, language='en', name=f'Name {n}')
    page(client, portal_headers, synced_movie)  # warm up the profile

    def count(items: list[dict]) -> int:
        TitleDetails.objects.update_or_create(
            title=synced_movie, defaults={'similar': items}
        )
        with CaptureQueriesContext(connection) as queries:
            page(client, portal_headers, synced_movie)
        return len(queries)

    few = count([similar_item(900 + n, 'S') for n in range(2)])
    many = count(
        [similar_item(900 + n, 'S') for n in range(6)]
        + [similar_item(800 + n, 'U') for n in range(6)]
    )
    assert many == few


def test_enrichment_does_not_leak_between_users(
    client, portal_headers, alice, bob, synced_show
):
    LibraryEntry.objects.create(
        user=bob, title=synced_show, status='watching', note='bob secret', score=9
    )
    details(synced_show, genres=['drama'], cast=[person(1)])
    html = page(client, portal_headers, synced_show)
    assert 'Actor 1' in html
    assert 'bob secret' not in html
    LibraryEntry.objects.create(user=alice, title=synced_show, note='alice note')
    assert 'alice note' in page(client, portal_headers, synced_show)


def test_enrichment_returns_plain_data(synced_movie):
    empty = enrichment(synced_movie, 'en')
    assert empty['details'] is None
    assert empty['genre_links'] == [] and empty['see_also'] == []
    assert empty['collection'] is None and empty['similar'] == []
    details(synced_movie, genres=['drama'], cast=[person(i) for i in range(10)])
    full = enrichment(synced_movie, 'en')
    assert full['details'].genres == ['drama']
    assert len(full['cast']) == 10 and full['cast_more'] == 2
