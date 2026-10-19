from django.core.management.base import BaseCommand
from providers.tasks import refresh_mapping_now


class Command(BaseCommand):
    help = 'Download the Fribb/anime-lists dataset and load it into the database.'

    def handle(self, *args, **options) -> None:
        count = refresh_mapping_now()
        self.stdout.write(self.style.SUCCESS(f'Loaded {count} anime mappings.'))
