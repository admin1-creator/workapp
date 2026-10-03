from django.db import migrations, models


def copy_craftsman_and_size(apps, schema_editor):
    WorkRecord = apps.get_model("workapp", "WorkRecord")
    grouped = {}
    for record in WorkRecord.objects.all():
        grouped.setdefault((record.voucher_no, str(record.date), record.site), []).append(record)
    for group in grouped.values():
        craftsman = ""
        for record in group:
            if (record.party_kind or "").strip() in ("職人", "shokunin"):
                craftsman = (record.helpers or "").split("、")[0].strip()
                break
        if not craftsman:
            for record in group:
                if (record.party_kind or "").strip() in ("元請", "moto", "応援", "ouen"):
                    craftsman = (record.helpers or "").split("、")[0].strip()
                    break
        for record in group:
            kind = (record.party_kind or "").strip()
            size = (record.work_size or "").strip() or (record.dimension or "").strip()
            record.work_size = size
            if kind in ("手元", "temoto"):
                record.craftsman = craftsman
            else:
                record.craftsman = craftsman or (record.helpers or "").split("、")[0].strip()
                if (record.helpers or "").strip() == (record.craftsman or "").strip():
                    record.helpers = ""
            record.save(update_fields=["work_size", "craftsman", "helpers"])


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0023_rate_unique_and_workrecord_search_index"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField(
                    model_name="workrecord",
                    old_name="worker",
                    new_name="party_kind",
                ),
                migrations.AlterField(
                    model_name="workrecord",
                    name="party_kind",
                    field=models.CharField(
                        db_column="worker",
                        default="",
                        max_length=50,
                        verbose_name="区分",
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.AddField(
            model_name="workrecord",
            name="craftsman",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="職人"),
        ),
        migrations.RunPython(copy_craftsman_and_size, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="workrecord",
            name="dimension",
        ),
        migrations.AlterField(
            model_name="workrecord",
            name="work_size",
            field=models.CharField(blank=True, default="", max_length=255, null=True, verbose_name="寸法"),
        ),
        migrations.AlterField(
            model_name="workrecord",
            name="helpers",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="手元"),
        ),
        migrations.RenameField(
            model_name="worker",
            old_name="company",
            new_name="affiliation",
        ),
        migrations.AlterField(
            model_name="worker",
            name="affiliation",
            field=models.CharField(
                blank=True,
                default="",
                help_text="基本は空欄（自社）です。専属のひとり親方のときだけ、本人名や屋号を入力します。応援企業とは別です。",
                max_length=100,
                verbose_name="所属",
            ),
        ),
    ]
