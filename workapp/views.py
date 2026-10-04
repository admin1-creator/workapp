from types import SimpleNamespace
from datetime import date, datetime
from math import floor
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import logout
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from .models import (
    GeneralContractorRate,
    GeneralContractor,
    WorkSize,
    WorkRecord,
    Worker,
    WorkerRate,
    WorkerDefaultRate,
    CompanyRate,
    Company,
    Site,
    unique_named,
    PrintedDocument,
    PrintedDocumentItem,
    KagamiSheet,
)
from .constants import (
    CONSUMPTION_TAX_PERCENT,
    DB_INT_MAX,
    DB_INT_MIN,
    HELPER_FIELDS,
    MAX_HELPER_COUNT,
    MAX_WORK_LINES,
    MULTI_HELPER_DEDUCTION_PERCENT,
    ONE_HELPER_DEDUCTION_PERCENT,
    PERCENT_SCALE,
    REMARK_MAX_LENGTH,
    SIZE_MARK_MAX_LENGTH,
    WORK_SIZE_MAX_LENGTH,
    WORK_TYPE_MAX_LENGTH,
)
from .forms import SignupForm, WorkRecordForm


OUEN_UNIT = 16000

# 手元人数ごとの割合（職人合計に対する％）
# 1人: 職人から ONE_HELPER_DEDUCTION_PERCENT を引き、手元へ同率
# 2人: 職人から MULTI_HELPER_DEDUCTION_PERCENT を引き、手元は 20% ずつ
# 3人: 職人から MULTI_HELPER_DEDUCTION_PERCENT を引き、手元はその合計を人数で分割
TWO_HELPER_EACH_PERCENT = 20
TEMOTO_SHARE_RULES = {
    1: {
        "shokunin_deduction": ONE_HELPER_DEDUCTION_PERCENT,
        "temoto_pool": ONE_HELPER_DEDUCTION_PERCENT,
        "temoto_each": ONE_HELPER_DEDUCTION_PERCENT,
    },
    2: {
        "shokunin_deduction": MULTI_HELPER_DEDUCTION_PERCENT,
        "temoto_pool": MULTI_HELPER_DEDUCTION_PERCENT,
        "temoto_each": TWO_HELPER_EACH_PERCENT,
    },
    3: {
        "shokunin_deduction": MULTI_HELPER_DEDUCTION_PERCENT,
        "temoto_pool": MULTI_HELPER_DEDUCTION_PERCENT,
        "temoto_each": None,
    },
}


def _temoto_share_rules(helper_count):
    return TEMOTO_SHARE_RULES.get(
        int(helper_count or 0),
        {
            "shokunin_deduction": 0,
            "temoto_pool": 0,
            "temoto_each": 0,
        },
    )


def _shokunin_deduction_percent(helper_count):
    count = int(helper_count or 0)
    if count <= 0:
        return 0
    if count == 1:
        return ONE_HELPER_DEDUCTION_PERCENT
    return MULTI_HELPER_DEDUCTION_PERCENT


def _temoto_workers_from_data(data):
    workers = []
    for key in HELPER_FIELDS:
        pk = data.get(f"{key}_id")
        name = str(data.get(key) or "").strip()
        person = None
        if pk:
            person = Worker.objects.filter(pk=pk).first()
        if person is None and name:
            person = unique_named(Worker, name)
        if person is not None:
            workers.append(person)
        elif name:
            workers.append(Worker(name=name, temoto_percent=None))
    if not workers:
        for name in _temoto_names_from_data(data):
            person = unique_named(Worker, name)
            workers.append(person or Worker(name=name, temoto_percent=None))
    return workers[:MAX_HELPER_COUNT]


def _temoto_allocation(helpers):
    """個人％は、その人が1人で手元に入ったときの率である。

    複数人に各自の％をそのまま足すと、職人控除（1人35%、2人以上40%）を超える。
    人数で割ってから配り、合計が1人分の率に収まるようにする。割り切れない端数は切り捨てる。
    """
    helpers = [helper for helper in (helpers or []) if helper]
    count = len(helpers)
    empty = {
        "mode": "none",
        "count": 0,
        "shokunin_deduction": 0,
        "temoto_pool": 0,
        "temoto_each": None,
        "split_pool": False,
        "shares": [],
    }
    if count == 0:
        return empty

    has_individual = any(
        getattr(helper, "temoto_percent", None) is not None for helper in helpers
    )
    shares = []
    count_deduction = _shokunin_deduction_percent(count)
    if has_individual:
        for helper in helpers:
            raw = helper.temoto_percent if helper.temoto_percent is not None else 0
            applied = raw // count
            shares.append(
                {
                    "name": helper.name,
                    "raw_percent": raw,
                    "percent": applied,
                }
            )
        helper_pool = sum(share["percent"] for share in shares)
        return {
            "mode": "individual",
            "count": count,
            "shokunin_deduction": count_deduction,
            "temoto_pool": helper_pool,
            "temoto_each": None,
            "split_pool": False,
            "shares": shares,
        }

    rules = _temoto_share_rules(count)
    split_pool = rules["temoto_each"] is None
    if split_pool:
        each = (rules["temoto_pool"] or 0) // count
    else:
        each = rules["temoto_each"] or 0
    for helper in helpers:
        shares.append(
            {
                "name": helper.name,
                "raw_percent": None,
                "percent": each,
            }
        )
    return {
        "mode": "count",
        "count": count,
        "shokunin_deduction": count_deduction,
        "temoto_pool": rules["temoto_pool"] or 0,
        "temoto_each": rules["temoto_each"],
        "split_pool": split_pool,
        "shares": shares,
    }


def _temoto_allocation_from_data(data):
    return _temoto_allocation(_temoto_workers_from_data(data))


def _amount_at_percent(amount, percent):
    """本体 × ％ ÷ 100。小数点以下は切り捨て（floor）。四捨五入しない。"""
    if amount is None or amount == "":
        return None
    try:
        base = float(amount)
    except TypeError, ValueError:
        return None
    return int(floor(base * int(percent or 0) / PERCENT_SCALE))


