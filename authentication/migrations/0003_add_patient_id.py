import uuid
from django.db import migrations, models


def generate_unique_patient_id(existing_ids):
    while True:
        pid = "SCD-" + uuid.uuid4().hex[:8].upper()
        if pid not in existing_ids:
            return pid


def populate_patient_ids(apps, schema_editor):
    CustomUser = apps.get_model("authentication", "CustomUser")
    existing_ids = set()
    for user in CustomUser.objects.all():
        pid = generate_unique_patient_id(existing_ids)
        existing_ids.add(pid)
        user.patient_id = pid
        user.save(update_fields=["patient_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0002_customuser_genotype"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="patient_id",
            field=models.CharField(blank=True, editable=False, max_length=20, default=""),
        ),
        migrations.RunPython(populate_patient_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customuser",
            name="patient_id",
            field=models.CharField(blank=True, editable=False, max_length=20, unique=True),
        ),
    ]
