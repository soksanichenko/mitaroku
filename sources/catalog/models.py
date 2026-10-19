"""Catalog models: titles, their external ids, seasons, episodes and translations.

The catalog is shared by all users and filled by the providers; nothing here
belongs to a single user (library entries and watch events arrive with tracking).
"""

from django.db import models
from django.db.models import Q


class Title(models.Model):
    """A movie, a TV series or an anime franchise.

    An anime ``Title`` is a whole franchise: its seasons are the AniList entries
    (TV cours, ONAs, OVAs, movies and specials). A movie has no seasons.
    """

    class Kind(models.TextChoices):
        MOVIE = 'movie'
        TV = 'tv'
        ANIME = 'anime'

    class Status(models.TextChoices):
        UPCOMING = 'upcoming'
        AIRING = 'airing'
        ENDED = 'ended'
        CANCELED = 'canceled'
        UNKNOWN = 'unknown'

    kind = models.CharField(max_length=8, choices=Kind)
    original_title = models.CharField(max_length=300)
    original_language = models.CharField(max_length=8, blank=True)
    release_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.UNKNOWN)
    runtime_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    poster_url = models.URLField(max_length=500, blank=True)
    backdrop_url = models.URLField(max_length=500, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    rating_votes = models.PositiveIntegerField(null=True, blank=True)
    rating_source = models.CharField(max_length=16, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.original_title


class ExternalId(models.Model):
    """An id of a title at a provider.

    TMDB movie and TV ids share a number space, so they are separate providers.
    An anime franchise carries the AniList id of every one of its entries, which
    is how adding any entry finds the existing franchise.
    """

    class Provider(models.TextChoices):
        TMDB_MOVIE = 'tmdb_movie'
        TMDB_TV = 'tmdb_tv'
        ANILIST = 'anilist'
        MAL = 'mal'
        IMDB = 'imdb'

    title = models.ForeignKey(
        Title, on_delete=models.CASCADE, related_name='external_ids'
    )
    provider = models.CharField(max_length=12, choices=Provider)
    external_id = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'external_id'], name='unique_external_id'
            )
        ]

    def __str__(self) -> str:
        return f'{self.provider}:{self.external_id}'


class Season(models.Model):
    """A season of a TV title, or one entry of an anime franchise.

    ``number`` orders the seasons inside the title (it is briefly negative while
    a sync renumbers them). ``episode_source`` records where the episodes came
    from: ``tmdb`` (accepted by the guard), ``anilist`` (degraded mode: counts
    and some dates only, no titles) or ``none``.
    """

    class Kind(models.TextChoices):
        TV = 'tv'
        ONA = 'ona'
        OVA = 'ova'
        MOVIE = 'movie'
        SPECIAL = 'special'

    class EpisodeSource(models.TextChoices):
        TMDB = 'tmdb'
        ANILIST = 'anilist'
        NONE = 'none'

    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name='seasons')
    number = models.SmallIntegerField()
    kind = models.CharField(max_length=8, choices=Kind)
    name = models.CharField(max_length=300, blank=True)
    is_extra = models.BooleanField(default=False)
    anilist_id = models.PositiveIntegerField(null=True, blank=True)
    tmdb_tv_id = models.PositiveIntegerField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    episode_count = models.PositiveIntegerField(null=True, blank=True)
    episode_source = models.CharField(
        max_length=8, choices=EpisodeSource, default=EpisodeSource.NONE
    )

    class Meta:
        ordering = ['number']
        constraints = [
            models.UniqueConstraint(
                fields=['title', 'number'], name='unique_season_number'
            ),
            models.UniqueConstraint(
                fields=['title', 'anilist_id'],
                condition=Q(anilist_id__isnull=False),
                name='unique_season_anilist_id',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} / {self.name or self.number}'


class Episode(models.Model):
    """One episode.

    ``absolute_number`` counts across the main seasons of a title. When the
    episode came from TMDB, ``tmdb_season_number`` and ``tmdb_episode_number``
    say which TMDB episode it is, so other languages can be fetched later.
    """

    season = models.ForeignKey(
        Season, on_delete=models.CASCADE, related_name='episodes'
    )
    number = models.PositiveIntegerField()
    absolute_number = models.PositiveIntegerField(null=True, blank=True)
    tmdb_season_number = models.PositiveSmallIntegerField(null=True, blank=True)
    tmdb_episode_number = models.PositiveIntegerField(null=True, blank=True)
    air_date = models.DateField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['number']
        constraints = [
            models.UniqueConstraint(
                fields=['season', 'number'], name='unique_episode_number'
            )
        ]

    def __str__(self) -> str:
        return f'{self.season} #{self.number}'


class TitleTranslation(models.Model):
    """Lazily filled name and overview of a title in one language.

    Search results are never stored here, and a row with neither a name nor an
    overview is never written.
    """

    title = models.ForeignKey(
        Title, on_delete=models.CASCADE, related_name='translations'
    )
    language = models.CharField(max_length=8)
    name = models.CharField(max_length=300, blank=True)
    overview = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['title', 'language'], name='unique_title_translation'
            ),
            models.CheckConstraint(
                condition=~Q(name='') | ~Q(overview=''),
                name='title_translation_not_empty',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} [{self.language}]'


class EpisodeTranslation(models.Model):
    """Lazily filled name and overview of an episode in one language."""

    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name='translations'
    )
    language = models.CharField(max_length=8)
    name = models.CharField(max_length=300, blank=True)
    overview = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['episode', 'language'], name='unique_episode_translation'
            ),
            models.CheckConstraint(
                condition=~Q(name='') | ~Q(overview=''),
                name='episode_translation_not_empty',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.episode} [{self.language}]'
