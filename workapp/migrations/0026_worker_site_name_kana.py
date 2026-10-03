from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0025_drop_unused_unit_invoice_payment"),
    ]

    operations = [
        migrations.AddField(
            model_name="site",
            name="name_kana",
            field=models.CharField(
                blank=True,
                default="",
                help_text="カタカナで入力します。ひらがなや半角でも、この読みで検索できます。",
                max_length=100,
                verbose_name="読み仮名",
            ),
        ),
        migrations.AddField(
            model_name="worker",
            name="name_kana",
            field=models.CharField(
                blank=True,
                default="",
                help_text="カタカナで入力します。ひらがなや半角でも、この読みで検索できます。",
                max_length=100,
                verbose_name="読み仮名",
            ),
        ),
    ]
