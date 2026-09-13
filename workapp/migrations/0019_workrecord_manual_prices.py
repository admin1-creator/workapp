from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0018_worker_use_common_rate"),
    ]

    operations = [
        migrations.AddField(
            model_name="workrecord",
            name="price_mode",
            field=models.CharField(
                blank=True,
                choices=[("master", "マスタ単価"), ("manual", "手入力")],
                default="master",
                max_length=10,
                verbose_name="単価区分",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="manual_billing",
            field=models.IntegerField(blank=True, null=True, verbose_name="手入力・請求額"),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="manual_pay",
            field=models.IntegerField(blank=True, null=True, verbose_name="手入力・支払額"),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="manual_ouen",
            field=models.IntegerField(blank=True, null=True, verbose_name="手入力・応援額"),
        ),
    ]
