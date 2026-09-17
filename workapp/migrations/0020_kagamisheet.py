from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0019_workrecord_manual_prices"),
    ]

    operations = [
        migrations.CreateModel(
            name="KagamiSheet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope_key", models.CharField(max_length=255, unique=True, verbose_name="対象キー")),
                ("lines", models.JSONField(blank=True, default=list, verbose_name="鑑の行")),
                ("total_amount", models.IntegerField(default=0, verbose_name="鑑の合計")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "鑑",
                "verbose_name_plural": "鑑",
            },
        ),
    ]
