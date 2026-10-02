import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from workapp.models import (
    Company,
    CompanyRate,
    GeneralContractor,
    GeneralContractorRate,
    Site,
    WorkRecord,
    WorkSize,
    WorkType,
    Worker,
    WorkerDefaultRate,
)
from workapp.views import (
    TWO_HELPER_EACH_PERCENT,
    _amount_at_percent,
    _shokunin_deduction_percent,
    _worker_unit_price,
)

VOUCHER_PREFIX = "DEMO-"
RECENT_DAY_SPAN = 6

CONTRACTORS = (
    {"name": "青葉建設", "closing_day": 20},
    {"name": "みどり工務店", "closing_day": 31},
    {"name": "東雲建設", "closing_day": 15},
)
SITES = (
    {"name": "北町マンション新築", "contractor": "青葉建設"},
    {"name": "北町マンション改修", "contractor": "青葉建設"},
    {"name": "北町マンション外構", "contractor": "青葉建設"},
    {"name": "南校舎改修", "contractor": "みどり工務店"},
    {"name": "南校舎新築", "contractor": "みどり工務店"},
    {"name": "中央ビル解体", "contractor": "東雲建設"},
)
WORKERS = (
    {"name": "山田太郎", "worker_type": "職人", "temoto_percent": None},
    {"name": "佐藤健一", "worker_type": "職人", "temoto_percent": None},
    {"name": "鈴木一郎", "worker_type": "職人", "temoto_percent": None},
    {"name": "高橋次郎", "worker_type": "手元", "temoto_percent": None},
    {"name": "伊藤三郎", "worker_type": "手元", "temoto_percent": 22},
    {"name": "中村五郎", "worker_type": "手元", "temoto_percent": None},
)
COMPANIES = ("東洋サポート",)
WORK_TYPES = ("圧接", "溶接", "切断")
SIZES = ("D19", "D22", "D25")
BILLING_PRICES = {"D19": 1200, "D22": 1500, "D25": 1800}
PAY_PRICES = {"D19": 800, "D22": 1000, "D25": 1200}
OUEN_PRICES = {"D19": 900, "D22": 1100, "D25": 1300}
CRAFTSMEN = ("山田太郎", "佐藤健一", "鈴木一郎")
HELPER_GROUPS = (
    (),
    ("高橋次郎",),
    ("高橋次郎", "伊藤三郎"),
    ("高橋次郎", "伊藤三郎", "中村五郎"),
    ("伊藤三郎",),
)
SITE_CYCLE = (
    "北町マンション新築",
    "北町マンション改修",
    "北町マンション外構",
    "南校舎改修",
    "南校舎新築",
    "中央ビル解体",
)
VOUCHER_COUNT = 15


class Command(BaseCommand):
    help = "DEMO_SEED=1 のときだけ、足りない架空マスタと直近のデモ伝票を追加する"

    def handle(self, *args, **options):
        if os.environ.get("DEMO_SEED") != "1":
            self.stdout.write("DEMO_SEED=1 ではないので、デモデータは入れません。")
            return

        contractors = {
            item["name"]: GeneralContractor.objects.get_or_create(
                name=item["name"],
                defaults={"closing_day": item["closing_day"]},
            )[0]
            for item in CONTRACTORS
        }
        sites = {}
        for item in SITES:
            sites[item["name"]] = Site.objects.get_or_create(
                name=item["name"],
                general_contractor=contractors[item["contractor"]],
            )[0]
        workers = {}
        for item in WORKERS:
            workers[item["name"]] = Worker.objects.get_or_create(
                name=item["name"],
                defaults={
                    "worker_type": item["worker_type"],
                    "temoto_percent": item["temoto_percent"],
                    "use_common_rate": True,
                },
            )[0]
        companies = {
            name: Company.objects.get_or_create(name=name)[0]
            for name in COMPANIES
        }
        for name in WORK_TYPES:
            WorkType.objects.get_or_create(name=name)
        sizes = {
            name: WorkSize.objects.get_or_create(name=name)[0]
            for name in SIZES
        }
        for size_name, size in sizes.items():
            WorkerDefaultRate.objects.get_or_create(
                work_size=size,
                defaults={"unit_price": PAY_PRICES[size_name]},
            )
            for contractor in contractors.values():
                GeneralContractorRate.objects.get_or_create(
                    general_contractor=contractor,
                    work_size=size,
                    defaults={"unit_price": BILLING_PRICES[size_name]},
                )
            for company in companies.values():
                CompanyRate.objects.get_or_create(
                    company=company,
                    work_size=size,
                    defaults={"unit_price": OUEN_PRICES[size_name]},
                )

        today = timezone.localdate()
        created_vouchers = 0
        created_rows = 0
        for voucher in _demo_vouchers(sites, workers, companies, sizes, today):
            if WorkRecord.objects.filter(voucher_no=voucher["voucher_no"]).exists():
                continue
            rows = _records_for_voucher(voucher)
            WorkRecord.objects.bulk_create(rows)
            created_vouchers += 1
            created_rows += len(rows)
        if created_vouchers == 0:
            self.stdout.write("デモ伝票は既にあるので、伝票は追加しません。")
            return
        self.stdout.write(self.style.SUCCESS(
            f"デモ伝票 {created_vouchers} 件（作業記録 {created_rows} 行）を追加しました。"
        ))


