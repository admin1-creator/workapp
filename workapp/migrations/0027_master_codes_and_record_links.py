from django.db import migrations, models
import django.db.models.deletion


def _unique(model, name):
    text = (name or "").strip()
    if not text:
        return None
    matches = list(model.objects.filter(name=text)[:2])
    if len(matches) == 1:
        return matches[0]
    return None


def link_records_by_unique_name(apps, schema_editor):
    WorkRecord = apps.get_model("workapp", "WorkRecord")
    Site = apps.get_model("workapp", "Site")
    Worker = apps.get_model("workapp", "Worker")
    Company = apps.get_model("workapp", "Company")
    GeneralContractor = apps.get_model("workapp", "GeneralContractor")
    for record in WorkRecord.objects.all():
        site = _unique(Site, record.site)
        craftsman = _unique(Worker, record.craftsman)
        company = _unique(Company, record.company)
        contractor = _unique(GeneralContractor, record.general_contractor)
        primary = _unique(GeneralContractor, record.primary_company)
        billing = _unique(GeneralContractor, record.billing_contractor)
        temoto1 = _unique(Worker, record.temoto1)
        temoto2 = _unique(Worker, record.temoto2)
        temoto3 = _unique(Worker, record.temoto3)
        record.site_master = site
        record.craftsman_worker = craftsman
        record.company_master = company
        record.contractor_master = contractor
        record.primary_master = primary
        record.billing_master = billing
        record.temoto1_worker = temoto1
        record.temoto2_worker = temoto2
        record.temoto3_worker = temoto3
        record.save(
            update_fields=[
                "site_master",
                "craftsman_worker",
                "company_master",
                "contractor_master",
                "primary_master",
                "billing_master",
                "temoto1_worker",
                "temoto2_worker",
                "temoto3_worker",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("workapp", "0026_worker_site_name_kana"),
    ]

    operations = [
        migrations.AddField(
            model_name="worker",
            name="employee_number",
            field=models.CharField(
                blank=True,
                help_text="他部門へ渡す番号です。空欄の人は未採番です。",
                max_length=20,
                null=True,
                unique=True,
                verbose_name="社員番号",
            ),
        ),
        migrations.AddField(
            model_name="worker",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text="オフにすると、新しい伝票の候補から外れます。過去の伝票には残ります。",
                verbose_name="有効",
            ),
        ),
        migrations.AddField(
            model_name="site",
            name="site_code",
            field=models.CharField(
                blank=True,
                help_text="他部門へ渡すコードです。空欄の現場は未採番です。",
                max_length=20,
                null=True,
                unique=True,
                verbose_name="現場コード",
            ),
        ),
        migrations.AddField(
            model_name="site",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text="オフにすると、新しい伝票の候補から外れます。過去の伝票には残ります。",
                verbose_name="有効",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="site_master",
            field=models.ForeignKey(
                blank=True,
                db_column="site_id",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="work_records",
                to="workapp.site",
                verbose_name="現場",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="craftsman_worker",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="craftsman_records",
                to="workapp.worker",
                verbose_name="職人",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="company_master",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="work_records",
                to="workapp.company",
                verbose_name="応援企業",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="contractor_master",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="contractor_records",
                to="workapp.generalcontractor",
                verbose_name="元請",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="primary_master",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="primary_records",
                to="workapp.generalcontractor",
                verbose_name="1次企業",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="billing_master",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="billing_records",
                to="workapp.generalcontractor",
                verbose_name="請求先",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="temoto1_worker",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="temoto1_records",
                to="workapp.worker",
                verbose_name="手元1",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="temoto2_worker",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="temoto2_records",
                to="workapp.worker",
                verbose_name="手元2",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="temoto3_worker",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="temoto3_records",
                to="workapp.worker",
                verbose_name="手元3",
            ),
        ),
        migrations.RunPython(link_records_by_unique_name, migrations.RunPython.noop),
    ]
