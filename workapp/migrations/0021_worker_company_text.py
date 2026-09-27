from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0020_kagamisheet"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="worker",
            name="company",
        ),
        migrations.AddField(
            model_name="worker",
            name="company",
            field=models.CharField(
                blank=True,
                default="",
                help_text="基本は空欄（自社）です。専属のひとり親方のときだけ、本人名や屋号を入力します。応援の会社名は入れません。",
                max_length=100,
                verbose_name="所属企業",
            ),
        ),
    ]
