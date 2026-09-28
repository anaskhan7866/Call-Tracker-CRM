from django.conf import settings
from django.db import migrations


def assign_unowned_contacts(apps, schema_editor):
    Contact = apps.get_model('leads', 'Contact')
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split('.')
    User = apps.get_model(user_app_label, user_model_name)
    database = schema_editor.connection.alias

    telecallers = list(
        User.objects.using(database)
        .filter(is_staff=False, is_superuser=False)
        .order_by('id')[:2]
    )
    if len(telecallers) == 1:
        Contact.objects.using(database).filter(user__isnull=True).update(
            user_id=telecallers[0].pk
        )


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0007_contact_last_updated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(assign_unowned_contacts, migrations.RunPython.noop),
    ]