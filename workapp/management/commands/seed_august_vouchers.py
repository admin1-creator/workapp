from calendar import monthrange
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from workapp.models import (
    Company,
    CompanyRate,
    GeneralContractorRate,
    Site,
    WorkRecord,
    WorkSize,
    Worker,
    WorkerDefaultRate,
    WorkerRate,
)
from workapp.views import (
    OUEN_UNIT,
    _amount_at_percent,
    _shokunin_deduction_percent,
    _worker_unit_price,
)

PREFIX = "A2608"
WORK_TYPES = ("圧接", "溶接", "ガス圧接", "切断", "その他")
VOUCHERS_PER_DAY = 15
LINE_COUNTS = (4, 5, 5, 5, 6)
AUGUST = 8
SAMPLE_QUANTITY_BASE = 6
SAMPLE_QUANTITY_CYCLE = 15
SAMPLE_LONG_MARK_EVERY = 7
SAMPLE_CRAFTSMAN_STRIDE = 3
SAMPLE_HELPER_STRIDE = 2
SAMPLE_OUEN_CYCLE = 3
SAMPLE_OUEN_SKIP_REMAINDER = 2
SAMPLE_HELPER_CYCLE = 5
FALLBACK_BILLING_UNIT = 1000
FALLBACK_PAY_UNIT = 800


