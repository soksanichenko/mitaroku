"""In-memory stand-ins for the provider clients, and data builders."""

from providers.anilist import AniListEdge, AniListMedia


def edge(relation, anilist_id, fmt='TV', media_type='ANIME'):
    return AniListEdge(relation, anilist_id, None, fmt, media_type)


def media(
    anilist_id,
    edges=(),
    fmt='TV',
    year=2020,
    month=1,
    day=1,
    episodes=12,
    status='FINISHED',
    score=80,
    mal_id=None,
    english='',
):
    return AniListMedia(
        anilist_id=anilist_id,
        mal_id=mal_id,
        format=fmt,
        status=status,
        episodes=episodes,
        average_score=score,
        romaji=f'n{anilist_id}',
        english=english,
        native='',
        cover_url=f'https://img.example/{anilist_id}.jpg',
        banner_url='',
        start=(year, month, day),
        edges=list(edges),
    )