def _temoto_line_amounts(line_total, allocation):
    count = allocation["count"]
    if count == 0:
        return []
    if line_total is None:
        return [None] * count
    if allocation.get("split_pool"):
        pool = _amount_at_percent(line_total, allocation["temoto_pool"])
        amounts = []
        for index in range(count):
            if index == count - 1:
                amounts.append(pool - (pool // count) * (count - 1))
            else:
                amounts.append(pool // count)
        return amounts
    return [
        _amount_at_percent(line_total, share["percent"])
        for share in allocation["shares"]
    ]


def _with_temoto_deduction(work_rows, allocation):
    for row in work_rows:
        row.setdefault("line_total", row.get("total_price"))

    subtotal = sum(row["line_total"] or 0 for row in work_rows)
    helper_count = allocation["count"] if allocation else 0
    percent = _shokunin_deduction_percent(helper_count)

    deduction = 0
    if percent:
        for row in work_rows:
            line_total = row["line_total"]
            if line_total is None:
                row["total_price"] = None
                continue
            cut = _amount_at_percent(line_total, percent)
            deduction += cut
            row["total_price"] = line_total - cut

    total = sum(row["total_price"] or 0 for row in work_rows)
    allocation = allocation or _temoto_allocation([])
    return {
        "subtotal": subtotal,
        "deduction_percent": percent,
        "deduction": deduction,
        "total": total,
        "helper_count": allocation["count"],
        "temoto_pool_percent": allocation["temoto_pool"],
        "temoto_each_percent": allocation["temoto_each"],
        "temoto_mode": allocation["mode"],
        "temoto_shares": allocation["shares"],
    }


def _sum_line_totals(work_rows, allocation=None):
    total = sum(row.get("total_price") or 0 for row in work_rows)
    allocation = allocation or _temoto_allocation([])
    return {
        "subtotal": total,
        "deduction_percent": 0,
        "deduction": 0,
        "total": total,
        "helper_count": allocation["count"],
        "temoto_pool_percent": allocation.get("temoto_pool") or 0,
        "temoto_each_percent": allocation.get("temoto_each"),
        "temoto_mode": allocation.get("mode") or "none",
        "temoto_shares": allocation.get("shares") or [],
    }


def _temoto_names_from_data(data):
    names = []
    for key in HELPER_FIELDS:
        name = str(data.get(key) or "").strip()
        if name:
            names.append(name)
    if not names and data.get("helper"):
        names = [
            name.strip()
            for name in str(data["helper"]).replace("、", ",").split(",")
            if name.strip()
        ]
    return names[:MAX_HELPER_COUNT]


def _collect_temoto_from_form(form):
    names = []
    ids = []
    fields = {}
    for key in HELPER_FIELDS:
        person = form.cleaned_data.get(key)
        if person:
            names.append(person.name)
            ids.append(person.pk)
            fields[key] = person.name
            fields[f"{key}_id"] = person.pk
        else:
            fields[key] = ""
            fields[f"{key}_id"] = None
    return names, ids, fields


def _resolve_worker(data):
    worker_id = data.get("worker_id")
    if worker_id:
        return Worker.objects.get(pk=worker_id)

    worker_str = str(data.get("worker") or "")
    worker = unique_named(Worker, worker_str)
    if worker:
        return worker

    name_only = worker_str.split("（")[0]
    return Worker.objects.get(name=name_only)


def _parse_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _in_db_int(value):
    return value is None or DB_INT_MIN <= value <= DB_INT_MAX


def _line_range_error(work_type, work_size, amount, note, mark, money_values):
    """PostgreSQL は文字列長と32bit整数を超えると保存に失敗する。保存前に止める。"""
    if len(work_type) > WORK_TYPE_MAX_LENGTH:
        return f"作業内容は{WORK_TYPE_MAX_LENGTH}文字までです。"
    if len(note) > REMARK_MAX_LENGTH:
        return f"備考は{REMARK_MAX_LENGTH}文字までです。"
    if len(mark) > SIZE_MARK_MAX_LENGTH:
        return f"印は{SIZE_MARK_MAX_LENGTH}文字までです。"
    size_text = "" if str(work_size).isdigit() else str(work_size or "")
    if len(size_text) > WORK_SIZE_MAX_LENGTH:
        return f"寸法は{WORK_SIZE_MAX_LENGTH}文字までです。"
    if not _in_db_int(amount):
        return f"作業量は{DB_INT_MIN}から{DB_INT_MAX}の整数にしてください。"
    for money in money_values:
        if not _in_db_int(money):
            return f"金額は{DB_INT_MIN}から{DB_INT_MAX}の整数にしてください。"
        if amount is not None and money is not None and not _in_db_int(amount * money):
            return "作業量と単価を掛けた金額が、保存できる範囲を超えています。"
    return ""


def _posted_line_has_work(
    work_type,
    work_size,
    work_amount,
    note,
    mark,
    billing,
    pay,
    ouen,
    rate_billing=None,
    rate_pay=None,
    rate_ouen=None,
):
    if (work_type or "").strip():
        return True
    if (work_size or "").strip():
        return True
    if _parse_int(work_amount) is not None:
        return True
    if (note or "").strip():
        return True
    if (mark or "").strip():
        return True
    return any(
        value is not None
        for value in (billing, pay, ouen, rate_billing, rate_pay, rate_ouen)
    )


def _collect_posted_work_lines(post):
    work_types = []
    work_sizes = []
    work_amounts = []
    remarks = []
    size_marks = []
    price_modes = []
    manual_billings = []
    manual_pays = []
    manual_ouens = []
    rate_billings = []
    rate_pays = []
    rate_ouens = []
    manual_missing = False
    type_missing = False
    line_error = ""
    prev_type = ""
    for i in range(1, MAX_WORK_LINES + 1):
        work_type = (post.get(f"work_type_{i}") or "").strip()
        work_size = post.get(f"work_size_{i}") or ""
        work_amount = post.get(f"work_amount_{i}")
        note = (post.get(f"remark_{i}") or "").strip()
        mark = (post.get(f"size_mark_{i}") or "").strip()
        mode = (post.get(f"price_mode_{i}") or "master").strip()
        billing = _parse_int(post.get(f"manual_billing_{i}"))
        pay = _parse_int(post.get(f"manual_pay_{i}"))
        ouen = _parse_int(post.get(f"manual_ouen_{i}"))
        rate_billing = _parse_int(post.get(f"rate_billing_{i}"))
        rate_pay = _parse_int(post.get(f"rate_pay_{i}"))
        rate_ouen = _parse_int(post.get(f"rate_ouen_{i}"))
        if not _posted_line_has_work(
            work_type,
            work_size,
            work_amount,
            note,
            mark,
            billing,
            pay,
            ouen,
            rate_billing,
            rate_pay,
            rate_ouen,
        ):
            continue
        if not work_type:
            work_type = prev_type
        if not work_type:
            type_missing = True
            continue
        prev_type = work_type
        amount_number = _parse_int(work_amount)
        money_values = (billing, pay, ouen, rate_billing, rate_pay, rate_ouen)
        line_error = _line_range_error(
            work_type, work_size, amount_number, note, mark, money_values
        )
        if line_error:
            break
        if (
            mode == "manual"
            and billing is None
            and pay is None
            and ouen is None
            and rate_billing is None
            and rate_pay is None
            and rate_ouen is None
        ):
            manual_missing = True
        work_types.append(work_type)
        work_sizes.append(work_size)
        work_amounts.append(amount_number)
        remarks.append(note)
        size_marks.append(mark)
        price_modes.append("manual" if mode == "manual" else "master")
        manual_billings.append(billing)
        manual_pays.append(pay)
        manual_ouens.append(ouen)
        rate_billings.append(rate_billing)
        rate_pays.append(rate_pay)
        rate_ouens.append(rate_ouen)
    return {
        "work_types": work_types,
        "work_sizes": work_sizes,
        "work_amounts": work_amounts,
        "remarks": remarks,
        "size_marks": size_marks,
        "price_modes": price_modes,
        "manual_billings": manual_billings,
        "manual_pays": manual_pays,
        "manual_ouens": manual_ouens,
        "rate_billings": rate_billings,
        "rate_pays": rate_pays,
        "rate_ouens": rate_ouens,
        "manual_missing": manual_missing,
        "type_missing": type_missing,
        "line_error": line_error,
    }


def _iter_work_lines(data):
    types = data.get("work_types") or []
    sizes = data.get("work_sizes") or []
    amounts = data.get("work_amounts") or []
    remarks = data.get("remarks") or []
    size_marks = data.get("size_marks") or []
    modes = data.get("price_modes") or []
    billings = data.get("manual_billings") or []
    pays = data.get("manual_pays") or []
    ouens = data.get("manual_ouens") or []
    rate_billings = data.get("rate_billings") or []
    rate_pays = data.get("rate_pays") or []
    rate_ouens = data.get("rate_ouens") or []
    for i, work_type in enumerate(types):
        mode = str(modes[i] if i < len(modes) else "master").strip() or "master"
        yield {
            "type": work_type,
            "size": sizes[i] if i < len(sizes) else "",
            "amount": amounts[i] if i < len(amounts) else None,
            "remark": (remarks[i] if i < len(remarks) else "") or "",
            "size_mark": (size_marks[i] if i < len(size_marks) else "") or "",
            "price_mode": "manual" if mode == "manual" else "master",
            "manual_billing": _parse_int(billings[i] if i < len(billings) else None),
            "manual_pay": _parse_int(pays[i] if i < len(pays) else None),
            "manual_ouen": _parse_int(ouens[i] if i < len(ouens) else None),
            "rate_billing": _parse_int(
                rate_billings[i] if i < len(rate_billings) else None
            ),
            "rate_pay": _parse_int(rate_pays[i] if i < len(rate_pays) else None),
            "rate_ouen": _parse_int(rate_ouens[i] if i < len(rate_ouens) else None),
        }


def _line_base(line):
    size_obj, size_name = _lookup_work_size(line["size"])
    return size_obj, {
        "type": line["type"],
        "size": size_name,
        "amount": line["amount"],
        "remark": line["remark"],
        "size_mark": line.get("size_mark") or "",
        "price_mode": line["price_mode"],
        "manual_billing": line["manual_billing"],
        "manual_pay": line["manual_pay"],
        "manual_ouen": line["manual_ouen"],
        "rate_billing": line.get("rate_billing"),
        "rate_pay": line.get("rate_pay"),
        "rate_ouen": line.get("rate_ouen"),
    }


def _priced_work_row(line, master_unit, manual_amount, manual_unit=None):
    size_obj, row = _line_base(line)
    if row["price_mode"] == "manual":
        row["unit_price"] = manual_unit
        if manual_amount is not None:
            row["total_price"] = manual_amount
        else:
            row["total_price"] = _safe_mul(manual_unit, row["amount"])
            if row["total_price"] is None:
                row["total_price"] = 0
        return size_obj, row
    row["unit_price"] = master_unit
    row["total_price"] = _safe_mul(master_unit, row["amount"])
    return size_obj, row


class LineOutOfRange(Exception):
    pass


def _safe_mul(left, right):
    if left is None or right is None:
        return None
    return left * right


def _lookup_work_size(work_size_value):
    if work_size_value in (None, ""):
        return None, ""
    try:
        obj = WorkSize.objects.get(id=int(work_size_value))
        return obj, obj.name
    except ValueError, TypeError, WorkSize.DoesNotExist:
        return None, str(work_size_value)


def _worker_unit_price(worker, size_obj):
    if not worker or not size_obj:
        return None
    if getattr(worker, "use_common_rate", True):
        default_rate = WorkerDefaultRate.objects.filter(work_size=size_obj).first()
        return default_rate.unit_price if default_rate else None
    rate = WorkerRate.objects.filter(worker=worker, work_size=size_obj).first()
    return rate.unit_price if rate else None


def _record_common_fields(data, role=None, allocation=None):
    role = role or data.get("unit_role")
    labels = {
        "moto": "元請",
        "shokunin": "職人",
        "temoto": "手元",
        "ouen": "応援",
    }
    worker_label = labels.get(role, data.get("worker") or "")
    temoto_names = _temoto_names_from_data(data)
    helpers_joined = "、".join(temoto_names)
    allocation = allocation or _temoto_allocation_from_data(data)
    helper_count = allocation["count"]
    deduction = _shokunin_deduction_percent(helper_count) or None
    if role == "temoto":
        temoto_percent = None
    else:
        temoto_percent = deduction

    helpers = ""
    if role == "temoto":
        helpers = helpers_joined or "手元"

    return {
        "voucher_no": data["voucher_no"],
        "date": data["date"],
        "site": data["site"],
        "general_contractor": data["general_contractor"],
        "primary_company": data.get("primary_company") or "",
        "billing_contractor": data.get("billing_contractor")
        or data.get("general_contractor")
        or "",
        "party_kind": worker_label,
        "craftsman": data.get("worker") or "",
        "company": data.get("company") or "",
        "helpers": helpers,
        "helper_count": helper_count,
        "temoto1": data.get("temoto1") or "",
        "temoto2": data.get("temoto2") or "",
        "temoto3": data.get("temoto3") or "",
        "site_master_id": data.get("site_id") or None,
        "contractor_master_id": data.get("general_contractor_id") or None,
        "primary_master_id": data.get("primary_company_id") or None,
        "billing_master_id": data.get("billing_contractor_id")
        or data.get("primary_company_id")
        or data.get("general_contractor_id")
        or None,
        "craftsman_worker_id": data.get("worker_id") or None,
        "company_master_id": data.get("company_id") or None,
        "temoto1_worker_id": data.get("temoto1_id") or None,
        "temoto2_worker_id": data.get("temoto2_id") or None,
        "temoto3_worker_id": data.get("temoto3_id") or None,
        "temoto_percent": temoto_percent or None,
        "shokunin_deduction_percent": deduction
        if role == "shokunin" and helper_count
        else None,
    }


def _serialize_record_fields(fields):
    out = {}
    for key, value in fields.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _deserialize_record_fields(fields):
    out = dict(fields)
    date_value = out.get("date")
    if isinstance(date_value, str) and date_value:
        out["date"] = datetime.strptime(date_value[:10], "%Y-%m-%d").date()
    return out


def _record_snapshot(record):
    keys = [
        "voucher_no",
        "date",
        "site",
        "general_contractor",
        "primary_company",
        "billing_contractor",
        "party_kind",
        "craftsman",
        "company",
        "work_type",
        "work_size",
        "work_amount",
        "remark",
        "size_mark",
        "helpers",
        "helper_count",
        "temoto1",
        "temoto2",
        "temoto3",
        "temoto_percent",
        "shokunin_deduction_percent",
        "unit_price",
        "total_price",
        "price_mode",
        "manual_billing",
        "manual_pay",
        "manual_ouen",
    ]
    return {key: getattr(record, key) for key in keys}


def _save_work_record(fields):
    payload = _deserialize_record_fields(fields)
    _ensure_payload_fits_db(payload)
    return WorkRecord.objects.create(**payload)


def _ensure_payload_fits_db(payload):
    if len(payload.get("work_type") or "") > WORK_TYPE_MAX_LENGTH:
        raise LineOutOfRange(f"作業内容は{WORK_TYPE_MAX_LENGTH}文字までです。")
    if len(payload.get("remark") or "") > REMARK_MAX_LENGTH:
        raise LineOutOfRange(f"備考は{REMARK_MAX_LENGTH}文字までです。")
    if len(payload.get("size_mark") or "") > SIZE_MARK_MAX_LENGTH:
        raise LineOutOfRange(f"印は{SIZE_MARK_MAX_LENGTH}文字までです。")
    if len(payload.get("work_size") or "") > WORK_SIZE_MAX_LENGTH:
        raise LineOutOfRange(f"寸法は{WORK_SIZE_MAX_LENGTH}文字までです。")
    for key in (
        "work_amount",
        "unit_price",
        "total_price",
        "manual_billing",
        "manual_pay",
        "manual_ouen",
    ):
        if not _in_db_int(payload.get(key)):
            raise LineOutOfRange("作業量または金額が、保存できる範囲を超えています。")


def _row_save_fields(row, total_price=None):
    return {
        "work_type": row["type"],
        "work_size": row["size"] or None,
        "work_amount": row["amount"],
        "remark": row.get("remark") or "",
        "size_mark": row.get("size_mark") or "",
        "unit_price": row["unit_price"],
        "total_price": row["total_price"] if total_price is None else total_price,
        "price_mode": row.get("price_mode") or "master",
        "manual_billing": row.get("manual_billing"),
        "manual_pay": row.get("manual_pay"),
        "manual_ouen": row.get("manual_ouen"),
    }


# 昔の伝票は区分に moto などの英語キーを保存している。今の伝票は「元請」などの日本語。
# 一覧と印刷は、どちらも同じ区分として扱う。
KIND_WORKERS = {
    "moto": ["元請", "moto"],
    "shokunin": ["職人", "shokunin"],
    "temoto": ["手元", "temoto"],
    "ouen": ["応援", "ouen"],
}

VOUCHER_HEADER_FIELDS = [
    ("voucher_no", "伝票番号"),
    ("date", "日付"),
    ("site", "現場名"),
    ("general_contractor", "元請"),
    ("primary_company", "1次企業"),
    ("billing_contractor", "請求先"),
    ("party_kind", "区分"),
    ("craftsman", "職人"),
    ("company", "応援企業"),
    ("temoto1", "手元1"),
    ("temoto2", "手元2"),
    ("temoto3", "手元3"),
]

VOUCHER_DETAIL_FIELDS = [
    ("helpers", "手元"),
    ("work_type", "作業内容"),
    ("work_size", "寸法"),
    ("work_amount", "作業量"),
    ("remark", "備考"),
    ("unit_price", "単価"),
    ("total_price", "金額"),
    ("temoto_percent", "手元％"),
]


def _party_kind_text(record):
    return (
        getattr(record, "party_kind", None) or getattr(record, "worker", None) or ""
    ).strip()


def _kind_from_worker(worker):
    worker_label = (worker or "").strip()
    for kind, labels in KIND_WORKERS.items():
        if worker_label in labels:
            return kind
    return None


PARTY_KIND_LABELS = {
    "moto": "元請",
    "shokunin": "職人",
    "temoto": "手元",
    "worker": "作業員",
    "ouen": "応援",
}


def _party_kind_of_record(record):
    kind = _kind_from_worker(_party_kind_text(record))
    if kind:
        return kind
    if (getattr(record, "company", "") or "").strip():
        return "ouen"
    return None


def _party_kind_of_fields(fields):
    kind = _kind_from_worker((fields or {}).get("party_kind"))
    if kind:
        return kind
    if ((fields or {}).get("company") or "").strip():
        return "ouen"
    return None


def _locked_party_kinds(records):
    printed_map = _printed_item_map([r.pk for r in records if getattr(r, "pk", None)])
    kinds = []
    seen = set()
    for rec in records:
        if rec.pk not in printed_map:
            continue
        kind = _party_kind_of_record(rec)
        if kind and kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def _existing_voucher_groups(voucher_no):
    voucher_no = (voucher_no or "").strip()
    titles = [
        ("moto", "元請"),
        ("shokunin", "職人"),
        ("temoto", "手元"),
        ("ouen", "応援"),
    ]
    grouped = {kind: [] for kind, _title in titles}
    if voucher_no:
        for record in WorkRecord.objects.filter(voucher_no=voucher_no).order_by("id"):
            kind = _kind_from_worker(_party_kind_text(record))
            if kind:
                grouped[kind].append(record)

    groups = []
    existing_kinds = set()
    for kind, title in titles:
        records = grouped[kind]
        if not records:
            continue
        existing_kinds.add(kind)
        first = _serialize_record_fields(_record_snapshot(records[0]))
        groups.append(
            {
                "kind": kind,
                "title": title,
                "header_rows": [
                    {"label": label, "value": first.get(key)}
                    for key, label in VOUCHER_HEADER_FIELDS
                ],
                "detail_labels": [label for _key, label in VOUCHER_DETAIL_FIELDS],
                "details": [
                    [
                        _serialize_record_fields(_record_snapshot(record)).get(key)
                        for key, _label in VOUCHER_DETAIL_FIELDS
                    ]
                    for record in records
                ],
                "ids": [record.pk for record in records],
            }
        )
    return groups, existing_kinds


def _apply_voucher_save(action, existing_ids, new_fields):
    """上書きでも、印刷済みの区分の行は消さず、入れ直しもしない。

    印刷した金額は書類側に固定してある。印刷済みの行を消して保存し直すと、
    画面の金額と印刷済み書類がずれる。
    """
    with transaction.atomic():
        _apply_voucher_save_in_transaction(action, existing_ids, new_fields)


def _apply_voucher_save_in_transaction(action, existing_ids, new_fields):
    if action == "overwrite":
        printed_ids = set(_printed_item_map(existing_ids).keys())
        locked_kinds = set()
        if printed_ids:
            for rec in WorkRecord.objects.filter(pk__in=printed_ids):
                kind = _party_kind_of_record(rec)
                if kind:
                    locked_kinds.add(kind)
        WorkRecord.objects.filter(pk__in=existing_ids).exclude(
            pk__in=printed_ids
        ).delete()
        for fields in new_fields:
            kind = _party_kind_of_fields(fields)
            if kind and kind in locked_kinds:
                continue
            _save_work_record(fields)
        return
    if action in ("save", "create"):
        for fields in new_fields:
            _save_work_record(fields)


def _voucher_existing_ids(voucher_no):
    voucher_no = (voucher_no or "").strip()
    if not voucher_no:
        return []
    return list(
        WorkRecord.objects.filter(voucher_no=voucher_no).values_list("pk", flat=True)
    )


def _bulk_save_fields(data):
    sections, _groups = _build_review_sections(data)
    fields = []
    for section in sections:
        if section.get("can_save"):
            fields.extend(section.get("_new_fields") or [])
    return fields


def _voucher_qs(record):
    voucher = (getattr(record, "voucher_no", "") or "").strip()
    qs = WorkRecord.objects.filter(
        date=getattr(record, "date", None),
        site=getattr(record, "site", "") or "",
    )
    if voucher:
        return qs.filter(voucher_no=voucher)
    pk = getattr(record, "pk", None)
    if pk:
        return qs.filter(pk=pk)
    return WorkRecord.objects.none()


def _existing_as_input(voucher_no):
    voucher_no = (voucher_no or "").strip()
    if not voucher_no:
        return None
    records = list(WorkRecord.objects.filter(voucher_no=voucher_no).order_by("id"))
    if not records:
        return None
    return _basic_record_from_records(records)


def _basic_record_from_voucher(record):
    records = list(_voucher_qs(record).order_by("id"))
    if not records:
        records = [record]
    return _basic_record_from_records(records)


def _record_line_key(record):
    return (
        record.work_type or "",
        record.work_size or "",
        record.work_amount,
        record.remark or "",
        getattr(record, "size_mark", "") or "",
    )


def _unit_price_for_role(records, labels, key):
    for record in records:
        if _party_kind_text(record) in labels and _record_line_key(record) == key:
            return getattr(record, "unit_price", None)
    return None


def _linked_master(records, fk_name, model, name):
    for record in records:
        if record is None:
            continue
        pk = getattr(record, f"{fk_name}_id", None)
        if not pk:
            continue
        obj = model.objects.filter(pk=pk).first()
        if obj is not None:
            return obj
    return unique_named(model, name)


def _basic_record_from_records(records):
    first = records[0]
    shokunin = next(
        (
            record
            for record in records
            if _party_kind_text(record) in ("職人", "shokunin")
        ),
        None,
    )
    source = None
    for labels in (
        ("職人", "shokunin"),
        ("元請", "moto"),
        ("応援", "ouen"),
        ("手元", "temoto"),
    ):
        subset = [record for record in records if _party_kind_text(record) in labels]
        if subset:
            source = subset
            break
    source = source or records
    seen = set()
    lines = []
    work_types = []
    work_sizes = []
    work_amounts = []
    remarks = []
    size_marks = []
    price_modes = []
    manual_billings = []
    manual_pays = []
    manual_ouens = []
    rate_billings = []
    rate_pays = []
    rate_ouens = []
    for record in source:
        key = _record_line_key(record)
        if key in seen:
            continue
        seen.add(key)
        size_name = record.work_size or ""
        size_obj = (
            WorkSize.objects.filter(name=size_name).first() if size_name else None
        )
        mode = getattr(record, "price_mode", "") or "master"
        lines.append(
            {
                "work_type": record.work_type,
                "work_size": size_name,
                "work_amount": record.work_amount,
                "remark": record.remark,
                "size_mark": getattr(record, "size_mark", "") or "",
                "price_mode": mode,
            }
        )
        work_types.append(record.work_type or "")
        work_sizes.append(str(size_obj.pk) if size_obj else "")
        work_amounts.append(record.work_amount)
        remarks.append(record.remark or "")
        size_marks.append(getattr(record, "size_mark", "") or "")
        price_modes.append(mode)
        manual_billings.append(getattr(record, "manual_billing", None))
        manual_pays.append(getattr(record, "manual_pay", None))
        manual_ouens.append(getattr(record, "manual_ouen", None))
        rate_billings.append(_unit_price_for_role(records, ("元請", "moto"), key))
        rate_pays.append(_unit_price_for_role(records, ("職人", "shokunin"), key))
        rate_ouens.append(_unit_price_for_role(records, ("応援", "ouen"), key))
    worker_name = ""
    if shokunin:
        worker_name = (
            getattr(shokunin, "craftsman", "") or getattr(shokunin, "helpers", "") or ""
        )
    else:
        worker_name = getattr(first, "craftsman", "") or ""
    worker = _linked_master(
        [shokunin, *records], "craftsman_worker", Worker, worker_name
    )
    site = _linked_master(records, "site_master", Site, first.site)
    company = _linked_master(records, "company_master", Company, first.company)
    primary = _linked_master(
        records, "primary_master", GeneralContractor, first.primary_company
    )
    billing = _linked_master(
        records, "billing_master", GeneralContractor, first.billing_contractor
    )
    contractor = _linked_master(
        records, "contractor_master", GeneralContractor, first.general_contractor
    )
    if site is not None and site.general_contractor_id:
        general_contractor_id = site.general_contractor_id
    else:
        general_contractor_id = contractor.pk if contractor else None
    temoto_ids = {}
    for key in HELPER_FIELDS:
        name = ""
        for record in records:
            name = getattr(record, key, "") or name
        person = _linked_master(records, f"{key}_worker", Worker, name)
        temoto_ids[f"{key}_id"] = person.pk if person else None
    return {
        "voucher_no": first.voucher_no,
        "date": first.date,
        "site": first.site,
        "site_id": site.pk if site else None,
        "general_contractor": first.general_contractor,
        "general_contractor_id": general_contractor_id,
        "primary_company": getattr(first, "primary_company", "") or "",
        "primary_company_id": primary.pk if primary else None,
        "billing_contractor": getattr(first, "billing_contractor", "")
        or first.general_contractor,
        "billing_contractor_id": billing.pk if billing else general_contractor_id,
        "worker": worker.name if worker else worker_name,
        "worker_id": worker.pk if worker else None,
        "temoto1": first.temoto1,
        "temoto2": first.temoto2,
        "temoto3": first.temoto3,
        "temoto1_id": temoto_ids["temoto1_id"],
        "temoto2_id": temoto_ids["temoto2_id"],
        "temoto3_id": temoto_ids["temoto3_id"],
        "company": first.company,
        "company_id": company.pk if company else None,
        "lines": lines,
        "work_types": work_types,
        "work_sizes": work_sizes,
        "work_amounts": work_amounts,
        "remarks": remarks,
        "size_marks": size_marks,
        "price_modes": price_modes,
        "manual_billings": manual_billings,
        "manual_pays": manual_pays,
        "manual_ouens": manual_ouens,
        "rate_billings": rate_billings,
        "rate_pays": rate_pays,
        "rate_ouens": rate_ouens,
        "helper": "、".join(
            [name for name in (first.temoto1, first.temoto2, first.temoto3) if name]
        ),
        "helper_count": len(
            [name for name in (first.temoto1, first.temoto2, first.temoto3) if name]
        ),
    }


def _serialize_session_basic(data):
    out = dict(data)
    date_value = out.get("date")
    if hasattr(date_value, "isoformat"):
        out["date"] = date_value.isoformat()
    return out


def _mark_voucher_actions(records):
    seen = set()
    for rec in records:
        key = (
            str(getattr(rec, "voucher_no", "") or ""),
            getattr(rec, "date", None),
            str(getattr(rec, "site", "") or ""),
        )
        rec.show_voucher_actions = key not in seen
        seen.add(key)
    return records


def _try_resolve_worker(data):
    if not data.get("worker_id") and not str(data.get("worker") or "").strip():
        return None
    try:
        return _resolve_worker(data)
    except Worker.DoesNotExist, ValueError, TypeError:
        return None


def _moto_rate_contractor(data):
    for key in ("billing_contractor_id", "general_contractor_id"):
        pk = data.get(key)
        if not pk:
            continue
        contractor = GeneralContractor.objects.filter(pk=pk).first()
        if contractor is not None:
            return contractor
    billing = (data.get("billing_contractor") or "").strip()
    site_contractor = (data.get("general_contractor") or "").strip()
    contractor = unique_named(GeneralContractor, billing) if billing else None
    if contractor is None and site_contractor:
        contractor = unique_named(GeneralContractor, site_contractor)
    return contractor


def _moto_work_rows(data):
    contractor = _moto_rate_contractor(data)
    work_rows = []
    for line in _iter_work_lines(data):
        size_obj, _row = _line_base(line)
        unit_price = None
        if contractor and size_obj:
            rate = GeneralContractorRate.objects.filter(
                general_contractor=contractor,
                work_size=size_obj,
            ).first()
            if rate:
                unit_price = rate.unit_price
        _size, row = _priced_work_row(
            line, unit_price, line["manual_billing"], line.get("rate_billing")
        )
        work_rows.append(row)
    allocation = _temoto_allocation_from_data(data)
    totals = _sum_line_totals(work_rows, allocation)
    return work_rows, totals, allocation


def _shokunin_work_rows(data, worker):
    work_rows = []
    for line in _iter_work_lines(data):
        size_obj, _row = _line_base(line)
        unit_price = _worker_unit_price(worker, size_obj) if worker else None
        _size, row = _priced_work_row(
            line, unit_price, line["manual_pay"], line.get("rate_pay")
        )
        work_rows.append(row)
    allocation = _temoto_allocation_from_data(data)
    totals = _with_temoto_deduction(work_rows, allocation)
    return work_rows, totals, allocation


def _temoto_work_rows(data, worker):
    allocation = _temoto_allocation_from_data(data)
    shares = allocation["shares"]
    if not shares:
        shares = [{"name": "手元", "percent": 0, "raw_percent": None}]
        allocation = {**allocation, "count": 1, "shares": shares}
    work_rows = []
    for line in _iter_work_lines(data):
        size_obj, _row = _line_base(line)
        unit_price = _worker_unit_price(worker, size_obj) if worker else None
        _size, priced = _priced_work_row(
            line, unit_price, line["manual_pay"], line.get("rate_pay")
        )
        shokunin_total = priced["total_price"]
        person_amounts = _temoto_line_amounts(shokunin_total, allocation)
        work_rows.append(
            {
                **priced,
                "line_total": shokunin_total,
                "total_price": sum(a or 0 for a in person_amounts)
                if shokunin_total is not None
                else None,
                "person_amounts": person_amounts,
                "person_shares": [
                    {
                        "name": share["name"],
                        "percent": share["percent"],
                        "raw_percent": share.get("raw_percent"),
                        "amount": person_amounts[index],
                    }
                    for index, share in enumerate(shares)
                ],
            }
        )
    person_totals = []
    for index, share in enumerate(shares):
        person_totals.append(
            {
                "name": share["name"],
                "percent": share["percent"],
                "raw_percent": share.get("raw_percent"),
                "amount": sum((row["person_amounts"][index] or 0) for row in work_rows),
            }
        )
    totals = {
        "total": sum(row["total_price"] or 0 for row in work_rows),
        "shokunin_subtotal": sum(row["line_total"] or 0 for row in work_rows),
        "temoto_mode": allocation["mode"],
        "helper_count": allocation["count"],
        "person_totals": person_totals,
    }
    return work_rows, totals, allocation, shares


def _ouen_work_rows(data):
    work_rows = []
    company_name = str(data.get("company") or "").strip()
    company = None
    if data.get("company_id"):
        company = Company.objects.filter(pk=data.get("company_id")).first()
    if company is None and company_name:
        company = unique_named(Company, company_name)
    for line in _iter_work_lines(data):
        size_obj, _row = _line_base(line)
        unit_price = OUEN_UNIT
        if company and size_obj:
            rate = CompanyRate.objects.filter(
                company=company, work_size=size_obj
            ).first()
            if rate:
                unit_price = rate.unit_price
        _size, row = _priced_work_row(
            line, unit_price, line["manual_ouen"], line.get("rate_ouen")
        )
        work_rows.append(row)
    allocation = _temoto_allocation_from_data(data)
    totals = _sum_line_totals(work_rows, allocation)
    return work_rows, totals, allocation


def _section_state(kind, title, common, lines, display_rows, totals, missing=""):
    serialized = (
        [_serialize_record_fields({**common, **line}) for line in lines]
        if lines
        else []
    )
    return {
        "kind": kind,
        "title": title,
        "missing": missing,
        "rows": display_rows,
        "totals": totals,
        "has_existing": False,
        "can_save": bool(lines) and not missing,
        "_new_fields": serialized,
        "_existing_ids": [],
    }


def _build_review_sections(data):
    worker = _try_resolve_worker(data)
    has_lines = bool(data.get("work_types"))
    temoto_selected = any(str(data.get(key) or "").strip() for key in HELPER_FIELDS)
    company_selected = bool(str(data.get("company") or "").strip())
    gc_selected = bool(str(data.get("general_contractor") or "").strip())

    moto_rows, moto_totals, moto_alloc = _moto_work_rows(data)
    shokunin_rows, shokunin_totals, shokunin_alloc = _shokunin_work_rows(data, worker)
    temoto_rows, temoto_totals, temoto_alloc, shares = _temoto_work_rows(data, worker)
    ouen_rows, ouen_totals, ouen_alloc = _ouen_work_rows(data)

    temoto_lines = []
    temoto_display = []
    for index, share in enumerate(shares):
        for row in temoto_rows:
            fields = _row_save_fields(row, total_price=row["person_amounts"][index])
            fields["helpers"] = share["name"]
            fields["temoto_percent"] = share["percent"] or None
            temoto_lines.append(fields)
            temoto_display.append(
                {
                    "type": row["type"],
                    "size": row["size"],
                    "amount": row["amount"],
                    "remark": row.get("remark") or "",
                    "size_mark": row.get("size_mark") or "",
                    "helper": share["name"],
                    "percent": share["percent"],
                    "unit_price": row["unit_price"],
                    "total_price": row["person_amounts"][index],
                }
            )

    sections = [
        _section_state(
            "moto",
            "元請",
            _record_common_fields(data, role="moto", allocation=moto_alloc),
            [_row_save_fields(row) for row in moto_rows],
            moto_rows,
            moto_totals,
            ""
            if gc_selected and has_lines
            else ("作業内容がありません。" if not has_lines else "元請が未選択です。"),
        ),
        _section_state(
            "shokunin",
            "職人",
            _record_common_fields(data, role="shokunin", allocation=shokunin_alloc),
            [_row_save_fields(row) for row in shokunin_rows] if worker else [],
            shokunin_rows if worker else [],
            shokunin_totals,
            "" if not worker else ("" if has_lines else "作業内容がありません。"),
        ),
        _section_state(
            "temoto",
            "手元",
            _record_common_fields(data, role="temoto", allocation=temoto_alloc),
            temoto_lines if worker and temoto_selected else [],
            temoto_display if worker and temoto_selected else [],
            temoto_totals,
            ""
            if not temoto_selected
            else (
                "作業内容がありません。"
                if not has_lines
                else ("職人が未選択です。" if not worker else "")
            ),
        ),
        _section_state(
            "ouen",
            "応援",
            _record_common_fields(data, role="ouen", allocation=ouen_alloc),
            [_row_save_fields(row) for row in ouen_rows],
            ouen_rows,
            ouen_totals,
            ""
            if company_selected and has_lines
            else (
                "作業内容がありません。" if not has_lines else "応援企業が未選択です。"
            ),
        ),
    ]
    existing_groups, existing_kinds = _existing_voucher_groups(data.get("voucher_no"))
    for section in sections:
        section["has_existing"] = section["kind"] in existing_kinds
        match = next(
            (group for group in existing_groups if group["kind"] == section["kind"]),
            None,
        )
        section["_existing_ids"] = match["ids"] if match else []
    for group in existing_groups:
        new_section = next(
            (section for section in sections if section["kind"] == group["kind"]), None
        )
        group["can_apply"] = bool(new_section and new_section["can_save"])
    return sections, existing_groups


def _work_lines_from_session(data):
    types = data.get("work_types") or []
    sizes = data.get("work_sizes") or []
    amounts = data.get("work_amounts") or []
    remarks = data.get("remarks") or []
    size_marks = data.get("size_marks") or []
    modes = data.get("price_modes") or []
    billings = data.get("manual_billings") or []
    pays = data.get("manual_pays") or []
    ouens = data.get("manual_ouens") or []
    rate_billings = data.get("rate_billings") or []
    rate_pays = data.get("rate_pays") or []
    rate_ouens = data.get("rate_ouens") or []
    lines = []
    for i in range(MAX_WORK_LINES):
        amount = amounts[i] if i < len(amounts) else ""
        billing = billings[i] if i < len(billings) else ""
        pay = pays[i] if i < len(pays) else ""
        ouen = ouens[i] if i < len(ouens) else ""
        rate_billing = rate_billings[i] if i < len(rate_billings) else ""
        rate_pay = rate_pays[i] if i < len(rate_pays) else ""
        rate_ouen = rate_ouens[i] if i < len(rate_ouens) else ""
        lines.append(
            {
                "work_type": types[i] if i < len(types) else "",
                "work_size": str(sizes[i])
                if i < len(sizes) and sizes[i] not in (None, "")
                else "",
                "work_amount": "" if amount in (None, "") else amount,
                "remark": remarks[i] if i < len(remarks) else "",
                "size_mark": size_marks[i] if i < len(size_marks) else "",
                "price_mode": modes[i] if i < len(modes) else "master",
                "manual_billing": "" if billing in (None, "") else billing,
                "manual_pay": "" if pay in (None, "") else pay,
                "manual_ouen": "" if ouen in (None, "") else ouen,
                "rate_billing": "" if rate_billing in (None, "") else rate_billing,
                "rate_pay": "" if rate_pay in (None, "") else rate_pay,
                "rate_ouen": "" if rate_ouen in (None, "") else rate_ouen,
            }
        )
    return lines


def _id_from_name(model, name):
    obj = unique_named(model, name)
    return obj.pk if obj else None


def _form_from_basic_record(data):
    initial = {
        "voucher_no": data.get("voucher_no") or "",
        "date": data.get("date") or "",
    }
    site_id = data.get("site_id") or _id_from_name(Site, data.get("site"))
    company_id = data.get("company_id") or _id_from_name(Company, data.get("company"))
    if site_id:
        initial["site"] = site_id
    primary_id = data.get("primary_company_id") or _id_from_name(
        GeneralContractor, data.get("primary_company")
    )
    if primary_id:
        initial["primary_company"] = primary_id
    if data.get("worker_id"):
        initial["worker"] = data["worker_id"]
    else:
        worker_id = _id_from_name(Worker, data.get("worker"))
        if worker_id:
            initial["worker"] = worker_id
    if company_id:
        initial["company"] = company_id
    for key in HELPER_FIELDS:
        pk = data.get(f"{key}_id") or _id_from_name(Worker, data.get(key))
        if pk:
            initial[key] = pk
    return WorkRecordForm(initial=initial)


def _parse_search_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _apply_party_search(qs, party):
    raw = str(party or "").strip()
    if not raw or "-" not in raw:
        return qs
    kind, pk = raw.split("-", 1)
    if kind in ("shokunin", "temoto", "worker"):
        person = Worker.objects.filter(pk=pk).first()
        if person is None:
            return qs.none()
        name = person.name
        return qs.filter(
            Q(party_kind=name)
            | Q(craftsman=name)
            | Q(helpers=name)
            | Q(temoto1=name)
            | Q(temoto2=name)
            | Q(temoto3=name)
        )
    if kind == "ouen":
        company = Company.objects.filter(pk=pk).first()
        if company is None:
            return qs.none()
        return qs.filter(
            Q(company=company.name) | Q(party_kind__in=["応援", "ouen", company.name])
        )
    return qs


def _voucher_search_groups(records):
    from collections import OrderedDict

    groups = OrderedDict()
    for rec in records:
        key = (rec.voucher_no or "", rec.date, rec.site or "")
        if key not in groups:
            groups[key] = {
                "pk": rec.pk,
                "voucher_no": rec.voucher_no or "（未設定）",
                "date": rec.date,
                "site": rec.site,
                "general_contractor": rec.general_contractor,
                "primary_company": rec.primary_company or "",
                "billing_contractor": rec.billing_contractor or rec.general_contractor,
                "parties": [],
                "line_count": 0,
            }
        group = groups[key]
        group["line_count"] += 1
        for label in (
            rec.party_kind,
            rec.craftsman,
            rec.company,
            rec.temoto1,
            rec.temoto2,
            rec.temoto3,
        ):
            text = str(label or "").strip()
            if text and text not in group["parties"]:
                group["parties"].append(text)
    return list(groups.values())


def workrecord_search(request):
    date_str = request.GET.get("date", "").strip()
    site_id = request.GET.get("site", "").strip()
    party = request.GET.get("party", "").strip()
    voucher_no = request.GET.get("voucher_no", "").strip()
    submitted = bool(date_str or site_id or party or voucher_no)
    error = ""
    groups = []
    selected_site = None
    if site_id.isdigit():
        selected_site = Site.objects.filter(pk=int(site_id)).first()

    if submitted:
        qs = WorkRecord.objects.all()
        parsed_date = _parse_search_date(date_str) if date_str else None
        if date_str and parsed_date is None:
            error = "日付の形式が正しくありません。"
        else:
            if parsed_date:
                qs = qs.filter(date=parsed_date)
            if site_id:
                if selected_site is None:
                    qs = qs.none()
                else:
                    qs = qs.filter(
                        Q(site_master=selected_site)
                        | Q(site_master__isnull=True, site=selected_site.name)
                    )
            if voucher_no:
                qs = qs.filter(voucher_no__icontains=voucher_no)
            qs = _apply_party_search(qs, party)
            groups = _voucher_search_groups(qs.order_by("-date", "voucher_no", "id"))

    workers = list(Worker.objects.order_by("name"))
    companies = list(Company.objects.all().order_by("name"))
    sites = list(Site.objects.order_by("name"))
    for worker in workers:
        worker.party_key = f"worker-{worker.pk}"
    for company in companies:
        company.party_key = f"ouen-{company.pk}"

    return render(
        request,
        "workapp/workrecord_search.html",
        {
            "date": date_str,
            "site": site_id,
            "party": party,
            "voucher_no": voucher_no,
            "submitted": submitted,
            "error": error,
            "groups": groups,
            "workers": workers,
            "companies": companies,
            "sites": sites,
        },
    )


def workrecord_voucher_detail(request, pk):
    base = get_object_or_404(WorkRecord, pk=pk)
    records = WorkRecord.objects.filter(
        voucher_no=base.voucher_no,
        date=base.date,
        site=base.site,
    ).order_by("id")
    return render(
        request,
        "workapp/workrecord_voucher_detail.html",
        {
            "base": base,
            "records": records,
        },
    )


def workrecord_create(request):
    work_lines = _work_lines_from_session({})
    unit_role = ""
    if request.method == "POST":
        form = WorkRecordForm(request.POST)
        work_lines = []
        for i in range(1, MAX_WORK_LINES + 1):
            work_lines.append(
                {
                    "work_type": request.POST.get(f"work_type_{i}") or "",
                    "work_size": request.POST.get(f"work_size_{i}") or "",
                    "work_amount": request.POST.get(f"work_amount_{i}") or "",
                    "remark": request.POST.get(f"remark_{i}") or "",
                    "size_mark": request.POST.get(f"size_mark_{i}") or "",
                    "price_mode": request.POST.get(f"price_mode_{i}") or "master",
                    "manual_billing": request.POST.get(f"manual_billing_{i}") or "",
                    "manual_pay": request.POST.get(f"manual_pay_{i}") or "",
                    "manual_ouen": request.POST.get(f"manual_ouen_{i}") or "",
                    "rate_billing": request.POST.get(f"rate_billing_{i}") or "",
                    "rate_pay": request.POST.get(f"rate_pay_{i}") or "",
                    "rate_ouen": request.POST.get(f"rate_ouen_{i}") or "",
                }
            )

        if form.is_valid():
            record = form.save(commit=False)

            voucher_no = record.voucher_no
            date = record.date
            worker = form.cleaned_data.get("worker")
            temoto_names, temoto_ids, temoto_fields = _collect_temoto_from_form(form)
            company = form.cleaned_data.get("company")
            site_obj = form.cleaned_data.get("site")
            gc_name = form.cleaned_data.get("general_contractor_name") or ""
            primary_name = form.cleaned_data.get("primary_company_name") or ""
            billing_name = form.cleaned_data.get("billing_contractor_name") or gc_name
            primary_obj = form.cleaned_data.get("primary_company")

            posted_lines = _collect_posted_work_lines(request.POST)
            work_types = posted_lines["work_types"]
            work_sizes = posted_lines["work_sizes"]
            work_amounts = posted_lines["work_amounts"]
            remarks = posted_lines["remarks"]
            size_marks = posted_lines["size_marks"]
            price_modes = posted_lines["price_modes"]
            manual_billings = posted_lines["manual_billings"]
            manual_pays = posted_lines["manual_pays"]
            manual_ouens = posted_lines["manual_ouens"]
            rate_billings = posted_lines["rate_billings"]
            rate_pays = posted_lines["rate_pays"]
            rate_ouens = posted_lines["rate_ouens"]

            if posted_lines["type_missing"] or not work_types:
                form.add_error(None, "作業内容を1行以上入力してください。")
            elif posted_lines["manual_missing"]:
                form.add_error(
                    None, "手入力の行は、単価か金額のいずれかを入力してください。"
                )
            elif posted_lines["line_error"]:
                form.add_error(None, posted_lines["line_error"])
            else:
                request.session["basic_record"] = {
                    "voucher_no": voucher_no,
                    "date": str(date),
                    "site": site_obj.name if site_obj else "",
                    "site_id": site_obj.pk if site_obj else None,
                    "general_contractor": gc_name,
                    "general_contractor_id": site_obj.general_contractor_id
                    if site_obj
                    else None,
                    "primary_company": primary_name,
                    "primary_company_id": primary_obj.pk if primary_obj else None,
                    "billing_contractor": billing_name,
                    "billing_contractor_id": (
                        primary_obj.pk
                        if primary_obj
                        else (site_obj.general_contractor_id if site_obj else None)
                    ),
                    "worker": worker.name if worker else "",
                    "worker_id": worker.pk if worker else None,
                    "helper": "、".join(temoto_names),
                    "helper_count": len(temoto_names),
                    "temoto1": temoto_fields.get("temoto1") or "",
                    "temoto2": temoto_fields.get("temoto2") or "",
                    "temoto3": temoto_fields.get("temoto3") or "",
                    "temoto1_id": temoto_fields.get("temoto1_id"),
                    "temoto2_id": temoto_fields.get("temoto2_id"),
                    "temoto3_id": temoto_fields.get("temoto3_id"),
                    "company": company.name if company else "",
                    "company_id": company.pk if company else None,
                    "work_types": work_types,
                    "work_sizes": work_sizes,
                    "work_amounts": work_amounts,
                    "remarks": remarks,
                    "size_marks": size_marks,
                    "price_modes": price_modes,
                    "manual_billings": manual_billings,
                    "manual_pays": manual_pays,
                    "manual_ouens": manual_ouens,
                    "rate_billings": rate_billings,
                    "rate_pays": rate_pays,
                    "rate_ouens": rate_ouens,
                }
                existing_ids = _voucher_existing_ids(voucher_no)
                try:
                    payloads = _bulk_save_fields(request.session["basic_record"])
                    if existing_ids:
                        request.session["review_saved"] = False
                    else:
                        _apply_voucher_save("save", [], payloads)
                        request.session["review_saved"] = True
                except LineOutOfRange as exc:
                    form.add_error(None, str(exc))
                else:
                    return redirect("workrecord_review")

    else:
        data = (
            request.session.get("basic_record") if request.GET.get("restore") else None
        )
        if data:
            form = _form_from_basic_record(data)
            work_lines = _work_lines_from_session(data)
            unit_role = data.get("unit_role") or ""
        else:
            request.session.pop("basic_record", None)
            request.session.pop("review_saved", None)
            request.session.pop("editing_voucher", None)
            request.session.pop("locked_party_labels", None)
            form = WorkRecordForm()
            work_lines = _work_lines_from_session({})
            unit_role = ""

    locked_party_labels = request.session.get("locked_party_labels") or []
    line_error = request.session.pop("line_error", "")
    if line_error:
        form.add_error(None, line_error)
    return render(
        request,
        "workapp/workrecord_basic_form.html",
        {
            "form": form,
            "work_sizes": WorkSize.objects.all(),
            "work_lines": work_lines,
            "unit_role": unit_role,
            "editing_voucher": bool(request.session.get("editing_voucher")),
            "locked_party_labels": locked_party_labels,
            "list_qs": urlencode(
                _list_filter_params(request)
                or (request.session.get("list_filter") or {})
            ),
        },
    )


def workrecord_review(request):
    data = request.session.get("basic_record")
    if not data:
        return redirect("workrecord_create")

    existing_ids = _voucher_existing_ids(data.get("voucher_no"))
    already_saved = bool(request.session.get("review_saved"))

    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("cancel", "back"):
            request.session["review_saved"] = False
            return redirect(reverse("workrecord_create") + "?restore=1")
        try:
            payloads = _bulk_save_fields(data)
            if action == "overwrite":
                _apply_voucher_save("overwrite", existing_ids, payloads)
                request.session["review_saved"] = True
            elif action == "create":
                _apply_voucher_save("create", [], payloads)
                request.session["review_saved"] = True
        except LineOutOfRange as exc:
            request.session["line_error"] = str(exc)
            return redirect(reverse("workrecord_create") + "?restore=1")
        return redirect("workrecord_review")

    needs_confirm = bool(existing_ids) and not already_saved
    sections, _groups = _build_review_sections(data)
    return render(
        request,
        "workapp/workrecord_review.html",
        {
            "data": data,
            "sections": sections,
            "existing": _existing_as_input(data.get("voucher_no"))
            if needs_confirm
            else None,
            "needs_confirm": needs_confirm,
            "saved": already_saved,
        },
    )


def _classify_record(record):
    worker_label = _party_kind_text(record)
    if worker_label == "元請":
        return (
            "元請",
            record.billing_contractor or record.general_contractor or "（未設定）",
        )
    if worker_label in ("職人", "shokunin"):
        return "職人", getattr(
            record, "craftsman", ""
        ) or record.helpers or worker_label
    if worker_label in ("手元", "temoto"):
        return "手元", record.helpers or worker_label
    if worker_label == "応援":
        return "応援", record.company or "（未設定）"
    if record.company:
        return "応援", record.company
    if record.billing_contractor or record.general_contractor:
        return "元請", record.billing_contractor or record.general_contractor
    return "職人", worker_label or "（未設定）"


def _party_records(kind, contractor=None, worker=None, company=None):
    if kind == "moto" and contractor:
        name = contractor.name
        return WorkRecord.objects.filter(
            party_kind__in=["元請", "moto"],
        ).filter(
            Q(billing_master=contractor)
            | Q(billing_master__isnull=True, billing_contractor=name)
            | Q(
                billing_master__isnull=True,
                billing_contractor="",
                general_contractor=name,
            )
        )
    if kind == "shokunin" and worker:
        return WorkRecord.objects.filter(
            Q(craftsman_worker=worker)
            | Q(
                craftsman_worker__isnull=True,
                party_kind__in=["職人", "shokunin"],
                craftsman=worker.name,
            )
            | Q(
                craftsman_worker__isnull=True,
                party_kind__in=["職人", "shokunin"],
                helpers=worker.name,
            )
            | Q(craftsman_worker__isnull=True, party_kind=worker.name)
        )
    if kind == "temoto" and worker:
        return WorkRecord.objects.filter(
            Q(temoto1_worker=worker)
            | Q(temoto2_worker=worker)
            | Q(temoto3_worker=worker)
            | Q(
                temoto1_worker__isnull=True,
                party_kind__in=["手元", "temoto"],
                helpers=worker.name,
            )
            | Q(temoto1_worker__isnull=True, temoto1=worker.name)
            | Q(temoto2_worker__isnull=True, temoto2=worker.name)
            | Q(temoto3_worker__isnull=True, temoto3=worker.name)
        )
    if kind == "worker" and worker:
        return (
            _party_records("shokunin", worker=worker)
            | _party_records("temoto", worker=worker)
        ).distinct()
    if kind == "ouen" and company:
        return WorkRecord.objects.filter(
            party_kind__in=["応援", "ouen", company.name],
        ).filter(
            Q(company_master=company)
            | Q(company_master__isnull=True, company=company.name)
        )
    return WorkRecord.objects.none()


def _grouped_sections(records, kind=None, group_name=None):
    from collections import defaultdict

    buckets = {
        "元請": defaultdict(list),
        "職人": defaultdict(list),
        "手元": defaultdict(list),
        "応援": defaultdict(list),
    }
    for rec in records:
        rec_kind, name = _classify_record(rec)
        buckets[rec_kind][name].append(rec)

    def to_groups(bucket):
        groups = []
        for name, recs in bucket.items():
            groups.append(
                {
                    "name": name,
                    "records": recs,
                    "total": sum(r.total_price or 0 for r in recs),
                }
            )
        groups.sort(key=lambda g: g["name"])
        return groups

    titles = [
        ("元請", "1. 元請会社ごとの一覧"),
        ("職人", "2. 職人個人ごとの一覧"),
        ("手元", "3. 手元個人ごとの一覧"),
        ("応援", "4. 応援会社ごとの一覧"),
    ]
    if kind == "moto":
        titles = [("元請", "元請の一覧")]
    elif kind == "shokunin":
        titles = [("職人", "職人の一覧")]
    elif kind == "temoto":
        titles = [("手元", "手元の一覧")]
    elif kind == "worker":
        titles = [("作業員", "作業員の一覧")]
    elif kind == "ouen":
        titles = [("応援", "応援の一覧")]

    sections = []
    for section_kind, title in titles:
        groups = to_groups(buckets[section_kind])
        if group_name:
            groups = [g for g in groups if g["name"] == group_name]
        sections.append(
            {
                "kind": section_kind,
                "title": title,
                "groups": groups,
                "total": sum(g["total"] for g in groups),
            }
        )
    return sections


LIST_FILTER_KEYS = ("kind", "moto_company", "worker_id", "temoto_id", "company_id")

DOCUMENT_TITLES = {
    "moto": "請求書",
    "worker": "支払明細",
    "shokunin": "支払明細",
    "temoto": "支払明細",
    "ouen": "支払明細（応援）",
}


def _print_text(record, key):
    if isinstance(record, dict):
        value = record.get(key)
    else:
        value = getattr(record, key, None)
    return str(value or "").strip()


def _print_site_key(record):
    site = _print_text(record, "site")
    contractor_name = _print_text(record, "general_contractor")
    if site and contractor_name:
        return f"{site}（{contractor_name}）"
    return site or contractor_name


def _print_upper_key(record):
    return _print_text(record, "primary_company") or _print_text(
        record, "general_contractor"
    )


def _annotate_print_repeats(records):
    prev = None
    for rec in records:
        voucher = _print_text(rec, "voucher_no")
        rec_date = getattr(rec, "date", None)
        site = _print_site_key(rec)
        upper = _print_upper_key(rec)
        work = _print_text(rec, "work_type")
        same_voucher = bool(prev is not None and voucher and voucher == prev[0])
        rec.print_show_voucher = not same_voucher
        rec.print_show_date = not (same_voucher and rec_date == prev[1])
        rec.print_show_site = not (same_voucher and site == prev[2])
        rec.print_show_upper = not (same_voucher and upper == prev[3])
        rec.print_show_work = not (same_voucher and work == prev[4])
        prev = (voucher, rec_date, site, upper, work)
    return records


def _print_party_context(kind, party_name=""):
    name = (party_name or "").strip()
    company_kinds = ("moto", "ouen")
    return {
        "party_name": name,
        "party_honorific": "御中" if kind in company_kinds else "様",
        "party_role_label": "請求先" if kind == "moto" else "支払先",
    }


def _wants_kagami(src, kind):
    if kind in ("moto", "ouen"):
        return False
    return str((src or {}).get("kagami") or "").strip() in ("1", "on", "true")


def _next_month_end_label(end_date):
    if end_date in (None, ""):
        return ""
    if isinstance(end_date, str):
        try:
            end_date = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return ""
    month = end_date.month + 1
    year = end_date.year
    if month == 13:
        month = 1
        year += 1
    return f"{year}年{month}月末"


def _pay_tax_context(total):
    base = int(total or 0)
    tax = _amount_at_percent(base, CONSUMPTION_TAX_PERCENT)
    return {
        "show_tax_block": True,
        "pay_total_num": base,
        "pay_total": f"{base:,}",
        "pay_tax": f"{tax:,}",
        "pay_tax_included": f"{base + tax:,}",
    }


KAGAMI_LINE_COUNT = 10


def _parse_kagami_amount(value):
    text = (
        str(value or "")
        .replace(",", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("円", "")
        .replace("¥", "")
        .replace("￥", "")
        .strip()
    )
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _kagami_lines_from_src(src):
    lines = []
    total = 0
    for index in range(1, KAGAMI_LINE_COUNT + 1):
        item = str((src or {}).get(f"kagami_item_{index}") or "").strip()
        if index == 1:
            item = "売上"
        amount = _parse_kagami_amount((src or {}).get(f"kagami_amount_{index}"))
        lines.append({"item": item, "amount": amount})
        total += amount or 0
    return lines, total


def _default_kagami_lines(pay_total):
    amount = int(pay_total or 0)
    lines = [{"item": "売上", "amount": amount}]
    lines.extend({"item": "", "amount": None} for _ in range(KAGAMI_LINE_COUNT - 1))
    return lines, amount


def _kagami_period_scope(kind, worker_id, company_id, from_date, to_date):
    return (
        f"period:{kind}:w={worker_id or ''}:c={company_id or ''}:{from_date}:{to_date}"
    )


def _kagami_voucher_scope(kind, record_pk, worker_id):
    return f"voucher:{kind}:{record_pk}:w={worker_id or ''}"


def _kagami_context(scope_key, pay_total):
    sheet = KagamiSheet.objects.filter(scope_key=scope_key).first()
    if sheet:
        lines = list(sheet.lines or [])
        total = int(sheet.total_amount or 0)
        while len(lines) < KAGAMI_LINE_COUNT:
            lines.append({"item": "", "amount": None})
        lines = lines[:KAGAMI_LINE_COUNT]
        if lines:
            lines[0]["item"] = "売上"
    else:
        lines, total = _default_kagami_lines(pay_total)
    tax = _pay_tax_context(total)
    return {
        "kagami_lines": lines,
        "kagami_total": total,
        "kagami_pay_total": tax["pay_total"],
        "kagami_pay_tax": tax["pay_tax"],
        "kagami_pay_tax_included": tax["pay_tax_included"],
    }


def _save_kagami_sheet(scope_key, src):
    lines, total = _kagami_lines_from_src(src)
    KagamiSheet.objects.update_or_create(
        scope_key=scope_key,
        defaults={"lines": lines, "total_amount": total},
    )


def _single_print_party_name(kind, record, worker_id=""):
    if kind == "moto":
        return (
            (getattr(record, "billing_contractor", "") or "")
            or (getattr(record, "general_contractor", "") or "")
        ).strip()
    if kind == "ouen":
        return (getattr(record, "company", "") or "").strip()
    if worker_id:
        person = Worker.objects.filter(pk=worker_id).first()
        if person:
            return person.name
    return (
        (getattr(record, "craftsman", "") or "")
        or (getattr(record, "helpers", "") or "")
        or _party_kind_text(record)
    ).strip()


def _party_choice_querysets():
    contractors = GeneralContractor.objects.all().order_by("name")
    workers = Worker.objects.all().order_by("name")
    companies = Company.objects.all().order_by("name")
    return contractors, workers, companies


def _normalize_party_kind(kind, worker_id, temoto_id):
    kind = str(kind or "").strip()
    worker_id = str(worker_id or "").strip()
    temoto_id = str(temoto_id or "").strip()
    if kind in ("shokunin", "temoto", "worker"):
        lookup = worker_id or temoto_id
        return "worker", lookup, ""
    return kind, worker_id, temoto_id


def _list_filter_params(request=None, extra=None):
    params = {}
    source = extra or {}
    if request is not None:
        source = {**source, **request.GET.dict(), **request.POST.dict()}
    for key in LIST_FILTER_KEYS:
        val = str(source.get(key) or "").strip()
        if val:
            params[key] = val
    return params


def _list_url(params=None):
    url = reverse("workrecord_list")
    if params:
        return f"{url}?{urlencode(params)}"
    return url


def _redirect_to_list(request):
    params = _list_filter_params(request) or (request.session.get("list_filter") or {})
    params = {key: val for key, val in params.items() if val}
    return redirect(_list_url(params))


SNAPSHOT_FIELDS = (
    "id",
    "voucher_no",
    "date",
    "site",
    "work_type",
    "work_size",
    "work_amount",
    "remark",
    "size_mark",
    "general_contractor",
    "primary_company",
    "billing_contractor",
    "party_kind",
    "craftsman",
    "company",
    "helpers",
    "temoto1",
    "temoto2",
    "temoto3",
    "helper_count",
    "temoto_percent",
    "shokunin_deduction_percent",
    "unit_price",
    "total_price",
    "price_mode",
    "manual_billing",
    "manual_pay",
    "manual_ouen",
)


def _record_print_snapshot(record):
    data = {}
    for key in SNAPSHOT_FIELDS:
        value = getattr(record, key, None)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        data[key] = value
    return data


def _line_from_snapshot(item):
    snap = dict(item.snapshot or {})
    if not snap.get("party_kind"):
        snap["party_kind"] = snap.get("worker") or ""
    if not snap.get("craftsman"):
        kind = str(snap.get("party_kind") or "")
        if kind in ("職人", "shokunin", "元請", "moto", "応援", "ouen"):
            snap["craftsman"] = str(snap.get("helpers") or "").split("、")[0]
    if not snap.get("work_size"):
        snap["work_size"] = snap.get("dimension") or ""
    if item.line_total is not None:
        snap["total_price"] = item.line_total
    line = SimpleNamespace(**{key: snap.get(key) for key in SNAPSHOT_FIELDS})
    _attach_helper_display(line)
    return line


def _attach_helper_display(record):
    """支払明細では、控除前の金額と手元への控除額を分けて出す。

    保存されている total_price は控除後である。単価と数量が残っていれば、その積を控除前にする。
    伝票ごとに1行へまとめた行は単価と数量を空にしているので、控除後の金額から逆算する。
    控除も逆算も切り捨てなので、逆算した控除前は元の単価×数量と数円ずれることがある。その差は埋めない。
    """
    names = [
        str(name).strip()
        for name in (
            getattr(record, "temoto1", "") or "",
            getattr(record, "temoto2", "") or "",
            getattr(record, "temoto3", "") or "",
        )
        if str(name).strip()
    ]
    count = int(getattr(record, "helper_count", 0) or 0) or len(names)
    record.display_total = getattr(record, "total_price", None)
    record.helper_deduction = None
    record.helper_label = ""
    if count <= 0:
        return record

    percent = getattr(record, "shokunin_deduction_percent", None)
    if not percent:
        percent = _shokunin_deduction_percent(count)
    percent = int(percent or 0)
    gross = _safe_mul(
        getattr(record, "unit_price", None), getattr(record, "work_amount", None)
    )
    net = getattr(record, "total_price", None)
    if gross is None:
        if percent and net is not None and percent < 100:
            gross = net * 100 // (100 - percent)
        else:
            gross = net
    deduction = 0
    if percent and gross is not None:
        deduction = _amount_at_percent(gross, percent)
    elif gross is not None and net is not None:
        deduction = gross - net
    record.display_total = gross
    record.helper_deduction = deduction
    record.helper_label = "手元（" + "、".join(names) + "）" if names else "手元"
    return record


def _collapse_helper_rows_by_voucher(records):
    """手元のマイナス行は伝票番号ごとに1行だけ出す。"""
    from collections import OrderedDict

    records = list(records or [])
    if not records:
        return records
    buckets = OrderedDict()
    for rec in records:
        key = str(getattr(rec, "voucher_no", "") or "").strip()
        if not key:
            key = f"id-{getattr(rec, 'id', id(rec))}"
        buckets.setdefault(key, []).append(rec)
    ordered = []
    for bucket in buckets.values():
        bucket.sort(
            key=lambda rec: (
                getattr(rec, "date", None) or date.min,
                getattr(rec, "id", 0) or 0,
            )
        )
        label = ""
        total = 0
        has_helper = False
        for rec in bucket:
            if getattr(rec, "helper_label", ""):
                has_helper = True
                label = rec.helper_label or label
                total += rec.helper_deduction or 0
            rec.helper_label = ""
            rec.helper_deduction = None
        if has_helper:
            bucket[-1].helper_label = label
            bucket[-1].helper_deduction = total
        ordered.extend(bucket)
    return ordered


def _collapse_temoto_rows_by_voucher(records):
    """手元の支払は、伝票番号ごとに1行で出す。"""
    from collections import OrderedDict

    records = list(records or [])
    if not records:
        return records
    buckets = OrderedDict()
    for rec in records:
        key = (
            str(getattr(rec, "voucher_no", "") or "").strip(),
            getattr(rec, "date", None),
            str(getattr(rec, "site", "") or "").strip(),
        )
        if not any(key):
            key = ("id", getattr(rec, "id", id(rec)))
        buckets.setdefault(key, []).append(rec)
    collapsed = []
    for group in buckets.values():
        group = sorted(group, key=lambda rec: getattr(rec, "id", 0) or 0)
        first = group[0]
        data = {key: getattr(first, key, None) for key in SNAPSHOT_FIELDS}
        types = []
        total = 0
        printed = False
        for rec in group:
            work_type = str(getattr(rec, "work_type", "") or "").strip()
            if work_type and work_type not in types:
                types.append(work_type)
            total += getattr(rec, "total_price", None) or 0
            printed = printed or bool(getattr(rec, "is_printed", False))
        data["work_type"] = "、".join(types)
        data["total_price"] = total
        data["unit_price"] = ""
        data["work_size"] = ""
        data["work_amount"] = ""
        data["remark"] = ""
        data["party_kind"] = ""
        line = SimpleNamespace(**data)
        line.is_printed = printed
        line.printed_document = getattr(first, "printed_document", None)
        line.display_total = total
        collapsed.append(line)
    return collapsed


def _is_craftsman_row_for(record, worker_name):
    name = (worker_name or "").strip()
    if not name:
        return False
    role = _party_kind_text(record)
    helpers = (getattr(record, "helpers", "") or "").strip()
    craftsman = (getattr(record, "craftsman", "") or "").strip()
    if role in ("職人", "shokunin") and name in (craftsman, helpers):
        return True
    return role == name


def _prepare_worker_pay_rows(records, worker_name):
    craftsman_rows = []
    helper_rows = []
    for rec in records:
        if _is_craftsman_row_for(rec, worker_name):
            rec.pay_role = "shokunin"
            _attach_helper_display(rec)
            craftsman_rows.append(rec)
        else:
            rec.pay_role = "temoto"
            helper_rows.append(rec)
    craftsman_rows = _collapse_helper_rows_by_voucher(craftsman_rows)
    helper_rows = _collapse_temoto_rows_by_voucher(helper_rows)
    combined = list(craftsman_rows) + list(helper_rows)

    def sort_key(row):
        return (
            getattr(row, "date", None) or date.min,
            str(getattr(row, "voucher_no", "") or ""),
            getattr(row, "id", 0) or 0,
        )

    combined.sort(key=sort_key)
    return combined


def _printed_item_map(record_ids):
    if not record_ids:
        return {}
    items = PrintedDocumentItem.objects.filter(
        work_record_id__in=record_ids,
        document__status="printed",
    ).select_related("document")
    return {item.work_record_id: item for item in items}


def _active_print_item(record):
    return (
        PrintedDocumentItem.objects.filter(
            work_record=record,
            document__status="printed",
        )
        .select_related("document")
        .first()
    )


def _find_printed_document(
    kind, contractor=None, worker=None, company=None, start=None, end=None
):
    qs = PrintedDocument.objects.filter(
        kind=kind,
        period_start=start,
        period_end=end,
        status="printed",
    )
    if kind == "moto":
        qs = qs.filter(party_contractor=contractor)
    elif kind in ("shokunin", "temoto", "worker"):
        qs = qs.filter(party_worker=worker)
    elif kind == "ouen":
        qs = qs.filter(party_company=company)
    return qs.order_by("-printed_at").first()


def _mark_period_printed(
    kind, party_name, records, start, end, contractor=None, worker=None, company=None
):
    existing = _find_printed_document(kind, contractor, worker, company, start, end)
    if existing:
        return existing, 0
    printed_ids = set(_printed_item_map([r.pk for r in records]).keys())
    to_print = [r for r in records if r.pk not in printed_ids]
    skipped = len(records) - len(to_print)
    if not to_print:
        return None, skipped
    doc = PrintedDocument.objects.create(
        kind=kind,
        party_name=party_name,
        party_contractor=contractor if kind == "moto" else None,
        party_worker=worker if kind in ("shokunin", "temoto", "worker") else None,
        party_company=company if kind == "ouen" else None,
        period_start=start,
        period_end=end,
        total_amount=sum(r.total_price or 0 for r in to_print),
        status="printed",
    )
    PrintedDocumentItem.objects.bulk_create(
        [
            PrintedDocumentItem(
                document=doc,
                work_record=record,
                line_total=record.total_price,
                snapshot=_record_print_snapshot(record),
            )
            for record in to_print
        ]
    )
    return doc, skipped


def _period_print_redirect(
    kind, moto_company, worker_id, temoto_id, company_id, from_date, to_date, extra=None
):
    params = {
        "kind": kind,
        "moto_company": moto_company,
        "worker_id": worker_id,
        "temoto_id": temoto_id,
        "company_id": company_id,
        "from_date": from_date,
        "to_date": to_date,
    }
    if extra:
        params.update(extra)
    params = {key: val for key, val in params.items() if val}
    return redirect(f"{reverse('workrecord_print_period')}?{urlencode(params)}")


def workrecord_list(request):
    params = _list_filter_params(request)
    if params:
        request.session["list_filter"] = params
        request.session.modified = True
    else:
        params = dict(request.session.get("list_filter") or {})
    kind = str(params.get("kind") or "").strip()
    moto_company = str(params.get("moto_company") or "").strip()
    worker_id = str(params.get("worker_id") or "").strip()
    temoto_id = str(params.get("temoto_id") or "").strip()
    company_id = str(params.get("company_id") or "").strip()
    kind, worker_id, temoto_id = _normalize_party_kind(kind, worker_id, temoto_id)

    contractors, workers, companies = _party_choice_querysets()

    if request.method == "POST" and request.POST.get("save_closing_day") is not None:
        contractor = (
            contractors.filter(pk=moto_company).first() if moto_company else None
        )
        if contractor:
            raw = str(request.POST.get("closing_day") or "").strip()
            if raw == "":
                contractor.closing_day = None
                contractor.save(update_fields=["closing_day"])
            else:
                try:
                    day = int(raw)
                except ValueError:
                    day = None
                if day is not None and 1 <= day <= 31:
                    contractor.closing_day = day
                    contractor.save(update_fields=["closing_day"])
        return redirect(
            _list_url(
                {
                    "kind": "moto",
                    "moto_company": moto_company,
                }
            )
        )

    records = WorkRecord.objects.none()
    selected_name = ""
    has_filter = False
    section_kind = None
    selected_contractor = None

    group_name = None
    if kind == "moto" and moto_company:
        contractor = contractors.filter(pk=moto_company).first()
        if contractor:
            has_filter = True
            selected_contractor = contractor
            selected_name = contractor.name
            section_kind = "moto"
            records = _party_records("moto", contractor=contractor).order_by(
                "-date", "-id"
            )
    elif kind == "worker" and worker_id:
        worker = workers.filter(pk=worker_id).first()
        if worker:
            has_filter = True
            selected_name = worker.name
            group_name = worker.name
            section_kind = "worker"
            records = _party_records("worker", worker=worker).order_by("-date", "-id")
    elif kind == "ouen" and company_id:
        company = companies.filter(pk=company_id).first()
        if company:
            has_filter = True
            selected_name = company.name
            section_kind = "ouen"
            records = _party_records("ouen", company=company).order_by("-date", "-id")

    record_list = list(records)
    printed_map = _printed_item_map(
        [r.pk for r in record_list if getattr(r, "pk", None)]
    )
    for rec in record_list:
        item = printed_map.get(rec.pk)
        rec.is_printed = item is not None
        rec.printed_document = item.document if item else None

    if section_kind == "worker":
        prepared = _prepare_worker_pay_rows(record_list, group_name)
        pay_total = sum(r.total_price or 0 for r in prepared)
        sections = [
            {
                "kind": "作業員",
                "title": "作業員の一覧",
                "groups": [
                    {"name": group_name, "records": prepared, "total": pay_total}
                ],
                "total": pay_total,
            }
        ]
    else:
        for rec in record_list:
            _attach_helper_display(rec)
        sections = _grouped_sections(
            record_list, kind=section_kind, group_name=group_name
        )
        if section_kind == "shokunin":
            for section in sections:
                for group in section["groups"]:
                    group["records"] = _collapse_helper_rows_by_voucher(
                        group["records"]
                    )
        elif section_kind == "temoto":
            for section in sections:
                for group in section["groups"]:
                    group["records"] = _collapse_temoto_rows_by_voucher(
                        group["records"]
                    )
    for section in sections:
        for group in section.get("groups") or []:
            _mark_voucher_actions(group.get("records") or [])
    total_sum = sum(section["total"] for section in sections)

    closing_day = None
    closing_period = None
    previous_period = None
    unbilled_count = None
    closing_print_qs = ""
    previous_print_qs = ""
    if selected_contractor:
        closing_day = selected_contractor.closing_day
        closing_period = selected_contractor.current_closing_period()
        previous_period = selected_contractor.previous_closing_period()
        if closing_period:
            start, end = closing_period
            unbilled_count = sum(
                1
                for rec in record_list
                if rec.date and start <= rec.date <= end and not rec.is_printed
            )
            closing_print_qs = urlencode(
                {
                    "kind": "moto",
                    "moto_company": moto_company,
                    "from_date": start.isoformat(),
                    "to_date": end.isoformat(),
                }
            )
        if previous_period:
            prev_start, prev_end = previous_period
            previous_print_qs = urlencode(
                {
                    "kind": "moto",
                    "moto_company": moto_company,
                    "from_date": prev_start.isoformat(),
                    "to_date": prev_end.isoformat(),
                }
            )

    period_params = _list_filter_params(
        extra={
            "kind": kind,
            "moto_company": moto_company,
            "worker_id": worker_id,
            "temoto_id": temoto_id,
            "company_id": company_id,
        }
    )
    if closing_period:
        period_params["from_date"] = closing_period[0].isoformat()
        period_params["to_date"] = closing_period[1].isoformat()

    return render(
        request,
        "workapp/workrecord_list.html",
        {
            "sections": sections,
            "total_sum": total_sum,
            "kind": kind,
            "moto_company": moto_company,
            "worker_id": worker_id,
            "temoto_id": temoto_id,
            "company_id": company_id,
            "contractors": contractors,
            "workers": workers,
            "companies": companies,
            "has_filter": has_filter,
            "selected_name": selected_name,
            "document_title": DOCUMENT_TITLES.get(kind, ""),
            "period_print_qs": urlencode(period_params),
            "closing_day": closing_day,
            "closing_period": closing_period,
            "previous_period": previous_period,
            "unbilled_count": unbilled_count,
            "closing_print_qs": closing_print_qs,
            "previous_print_qs": previous_print_qs,
        },
    )


def workrecord_edit(request, pk):
    record = get_object_or_404(WorkRecord, pk=pk)
    list_qs = urlencode(
        _list_filter_params(request) or (request.session.get("list_filter") or {})
    )
    voucher_records = list(_voucher_qs(record).order_by("id")) or [record]
    locked_item = _active_print_item(record)
    if locked_item:
        return render(
            request,
            "workapp/workrecord_edit.html",
            {
                "form": None,
                "locked": True,
                "record": record,
                "printed_document": locked_item.document,
                "list_qs": list_qs,
                "party_label": "この伝票",
            },
        )

    locked_kinds = _locked_party_kinds(voucher_records)
    request.session["basic_record"] = _serialize_session_basic(
        _basic_record_from_voucher(record)
    )
    request.session["editing_voucher"] = True
    request.session["review_saved"] = False
    request.session["locked_party_labels"] = [
        PARTY_KIND_LABELS.get(kind, kind) for kind in locked_kinds
    ]
    request.session.modified = True
    url = reverse("workrecord_create") + "?restore=1"
    if list_qs:
        url += "&" + list_qs
    return redirect(url)


def _record_unit_type(record):
    worker_label = _party_kind_text(record)
    labels = {
        "元請": "moto",
        "職人": "shokunin",
        "手元": "temoto",
        "応援": "ouen",
    }
    if worker_label in labels:
        return labels[worker_label]
    if worker_label in ("moto", "shokunin", "temoto", "ouen"):
        return worker_label
    return ""


def _voucher_lines_for_print(record, kind):
    voucher = (record.voucher_no or "").strip()
    qs = WorkRecord.objects.all()
    if voucher:
        qs = qs.filter(voucher_no=voucher, date=record.date)
        if record.site_master_id:
            qs = qs.filter(
                Q(site_master_id=record.site_master_id)
                | Q(site_master__isnull=True, site=record.site)
            )
        else:
            qs = qs.filter(site=record.site)
    else:
        qs = qs.filter(pk=record.pk)
    if kind == "shokunin":
        qs = qs.filter(party_kind__in=["職人", "shokunin"])
    elif kind == "temoto":
        name = (record.helpers or "").strip()
        role = _party_kind_text(record)
        if role not in ("手元", "temoto", ""):
            name = role or name
        if name:
            qs = qs.filter(
                Q(party_kind__in=["手元", "temoto"], helpers=name) | Q(party_kind=name)
            )
        else:
            qs = qs.filter(pk=record.pk)
    elif kind == "ouen":
        company = (record.company or "").strip()
        if record.company_master_id:
            qs = qs.filter(
                party_kind__in=["応援", "ouen", company or record.company],
            ).filter(
                Q(company_master_id=record.company_master_id)
                | Q(company_master__isnull=True, company=record.company)
            )
        elif company:
            qs = qs.filter(company=company, party_kind__in=["応援", "ouen", company])
        else:
            qs = qs.filter(pk=record.pk)
    elif kind == "worker":
        name = (getattr(record, "craftsman", "") or record.helpers or "").strip()
        role = _party_kind_text(record)
        if role not in (
            "職人",
            "shokunin",
            "手元",
            "temoto",
            "元請",
            "moto",
            "応援",
            "ouen",
            "",
        ):
            name = role or name
        if name:
            qs = qs.filter(
                Q(party_kind__in=["職人", "shokunin"], craftsman=name)
                | Q(party_kind__in=["職人", "shokunin"], helpers=name)
                | Q(party_kind=name)
                | Q(party_kind__in=["手元", "temoto"], helpers=name)
                | Q(temoto1=name)
                | Q(temoto2=name)
                | Q(temoto3=name)
            )
        else:
            qs = qs.filter(pk=record.pk)
    elif kind == "moto":
        qs = qs.filter(party_kind__in=["元請", "moto"])
    else:
        qs = qs.filter(pk=record.pk)
    rows = list(qs.order_by("date", "id"))
    if not any(getattr(r, "pk", None) == record.pk for r in rows):
        rows.append(record)
    return rows


def workrecord_print(request, pk):
    record = get_object_or_404(WorkRecord, pk=pk)
    src = request.POST if request.method == "POST" else request.GET
    worker_type = src.get("kind", "").strip() or _record_unit_type(record)
    worker_id = src.get("worker_id", "").strip()
    temoto_id = src.get("temoto_id", "").strip()
    worker_type, worker_id, temoto_id = _normalize_party_kind(
        worker_type, worker_id, temoto_id
    )
    if (
        request.method == "POST"
        and request.POST.get("action") == "save_kagami"
        and _wants_kagami(src, worker_type)
    ):
        _save_kagami_sheet(
            _kagami_voucher_scope(worker_type, record.pk, worker_id), request.POST
        )
        params = {
            "kind": worker_type,
            "worker_id": worker_id,
            "temoto_id": temoto_id,
            "moto_company": src.get("moto_company", "").strip(),
            "company_id": src.get("company_id", "").strip(),
            "kagami": "1",
        }
        params = {key: val for key, val in params.items() if val}
        return redirect(
            f"{reverse('workrecord_print', args=[record.pk])}?{urlencode(params)}"
        )
    document_title = DOCUMENT_TITLES.get(worker_type, "作業記録")
    print_records = _voucher_lines_for_print(record, worker_type)
    if worker_type == "worker":
        person = Worker.objects.filter(pk=worker_id).first() if worker_id else None
        name = (
            person.name
            if person
            else (
                (getattr(record, "craftsman", "") or "").strip()
                or (record.helpers or "").strip()
                or _party_kind_text(record)
            )
        )
        if person:
            print_records = list(
                _party_records("worker", worker=person)
                .filter(
                    voucher_no=record.voucher_no,
                    date=record.date,
                    site=record.site,
                )
                .order_by("date", "id")
            )
            if not print_records:
                print_records = [record]
        print_records = _prepare_worker_pay_rows(print_records, name)
    elif worker_type == "shokunin":
        for rec in print_records:
            _attach_helper_display(rec)
        print_records = _collapse_helper_rows_by_voucher(print_records)
    elif worker_type == "temoto":
        print_records = _collapse_temoto_rows_by_voucher(print_records)
    print_records = _annotate_print_repeats(print_records)
    print_total = sum(r.total_price or 0 for r in print_records)
    party_name = _single_print_party_name(worker_type, record, worker_id)
    show_kagami = _wants_kagami(src, worker_type)
    kagami_scope = _kagami_voucher_scope(worker_type, record.pk, worker_id)
    return render(
        request,
        "workapp/workrecord_print.html",
        {
            "record": record,
            "print_records": print_records,
            "print_total": print_total,
            "worker_type": worker_type,
            "is_moto": worker_type == "moto",
            "is_worker": worker_type == "worker",
            "is_shokunin": worker_type == "shokunin",
            "is_temoto": worker_type == "temoto",
            "is_ouen": worker_type == "ouen",
            "document_title": document_title,
            "list_qs": urlencode(
                _list_filter_params(request)
                or (request.session.get("list_filter") or {})
            ),
            "show_kagami": show_kagami,
            "kagami_save_url": reverse("workrecord_print", args=[record.pk]),
            "kagami_hidden": {
                "kind": worker_type,
                "worker_id": worker_id,
                "temoto_id": temoto_id,
                "moto_company": src.get("moto_company", "").strip(),
                "company_id": src.get("company_id", "").strip(),
            },
            **_kagami_context(kagami_scope, print_total),
            "kagami_pay_month": _next_month_end_label(getattr(record, "date", None)),
            **_print_party_context(worker_type, party_name),
            **_pay_tax_context(print_total),
        },
    )


def workrecord_print_period(request):
    src = request.POST if request.method == "POST" else request.GET
    from_date_str = src.get("from_date", "").strip()
    to_date_str = src.get("to_date", "").strip()
    kind = (src.get("kind") or src.get("worker_type") or "").strip()
    moto_company = src.get("moto_company", "").strip()
    worker_id = src.get("worker_id", "").strip()
    temoto_id = src.get("temoto_id", "").strip()
    company_id = src.get("company_id", "").strip()
    kind, worker_id, temoto_id = _normalize_party_kind(kind, worker_id, temoto_id)

    contractors, workers, companies = _party_choice_querysets()

    if kind == "moto" and moto_company and (not from_date_str or not to_date_str):
        selected_for_dates = contractors.filter(pk=moto_company).first()
        period = (
            selected_for_dates.current_closing_period() if selected_for_dates else None
        )
        if period:
            from_date_str = from_date_str or period[0].isoformat()
            to_date_str = to_date_str or period[1].isoformat()

    selected_contractor = None
    selected_worker = None
    selected_company = None
    selected_name = ""
    records = []
    total_sum = 0
    error = ""
    has_period = False
    from_date = to_date = None
    printed_document = None
    skipped_printed = 0
    submitted = bool(
        from_date_str
        or to_date_str
        or kind
        or moto_company
        or worker_id
        or temoto_id
        or company_id
    )

    if submitted:
        if not from_date_str or not to_date_str:
            error = "開始日と終了日を両方指定してください。"
        elif kind not in ("moto", "worker", "ouen"):
            error = "請求先・作業員・応援のいずれかを選んでください。"
        else:
            try:
                from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
                to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
            except ValueError:
                error = "日付の形式が正しくありません。"
            else:
                if from_date > to_date:
                    error = "開始日は終了日以前にしてください。"
                else:
                    qs = WorkRecord.objects.none()
                    if kind == "moto":
                        if not moto_company:
                            error = "元請を選択してください。"
                        else:
                            selected_contractor = contractors.filter(
                                pk=moto_company
                            ).first()
                            if selected_contractor is None:
                                error = "元請が見つかりません。"
                            else:
                                selected_name = selected_contractor.name
                                qs = _party_records(
                                    "moto", contractor=selected_contractor
                                )
                    elif kind == "worker":
                        if not worker_id:
                            error = "作業員を選択してください。"
                        else:
                            selected_worker = workers.filter(pk=worker_id).first()
                            if selected_worker is None:
                                error = "作業員が見つかりません。"
                            else:
                                selected_name = selected_worker.name
                                qs = _party_records("worker", worker=selected_worker)
                    elif kind == "ouen":
                        if not company_id:
                            error = "応援企業を選択してください。"
                        else:
                            selected_company = companies.filter(pk=company_id).first()
                            if selected_company is None:
                                error = "応援企業が見つかりません。"
                            else:
                                selected_name = selected_company.name
                                qs = _party_records("ouen", company=selected_company)

                    if not error:
                        records = list(
                            qs.filter(date__range=(from_date, to_date)).order_by(
                                "date", "id"
                            )
                        )
                        total_sum = sum(r.total_price or 0 for r in records)
                        has_period = True

    if request.method == "POST" and has_period:
        action = request.POST.get("action")
        if action == "save_kagami":
            _save_kagami_sheet(
                _kagami_period_scope(
                    kind, worker_id, company_id, from_date_str, to_date_str
                ),
                request.POST,
            )
            return _period_print_redirect(
                kind,
                moto_company,
                worker_id,
                temoto_id,
                company_id,
                from_date_str,
                to_date_str,
                extra={"kagami": "1"},
            )
        if action == "cancel_printed":
            doc = PrintedDocument.objects.filter(
                pk=request.POST.get("document_id"),
                status="printed",
            ).first()
            if doc:
                doc.status = "cancelled"
                doc.save(update_fields=["status"])
                messages.success(request, "印刷を取り消しました。")
            return _period_print_redirect(
                kind,
                moto_company,
                worker_id,
                temoto_id,
                company_id,
                from_date_str,
                to_date_str,
            )
        if action == "mark_printed":
            doc, skipped_printed = _mark_period_printed(
                kind,
                selected_name,
                records,
                from_date,
                to_date,
                contractor=selected_contractor,
                worker=selected_worker,
                company=selected_company,
            )
            if doc is None:
                error = "この期間の伝票は、すでに別の書類で印刷済みです。"
            else:
                messages.success(
                    request, "印刷済みにしました。金額はこの内容で固定されます。"
                )
                return _period_print_redirect(
                    kind,
                    moto_company,
                    worker_id,
                    temoto_id,
                    company_id,
                    from_date_str,
                    to_date_str,
                )

    if has_period and from_date and to_date:
        printed_document = _find_printed_document(
            kind,
            selected_contractor,
            selected_worker,
            selected_company,
            from_date,
            to_date,
        )
        if printed_document:
            items = printed_document.items.all().order_by("id")
            records = [_line_from_snapshot(item) for item in items]
            total_sum = printed_document.total_amount
            if kind == "worker":
                records = _prepare_worker_pay_rows(records, selected_name)
            elif kind == "shokunin":
                records = _collapse_helper_rows_by_voucher(records)
            elif kind == "temoto":
                records = _collapse_temoto_rows_by_voucher(records)
        elif kind == "worker" and selected_worker:
            records = _prepare_worker_pay_rows(records, selected_worker.name)
        elif kind == "shokunin":
            for rec in records:
                _attach_helper_display(rec)
            records = _collapse_helper_rows_by_voucher(records)
        elif kind == "temoto":
            records = _collapse_temoto_rows_by_voucher(records)
        records = _annotate_print_repeats(records)

    is_moto = kind == "moto"
    is_worker = kind == "worker"
    is_shokunin = kind == "shokunin"
    is_temoto = kind == "temoto"
    is_ouen = kind == "ouen"
    document_title = DOCUMENT_TITLES.get(kind, "期間指定印刷")
    period_party = ""
    if printed_document:
        period_party = printed_document.party_name
    else:
        period_party = selected_name

    return render(
        request,
        "workapp/workrecord_print_period.html",
        {
            "from_date": from_date_str,
            "to_date": to_date_str,
            "records": records,
            "total_sum": total_sum,
            "error": error,
            "has_period": has_period,
            "kind": kind,
            "worker_type": kind,
            "is_moto": is_moto,
            "is_worker": is_worker,
            "is_shokunin": is_shokunin,
            "is_temoto": is_temoto,
            "is_ouen": is_ouen,
            "document_title": document_title,
            "moto_company": moto_company,
            "worker_id": worker_id,
            "temoto_id": temoto_id,
            "company_id": company_id,
            "contractors": contractors,
            "workers": workers,
            "companies": companies,
            "selected_contractor": selected_contractor,
            "selected_name": selected_name,
            "printed_document": printed_document,
            "skipped_printed": skipped_printed,
            "list_qs": urlencode(
                _list_filter_params(
                    extra={
                        "kind": kind,
                        "moto_company": moto_company,
                        "worker_id": worker_id,
                        "temoto_id": temoto_id,
                        "company_id": company_id,
                    }
                )
            ),
            **_print_party_context(kind, period_party),
            "show_kagami": _wants_kagami(src, kind),
            "kagami_save_url": reverse("workrecord_print_period"),
            "kagami_hidden": {
                "from_date": from_date_str,
                "to_date": to_date_str,
                "kind": kind,
                "moto_company": moto_company,
                "worker_id": worker_id,
                "temoto_id": temoto_id,
                "company_id": company_id,
            },
            "kagami_pay_month": _next_month_end_label(to_date or to_date_str),
            **_kagami_context(
                _kagami_period_scope(
                    kind, worker_id, company_id, from_date_str, to_date_str
                ),
                total_sum,
            ),
            **_pay_tax_context(total_sum),
        },
    )


@require_POST
def workrecord_delete(request, pk):
    record = get_object_or_404(WorkRecord, pk=pk)
    voucher_records = list(_voucher_qs(record)) or [record]
    if any(_active_print_item(rec) for rec in voucher_records):
        messages.error(
            request,
            "印刷済みのため削除できません。印刷を取り消してから削除してください。",
        )
        return _redirect_to_list(request)
    _voucher_qs(record).delete()
    messages.success(request, "伝票を削除しました。")
    return _redirect_to_list(request)


def logout_view(request):
    logout(request)
    return redirect("login")


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("workrecord_search")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect(reverse("login") + "?signed_up=1")
    return render(request, "workapp/signup.html", {"form": form})


def monthly_summary(request):
    today = datetime.today()
    year = today.year
    month = today.month

    records = WorkRecord.objects.filter(date__year=year, date__month=month).order_by(
        "date"
    )

    total_sum = sum(record.total_price or 0 for record in records)

    return render(
        request,
        "workapp/monthly_summary.html",
        {
            "records": records,
            "year": year,
            "month": month,
            "total_sum": total_sum,
        },
    )


def get_unit_price(request):
    rate_type = request.GET.get("type")  # contractor / worker / company
    target_id = request.GET.get("id")  # 元請ID / 職人ID / 会社ID
    size_id = request.GET.get("size")  # 寸法ID

    if not size_id:
        return JsonResponse({"unit_price": None})

    if rate_type == "contractor":
        try:
            rate = GeneralContractorRate.objects.get(
                general_contractor_id=target_id, work_size_id=size_id
            )
            return JsonResponse({"unit_price": rate.unit_price})
        except GeneralContractorRate.DoesNotExist:
            return JsonResponse({"unit_price": None})

    if rate_type == "worker":
        worker = Worker.objects.filter(pk=target_id).first()
        size = WorkSize.objects.filter(pk=size_id).first()
        return JsonResponse({"unit_price": _worker_unit_price(worker, size)})

    if rate_type == "company":
        try:
            rate = CompanyRate.objects.get(company_id=target_id, work_size_id=size_id)
            return JsonResponse({"unit_price": rate.unit_price})
        except CompanyRate.DoesNotExist:
            return JsonResponse({"unit_price": None})

    return JsonResponse({"unit_price": None})


def _master_row(obj, code):
    return {
        "id": obj.pk,
        "code": code or "",
        "name": obj.name,
        "name_kana": obj.name_kana or "",
        "is_active": obj.is_active,
    }


def api_sites(request):
    rows = [_master_row(site, site.site_code) for site in Site.objects.order_by("name")]
    return JsonResponse({"sites": rows})


def api_workers(request):
    rows = [
        _master_row(worker, worker.employee_number)
        for worker in Worker.objects.order_by("name")
    ]
    return JsonResponse({"workers": rows})
