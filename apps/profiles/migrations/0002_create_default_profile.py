from django.db import migrations


def create_default_profile(apps, schema_editor):
    Profile = apps.get_model('profiles', 'Profile')
    if not Profile.objects.filter(is_default=True).exists():
        Profile.objects.create(name='Default', color='#728668', is_default=True)


class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_profile, migrations.RunPython.noop),
    ]
