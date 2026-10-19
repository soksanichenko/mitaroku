"""Give the ``app-admins`` group read access to the catalog.

The catalog is filled by the providers, so the group may look but not edit.
"""

from collections.abc import Iterator

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Model

GROUP_NAME = 'app-admins'
MODELS = [
    'title',
    'externalid',
    'season',
    'episode',
    'titletranslation',
    'episodetranslation',
]


def _permissions(apps: Apps) -> Iterator[Model]:
    """Return the view permissions, creating missing ones on a fresh database."""
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    for model in MODELS:
        content_type, _ = ContentType.objects.get_or_create(
            app_label='catalog', model=model
        )
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=f'view_{model}',
            defaults={'name': f'Can view {model}'},
        )
        yield permission


def grant(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Add the permissions to the group."""
    Group = apps.get_model('auth', 'Group')
    group = Group.objects.get(name=GROUP_NAME)
    group.permissions.add(*_permissions(apps))


def revoke(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Remove the permissions from the group on rollback."""
    Group = apps.get_model('auth', 'Group')
    group = Group.objects.get(name=GROUP_NAME)
    group.permissions.remove(*_permissions(apps))


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0001_initial'),
        ('accounts', '0003_app_admins_permissions'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [migrations.RunPython(grant, revoke)]
