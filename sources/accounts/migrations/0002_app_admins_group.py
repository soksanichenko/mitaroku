"""Create the ``app-admins`` group that portal admins join."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

GROUP_NAME = 'app-admins'


def create_group(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Create the group if it does not exist."""
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Remove the group on rollback."""
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [migrations.RunPython(create_group, delete_group)]
