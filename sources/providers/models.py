"""Provider data that lives in the database."""

from django.db import models


class AnimeMapping(models.Model):
    """One row of the Fribb/anime-lists dataset, keyed by AniList id.

    The dataset bridges AniList to TMDB and IMDb ids and gives the TMDB season
    and episode offset an entry occupies. It has known errors, so the sync only
    trusts a mapping after the episode guard accepts it.
    """

    anilist_id = models.PositiveIntegerField(primary_key=True)
    mal_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    entry_type = models.CharField(max_length=8, blank=True)
    tmdb_tv_id = models.PositiveIntegerField(null=True, blank=True)
    tmdb_movie_id = models.PositiveIntegerField(null=True, blank=True)
    imdb_id = models.CharField(max_length=16, blank=True)
    tmdb_season = models.PositiveSmallIntegerField(null=True, blank=True)
    tmdb_episode_offset = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f'anilist:{self.anilist_id}'
