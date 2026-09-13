from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0013_workrecord_remark"),
    ]

    operations = [
        migrations.AddField(
            model_name="generalcontractor",
            name="closing_day",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="毎月の締め日（1〜31）。未設定なら期間は手入力します。",
                null=True,
                verbose_name="締め日",
            ),
        ),
    ]
