"""Admin registrations: the catalog is read-only for the admins' group."""

from catalog.models import Episode, ExternalId, Season, Title
from django.contrib import admin


class ExternalIdInline(admin.TabularInline):
    model = ExternalId
    extra = 0


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    """Titles with their provider ids."""

    list_display = ('original_title', 'kind', 'status', 'synced_at')
    list_filter = ('kind', 'status')
    search_fields = ('original_title', 'external_ids__external_id')
    inlines = [ExternalIdInline]


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    """Seasons and anime franchise entries."""

    list_display = ('title', 'number', 'kind', 'name', 'is_extra', 'episode_source')
    list_filter = ('kind', 'is_extra', 'episode_source')
    search_fields = ('title__original_title', 'name')


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    """Episodes."""

    list_display = ('season', 'number', 'air_date')
    search_fields = ('season__title__original_title',)
