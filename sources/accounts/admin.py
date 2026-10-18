"""Admin registrations."""

from accounts.models import Profile, User
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin


@admin.register(User)
class MitarokuUserAdmin(UserAdmin):
    """User admin with the portal fields visible."""

    fieldsets = UserAdmin.fieldsets + (
        ('Portal', {'fields': ('discord_id', 'avatar_url')}),
    )
    list_display = ('username', 'email', 'discord_id', 'is_staff')


admin.site.register(Profile)
