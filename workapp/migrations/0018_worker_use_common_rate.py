from django.db import migrations, models


def mark_custom_rate_workers(apps, schema_editor):
    Worker = apps.get_model("workapp", "Worker")
    WorkerRate = apps.get_model("workapp", "WorkerRate")
    seen = set()
    for rate in WorkerRate.objects.order_by("-id"):
        key = (rate.worker_id, rate.work_size_id)
        if key in seen:
            rate.delete()
        else:
            seen.add(key)
    worker_ids = WorkerRate.objects.values_list("worker_id", flat=True).distinct()
    Worker.objects.filter(pk__in=worker_ids).update(use_common_rate=False)


class Migration(migrations.Migration):

    dependencies = [
        ("workapp", "0017_worker_no_type_and_worker_pay"),
    ]

    operations = [
        migrations.AddField(
            model_name="worker",
            name="use_common_rate",
            field=models.BooleanField(
                default=True,
                help_text="オフにすると、この作業員だけの単価を下の表で入力します。",
                verbose_name="共通単価を使用する",
            ),
        ),
        migrations.RunPython(mark_custom_rate_workers, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="workerrate",
            options={
                "verbose_name": "作業員の独自単価",
                "verbose_name_plural": "作業員の独自単価",
            },
        ),
        migrations.AlterUniqueTogether(
            name="workerrate",
            unique_together={("worker", "work_size")},
        ),
    ]