class Command(BaseCommand):
    help = "8/1〜8/31の現場伝票サンプルを追加する（既存のA2608…は作り直す）"

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=2026)

    def handle(self, *args, **options):
        year = options["year"]
        sites = list(Site.objects.select_related("general_contractor").order_by("pk"))
        workers = list(Worker.objects.order_by("pk"))
        temoto_pool = [
            worker for worker in workers if (worker.worker_type or "") == "手元"
        ] or workers
        companies = list(Company.objects.order_by("pk"))
        sizes = list(WorkSize.objects.order_by("pk"))
        if not sites:
            raise CommandError("現場マスタがありません。")
        if not workers:
            raise CommandError("作業員マスタがありません。")
        if not sizes:
            raise CommandError("寸法マスタがありません。")

        default_rates = {
            rate.work_size_id: rate.unit_price
            for rate in WorkerDefaultRate.objects.all()
        }
        contractor_rates = {}
        for rate in GeneralContractorRate.objects.all():
            contractor_rates[(rate.general_contractor_id, rate.work_size_id)] = (
                rate.unit_price
            )
        company_rates = {}
        for rate in CompanyRate.objects.all():
            company_rates[(rate.company_id, rate.work_size_id)] = rate.unit_price
        own_rates = {}
        for rate in WorkerRate.objects.all():
            own_rates[(rate.worker_id, rate.work_size_id)] = rate.unit_price

        deleted, _ = WorkRecord.objects.filter(voucher_no__startswith=PREFIX).delete()
        if deleted:
            self.stdout.write(f"既存の {PREFIX} 伝票を {deleted} 件削除しました。")

        records = []
        days = monthrange(year, AUGUST)[1]
        line_total = 0
        voucher_total = 0
        for day in range(1, days + 1):
            work_date = date(year, AUGUST, day)
            for seq in range(VOUCHERS_PER_DAY):
                voucher_total += 1
                site = sites[(day + seq) % len(sites)]
                contractor = site.general_contractor
                contractor_name = contractor.name if contractor else ""
                craftsman = workers[
                    (day * SAMPLE_CRAFTSMAN_STRIDE + seq) % len(workers)
                ]
                temoto = temoto_pool[
                    (day + seq * SAMPLE_HELPER_STRIDE) % len(temoto_pool)
                ]
                if temoto.pk == craftsman.pk and len(temoto_pool) > 1:
                    temoto = temoto_pool[
                        (day + seq * SAMPLE_HELPER_STRIDE + 1) % len(temoto_pool)
                    ]
                company = companies[(day + seq) % len(companies)] if companies else None
                has_ouen = bool(company) and (
                    seq % SAMPLE_OUEN_CYCLE != SAMPLE_OUEN_SKIP_REMAINDER
                )
                has_temoto = (
                    seq % SAMPLE_HELPER_CYCLE == 0 and temoto.pk != craftsman.pk
                )
                helper_count = 1 if has_temoto else 0
                deduct_pct = _shokunin_deduction_percent(helper_count)
                line_n = LINE_COUNTS[seq % len(LINE_COUNTS)]
                voucher_no = f"{PREFIX}{day:02d}-{seq + 1:02d}"
                for line_i in range(line_n):
                    line_total += 1
                    size = sizes[(day + seq + line_i) % len(sizes)]
                    qty = SAMPLE_QUANTITY_BASE + (
                        (day + seq + line_i) % SAMPLE_QUANTITY_CYCLE
                    )
                    work_type = WORK_TYPES[line_i % len(WORK_TYPES)]
                    mark = (
                        "長尺"
                        if (day + seq + line_i) % SAMPLE_LONG_MARK_EVERY == 0
                        else ""
                    )
                    moto_unit = contractor_rates.get(
                        (contractor.pk if contractor else None, size.pk)
                    )
                    if moto_unit is None:
                        moto_unit = default_rates.get(size.pk, FALLBACK_BILLING_UNIT)
                    pay_unit = _worker_unit_price(craftsman, size)
                    if pay_unit is None:
                        pay_unit = own_rates.get((craftsman.pk, size.pk))
                    if pay_unit is None:
                        pay_unit = default_rates.get(size.pk, FALLBACK_PAY_UNIT)
                    ouen_unit = None
                    if has_ouen and company:
                        ouen_unit = company_rates.get((company.pk, size.pk), OUEN_UNIT)
                    moto_total = moto_unit * qty
                    pay_total = pay_unit * qty
                    helper_cut = (
                        _amount_at_percent(pay_total, deduct_pct) if helper_count else 0
                    )
                    shokunin_total = pay_total - (helper_cut or 0)
                    common = {
                        "voucher_no": voucher_no,
                        "date": work_date,
                        "site": site.name,
                        "work_type": work_type,
                        "work_size": size.name,
                        "work_amount": qty,
                        "remark": "",
                        "size_mark": mark,
                        "price_mode": "master",
                        "manual_billing": None,
                        "manual_pay": None,
                        "manual_ouen": None,
                        "general_contractor": contractor_name,
                        "primary_company": "",
                        "billing_contractor": contractor_name,
                        "company": company.name if has_ouen and company else "",
                        "helper_count": helper_count,
                        "temoto1": temoto.name if has_temoto else "",
                        "temoto2": "",
                        "temoto3": "",
                    }
                    records.append(
                        WorkRecord(
                            **common,
                            party_kind="元請",
                            craftsman=craftsman.name,
                            temoto_percent=deduct_pct or None,
                            shokunin_deduction_percent=None,
                            unit_price=moto_unit,
                            total_price=moto_total,
                        )
                    )
                    records.append(
                        WorkRecord(
                            **common,
                            party_kind="職人",
                            craftsman=craftsman.name,
                            temoto_percent=deduct_pct or None,
                            shokunin_deduction_percent=deduct_pct
                            if helper_count
                            else None,
                            unit_price=pay_unit,
                            total_price=shokunin_total,
                        )
                    )
                    if has_temoto:
                        records.append(
                            WorkRecord(
                                **common,
                                party_kind="手元",
                                craftsman=craftsman.name,
                                helpers=temoto.name,
                                temoto_percent=temoto.temoto_percent,
                                shokunin_deduction_percent=None,
                                unit_price=pay_unit,
                                total_price=helper_cut,
                            )
                        )
                    if has_ouen and company:
                        records.append(
                            WorkRecord(
                                **common,
                                party_kind="応援",
                                craftsman=craftsman.name,
                                temoto_percent=None,
                                shokunin_deduction_percent=None,
                                unit_price=ouen_unit,
                                total_price=(ouen_unit or 0) * qty,
                            )
                        )

        WorkRecord.objects.bulk_create(records, batch_size=500)
        self.stdout.write(
            self.style.SUCCESS(
                f"{year}/{AUGUST}/1〜{year}/{AUGUST}/{days} に伝票 {voucher_total} 件、作業行 {line_total} 行、"
                f"記録 {len(records)} 件を追加しました。"
            )
        )
