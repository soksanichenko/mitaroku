"""Create the ``app-admins`` group that portal admins join."""

from django.db import migrations

GROUP_NAME = 'app-admins'


def create_group(apps, schema_editor):
    """Create the group if it does not exist."""
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps, schema_editor):
    """Remove the group on rollback."""
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [migrations.RunPython(create_group, delete_group)]