def _demo_vouchers(sites, workers, companies, sizes, today):
    company = companies["東洋サポート"]
    vouchers = []
    for index in range(VOUCHER_COUNT):
        helper_names = HELPER_GROUPS[index % len(HELPER_GROUPS)]
        site_name = SITE_CYCLE[index % len(SITE_CYCLE)]
        size_name = SIZES[index % len(SIZES)]
        vouchers.append({
            "voucher_no": f"{VOUCHER_PREFIX}{1001 + index}",
            "date": today - timedelta(days=index % (RECENT_DAY_SPAN + 1)),
            "site": sites[site_name],
            "craftsman": workers[CRAFTSMEN[index % len(CRAFTSMEN)]],
            "helpers": tuple(workers[name] for name in helper_names),
            "company": company if index % 2 == 0 else None,
            "lines": (
                {
                    "work_type": WORK_TYPES[index % len(WORK_TYPES)],
                    "size": sizes[size_name],
                    "qty": 4 + (index % 5),
                    "mark": "長尺" if index % 4 == 0 else "",
                },
            ),
        })
    return vouchers


def _records_for_voucher(voucher):
    site = voucher["site"]
    contractor_name = site.general_contractor.name
    helpers = voucher["helpers"]
    helper_count = len(helpers)
    deduct_percent = _shokunin_deduction_percent(helper_count)
    company = voucher["company"]
    rows = []
    for line in voucher["lines"]:
        size = line["size"]
        qty = line["qty"]
        billing = GeneralContractorRate.objects.get(
            general_contractor=site.general_contractor,
            work_size=size,
        ).unit_price
        pay = _worker_unit_price(voucher["craftsman"], size)
        ouen = None
        if company is not None:
            ouen = CompanyRate.objects.get(company=company, work_size=size).unit_price
        pay_total = pay * qty
        helper_amounts = _helper_amounts(pay_total, helpers, deduct_percent)
        common = {
            "voucher_no": voucher["voucher_no"],
            "date": voucher["date"],
            "site": site.name,
            "work_type": line["work_type"],
            "work_size": size.name,
            "dimension": size.name,
            "work_amount": qty,
            "remark": "",
            "size_mark": line["mark"],
            "price_mode": "master",
            "manual_billing": None,
            "manual_pay": None,
            "manual_ouen": None,
            "general_contractor": contractor_name,
            "primary_company": "",
            "billing_contractor": contractor_name,
            "company": company.name if company else "",
            "helper_count": helper_count,
            "temoto1": helpers[0].name if helper_count > 0 else "",
            "temoto2": helpers[1].name if helper_count > 1 else "",
            "temoto3": helpers[2].name if helper_count > 2 else "",
        }
        rows.append(WorkRecord(
            **common,
            worker="元請",
            helpers=voucher["craftsman"].name,
            temoto_percent=deduct_percent or None,
            shokunin_deduction_percent=None,
            unit_price=billing,
            total_price=billing * qty,
        ))
        rows.append(WorkRecord(
            **common,
            worker="職人",
            helpers=voucher["craftsman"].name,
            temoto_percent=deduct_percent or None,
            shokunin_deduction_percent=deduct_percent if helper_count else None,
            unit_price=pay,
            total_price=pay_total - sum(helper_amounts),
        ))
        for helper, amount in zip(helpers, helper_amounts):
            rows.append(WorkRecord(
                **common,
                worker="手元",
                helpers=helper.name,
                temoto_percent=helper.temoto_percent,
                shokunin_deduction_percent=None,
                unit_price=pay,
                total_price=amount,
            ))
        if company is not None:
            rows.append(WorkRecord(
                **common,
                worker="応援",
                helpers=voucher["craftsman"].name,
                temoto_percent=None,
                shokunin_deduction_percent=None,
                unit_price=ouen,
                total_price=ouen * qty,
            ))
    return rows


def _helper_amounts(pay_total, helpers, deduct_percent):
    count = len(helpers)
    if count == 0:
        return []
    if count == 1 and helpers[0].temoto_percent is not None:
        return [_amount_at_percent(pay_total, helpers[0].temoto_percent)]
    if count == 2:
        return [
            _amount_at_percent(pay_total, TWO_HELPER_EACH_PERCENT),
            _amount_at_percent(pay_total, TWO_HELPER_EACH_PERCENT),
        ]
    pool = _amount_at_percent(pay_total, deduct_percent)
    each = pool // count
    amounts = [each] * count
    amounts[0] += pool - each * count
    return amounts
