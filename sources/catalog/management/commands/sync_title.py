from catalog.sync import default_clients, get_or_create_title, sync_title
from django.core.management.base import BaseCommand, CommandError
from providers.http import ProviderError
from providers.types import SearchHit

KIND_BY_PROVIDER = {'tmdb_movie': 'movie', 'tmdb_tv': 'tv', 'anilist': 'anime'}


class Command(BaseCommand):
    help = (
        'Add a title by provider id and sync it now, for example: '
        'sync_title anilist 154587, sync_title tmdb_tv 209867, sync_title tmdb_movie 603.'
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument('provider', choices=sorted(KIND_BY_PROVIDER))
        parser.add_argument('external_id', type=int)

    def handle(self, *args, provider: str, external_id: int, **options) -> None:
        hit = SearchHit(
            provider=provider,
            external_id=str(external_id),
            kind=KIND_BY_PROVIDER[provider],
            title=f'{provider}:{external_id}',
            original_title=f'{provider}:{external_id}',
            year=None,
            poster_url='',
            overview='',
        )
        # One set of clients per run: each one opens a connection that is not closed.
        clients = default_clients()
        created = False
        try:
            title, created = get_or_create_title(hit, clients)
            sync_title(title, clients)
        except ProviderError as error:
            if created:
                # Do not leave a placeholder that no provider has ever described.
                title.delete()
            raise CommandError(str(error)) from error
        title.refresh_from_db()
        seasons = title.seasons.count()
        episodes = sum(season.episodes.count() for season in title.seasons.all())
        self.stdout.write(
            self.style.SUCCESS(
                f'{"Added" if created else "Synced"} {title.original_title} '
                f'({title.kind}, {title.status}): {seasons} seasons, {episodes} episodes.'
            )
        )
