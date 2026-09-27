from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0021_worker_company_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="workrecord",
            name="size_mark",
            field=models.CharField(blank=True, default="", max_length=20, verbose_name="印"),
        ),
    ]
