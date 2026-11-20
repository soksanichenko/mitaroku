"""Give ``app-admins`` read access to title details and resync synced titles.

Details are filled by the sync, so the group may look but not edit. Titles that
were synced before ``TitleDetails`` existed have none, so they are marked due and
the scheduled sync fills the details gradually.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Model
from django.utils import timezone

GROUP_NAME = 'app-admins'


def _permission(apps: Apps) -> Model:
    """Return the view permission, creating it on a fresh database."""
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    content_type, _ = ContentType.objects.get_or_create(
        app_label='catalog', model='titledetails'
    )
    permission, _ = Permission.objects.get_or_create(
        content_type=content_type,
        codename='view_titledetails',
        defaults={'name': 'Can view title details'},
    )
    return permission


def grant(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Add the permission to the group."""
    Group = apps.get_model('auth', 'Group')
    group = Group.objects.get(name=GROUP_NAME)
    group.permissions.add(_permission(apps))


def revoke(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Remove the permission from the group on rollback."""
    Group = apps.get_model('auth', 'Group')
    group = Group.objects.get(name=GROUP_NAME)
    group.permissions.remove(_permission(apps))


def resync(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Mark every synced title as due now (20 titles are synced per run)."""
    Title = apps.get_model('catalog', 'Title')
    Title.objects.filter(synced_at__isnull=False).update(next_sync_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0003_titledetails'),
        ('accounts', '0003_app_admins_permissions'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
        migrations.RunPython(resync, migrations.RunPython.noop),
    ]
