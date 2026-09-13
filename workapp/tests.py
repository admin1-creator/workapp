from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Company,
    GeneralContractor,
    Site,
    WorkRecord,
    WorkSize,
    Worker,
    WorkerDefaultRate,
    WorkerRate,
    closing_period_for,
)
from .views import (
    _moto_work_rows,
    _ouen_work_rows,
    _shokunin_work_rows,
    _temoto_work_rows,
    _worker_unit_price,
)


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="test-pass-123")
        self.client.force_login(self.user)


class PartyFilterTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.gc_a = GeneralContractor.objects.create(name="元請A")
        self.gc_b = GeneralContractor.objects.create(name="元請B")
        self.company = Company.objects.create(name="応援B")
        self.shokunin = Worker.objects.create(name="職人太郎", worker_type="職人")
        self.temoto = Worker.objects.create(name="手元花子", worker_type="手元")
        WorkRecord.objects.create(
            voucher_no="V1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            worker="元請",
            general_contractor="元請A",
            total_price=1000,
        )
        WorkRecord.objects.create(
            voucher_no="V1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            worker="職人",
            helpers="職人太郎",
            general_contractor="元請A",
            total_price=800,
        )
        WorkRecord.objects.create(
            voucher_no="V1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            worker="手元",
            helpers="手元花子",
            general_contractor="元請A",
            temoto1="手元花子",
            total_price=200,
        )
        WorkRecord.objects.create(
            voucher_no="V1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            worker="応援",
            company="応援B",
            general_contractor="元請A",
            total_price=900,
        )

    def test_list_has_three_party_choices(self):
        response = self.client.get(reverse("workrecord_list"))
        self.assertContains(response, 'value="moto"')
        self.assertContains(response, 'value="worker"')
        self.assertContains(response, 'value="ouen"')
        self.assertNotContains(response, 'value="shokunin"')
        self.assertNotContains(response, 'value="temoto"')
        self.assertContains(response, "請求先・作業員・応援のいずれか")

    def test_lists_show_site_with_gc_and_upper_company(self):
        listed = self.client.get(reverse("workrecord_list"), {
            "kind": "worker",
            "worker_id": self.shokunin.pk,
        })
        self.assertContains(listed, "現場X（元請A）")
        self.assertContains(listed, "<th>上位企業</th>")

        WorkRecord.objects.filter(helpers="職人太郎").update(primary_company="元請B")
        listed = self.client.get(reverse("workrecord_list"), {
            "kind": "worker",
            "worker_id": self.shokunin.pk,
        })
        self.assertContains(listed, "現場X（元請A）")
        self.assertContains(listed, "元請B")
        self.assertNotContains(listed, "<th>元請</th>")

    def test_list_worker_includes_craftsman_pay(self):
        response = self.client.get(reverse("workrecord_list"), {
            "kind": "worker",
            "worker_id": self.shokunin.pk,
        })
        self.assertContains(response, "作業員の一覧")
        self.assertContains(response, "800")
        self.assertNotContains(response, "手元の一覧")

    def test_list_worker_includes_helper_pay(self):
        response = self.client.get(reverse("workrecord_list"), {
            "kind": "worker",
            "worker_id": self.temoto.pk,
        })
        self.assertContains(response, "作業員の一覧")
        self.assertContains(response, "200")
        self.assertNotContains(response, "職人の一覧")

    def test_old_shokunin_url_with_temoto_id_still_works(self):
        response = self.client.get(reverse("workrecord_list"), {
            "kind": "shokunin",
            "worker_id": self.temoto.pk,
        })
        self.assertContains(response, "手元花子")
        self.assertContains(response, "作業員の一覧")
        self.assertNotContains(response, "職人の一覧")

    def test_same_worker_craftsman_and_helper_are_combined(self):
        WorkRecord.objects.create(
            voucher_no="V2",
            date=date(2026, 9, 2),
            site="現場X",
            work_type="溶接",
            worker="手元",
            helpers="職人太郎",
            temoto1="職人太郎",
            general_contractor="元請A",
            total_price=150,
        )
        listed = self.client.get(reverse("workrecord_list"), {
            "kind": "worker",
            "worker_id": self.shokunin.pk,
        })
        self.assertContains(listed, "800")
        self.assertContains(listed, "150")
        self.assertContains(listed, "作業員の一覧")
        printed = self.client.get(reverse("workrecord_print_period"), {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "worker",
            "worker_id": self.shokunin.pk,
        })
        self.assertContains(printed, "支払書")
        self.assertContains(printed, "800")
        self.assertContains(printed, "150")

    def test_any_worker_can_be_craftsman_or_helper(self):
        from .forms import WorkRecordForm
        site = Site.objects.create(name="現場X", general_contractor=self.gc_a)
        form = WorkRecordForm(data={
            "voucher_no": "V9",
            "date": "2026-09-01",
            "site": str(site.pk),
            "worker": str(self.temoto.pk),
            "temoto1": str(self.shokunin.pk),
        })
        self.assertTrue(form.is_valid(), form.errors)
        create = self.client.get(reverse("workrecord_create"))
        self.assertContains(create, 'id="id_worker"')
        self.assertContains(create, "職人太郎")
        self.assertContains(create, "手元花子")
        listed = self.client.get(reverse("workrecord_list"), {
            "kind": "shokunin",
            "worker_id": self.shokunin.pk,
        })
        self.assertNotContains(listed, ">区分</th>")

    def test_period_print_titles(self):
        base = reverse("workrecord_print_period")
        moto = self.client.get(base, {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "moto",
            "moto_company": self.gc_a.pk,
        })
        self.assertContains(moto, "請求書")
        self.assertContains(moto, "1000")

        pay = self.client.get(base, {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "worker",
            "worker_id": self.shokunin.pk,
        })
        self.assertContains(pay, "支払書")
        self.assertNotContains(pay, "支払書（職人）")
        self.assertContains(pay, "800")
        self.assertNotContains(pay, "手元の一覧")

        helper = self.client.get(base, {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "worker",
            "worker_id": self.temoto.pk,
        })
        self.assertContains(helper, "支払書")
        self.assertNotContains(helper, "支払書（手元）")
        self.assertContains(helper, "200")

        ouen = self.client.get(base, {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "ouen",
            "company_id": self.company.pk,
        })
        self.assertContains(ouen, "支払書（応援）")
        self.assertContains(ouen, "900")

    def test_temoto_and_ouen_print_match_shokunin_columns(self):
        headers = [
            "<th>伝票番号</th>",
            "<th>日付</th>",
            "<th>現場名</th>",
            "<th>上位企業</th>",
            "<th>作業種類</th>",
            "<th>寸法</th>",
            "<th>作業量</th>",
            "<th>単価</th>",
            "<th>合計金額</th>",
        ]
        period = reverse("workrecord_print_period")
        shokunin = self.client.get(period, {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "worker",
            "worker_id": self.shokunin.pk,
        })
        temoto = self.client.get(period, {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "worker",
            "worker_id": self.temoto.pk,
        })
        ouen = self.client.get(period, {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "ouen",
            "company_id": self.company.pk,
        })
        for html in (shokunin, temoto, ouen):
            for header in headers:
                self.assertContains(html, header)
        self.assertNotContains(temoto, 'class="minus"')
        self.assertNotContains(ouen, 'class="minus"')

        temoto_row = WorkRecord.objects.get(worker="手元")
        ouen_row = WorkRecord.objects.get(worker="応援")
        temoto_one = self.client.get(reverse("workrecord_print", args=[temoto_row.pk]), {"kind": "temoto"})
        ouen_one = self.client.get(reverse("workrecord_print", args=[ouen_row.pk]), {"kind": "ouen"})
        for html in (temoto_one, ouen_one):
            for header in headers:
                self.assertContains(html, header)
            self.assertNotContains(html, 'class="minus"')
        self.assertContains(ouen_one, "元請A")

    def test_temoto_list_and_print_one_row_per_voucher(self):
        WorkRecord.objects.create(
            voucher_no="V1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="溶接",
            worker="手元",
            helpers="手元花子",
            general_contractor="元請A",
            temoto1="手元花子",
            unit_price=50,
            work_amount=2,
            total_price=50,
        )
        listed = self.client.get(reverse("workrecord_list"), {
            "kind": "worker",
            "worker_id": self.temoto.pk,
        })
        self.assertContains(listed, "V1", count=1)
        self.assertContains(listed, "圧接、溶接")
        self.assertContains(listed, "250")
        self.assertNotContains(listed, ">50<")

        printed = self.client.get(reverse("workrecord_print_period"), {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "worker",
            "worker_id": self.temoto.pk,
        })
        self.assertContains(printed, "V1", count=1)
        self.assertContains(printed, "圧接、溶接")
        self.assertContains(printed, "250")

    def test_create_form_is_grouped(self):
        response = self.client.get(reverse("workrecord_create"))
        self.assertContains(response, "1. 基本")
        self.assertContains(response, "2. 職人・手元・応援")
        self.assertContains(response, "3. 作業内容")
        self.assertContains(response, 'name="work_type_1"')
        self.assertContains(response, 'name="voucher_no"')
        self.assertContains(response, 'name="price_mode_1"')
        self.assertContains(response, "請求額")
        self.assertContains(response, "支払額")
        self.assertContains(response, "応援額")

    def test_manual_line_uses_separate_party_amounts(self):
        data = {
            "voucher_no": "X1",
            "date": date(2026, 9, 1),
            "site": "現場X",
            "general_contractor": "元請A",
            "worker": "職人太郎",
            "worker_id": self.shokunin.pk,
            "temoto1": "手元花子",
            "company": "応援B",
            "work_types": ["その他"],
            "work_sizes": [""],
            "work_amounts": [None],
            "remarks": [""],
            "price_modes": ["manual"],
            "manual_billings": [5000],
            "manual_pays": [3000],
            "manual_ouens": [2000],
        }
        moto_rows, moto_totals, _alloc = _moto_work_rows(data)
        self.assertEqual(moto_rows[0]["total_price"], 5000)
        self.assertEqual(moto_totals["total"], 5000)
        shokunin_rows, shokunin_totals, _alloc = _shokunin_work_rows(data, self.shokunin)
        self.assertEqual(shokunin_totals["subtotal"], 3000)
        self.assertEqual(shokunin_totals["total"], 1950)
        _temoto_rows, temoto_totals, _alloc, _shares = _temoto_work_rows(data, self.shokunin)
        self.assertEqual(temoto_totals["total"], 1050)
        ouen_rows, ouen_totals, _alloc = _ouen_work_rows(data)
        self.assertEqual(ouen_rows[0]["total_price"], 2000)
        self.assertEqual(ouen_totals["total"], 2000)
        self.assertNotEqual(ouen_rows[0]["total_price"], shokunin_totals["subtotal"])

    def test_edit_form_is_grouped(self):
        Site.objects.create(name="現場X", general_contractor=self.gc_a)
        shokunin_row = WorkRecord.objects.get(worker="職人")
        response = self.client.get(reverse("workrecord_edit", args=[shokunin_row.pk]), follow=True)
        self.assertContains(response, "伝票の編集")
        self.assertContains(response, "1. 基本")
        self.assertContains(response, "2. 職人・手元・応援")
        self.assertContains(response, "3. 作業内容")
        self.assertContains(response, 'name="work_type_1"')
        self.assertContains(response, "圧接")
        self.assertContains(response, "職人太郎")
        self.assertNotContains(response, "この行は")

        moto_row = WorkRecord.objects.get(worker="元請")
        moto_edit = self.client.get(reverse("workrecord_edit", args=[moto_row.pk]), follow=True)
        self.assertContains(moto_edit, "伝票の編集")
        self.assertContains(moto_edit, "圧接")

    def test_create_and_edit_use_searchable_site_and_contractor(self):
        site = Site.objects.create(name="現場X", general_contractor=self.gc_a)
        create = self.client.get(reverse("workrecord_create"))
        self.assertContains(create, "select2.full.min.js")
        self.assertContains(create, f'data-contractor="{self.gc_a.pk}"')
        self.assertContains(create, "現場X")

        row = WorkRecord.objects.get(worker="職人")
        edit = self.client.get(reverse("workrecord_edit", args=[row.pk]), follow=True)
        self.assertContains(edit, "select2.full.min.js")
        self.assertContains(edit, f'data-contractor="{site.general_contractor_id}"')
        self.assertContains(create, "1次企業")
        self.assertContains(create, "請求先")
        self.assertContains(create, 'id="id_general_contractor_display"')
        self.assertContains(create, 'id="id_primary_company"')

    def test_form_billing_follows_primary_company(self):
        from .forms import WorkRecordForm
        site = Site.objects.create(name="現場X", general_contractor=self.gc_a)
        base = {
            "voucher_no": "V9",
            "date": "2026-09-01",
            "site": str(site.pk),
            "worker": str(self.shokunin.pk),
        }
        form = WorkRecordForm(data=base)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["general_contractor_name"], "元請A")
        self.assertEqual(form.cleaned_data["primary_company_name"], "")
        self.assertEqual(form.cleaned_data["billing_contractor_name"], "元請A")

        form = WorkRecordForm(data={**base, "primary_company": str(self.gc_b.pk)})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["general_contractor_name"], "元請A")
        self.assertEqual(form.cleaned_data["primary_company_name"], "元請B")
        self.assertEqual(form.cleaned_data["billing_contractor_name"], "元請B")

    def test_moto_list_filters_by_billing_contractor(self):
        WorkRecord.objects.create(
            voucher_no="BILL",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            worker="元請",
            general_contractor="元請A",
            primary_company="元請B",
            billing_contractor="元請B",
            total_price=111,
        )
        a_list = self.client.get(reverse("workrecord_list"), {
            "kind": "moto",
            "moto_company": self.gc_a.pk,
        })
        self.assertNotContains(a_list, "BILL")
        b_list = self.client.get(reverse("workrecord_list"), {
            "kind": "moto",
            "moto_company": self.gc_b.pk,
        })
        self.assertContains(b_list, "BILL")
        self.assertContains(b_list, "111")


class ClosingDayTests(LoggedInTestCase):
    def test_closing_period_before_and_after_day(self):
        self.assertEqual(
            closing_period_for(date(2026, 9, 9), 20),
            (date(2026, 8, 21), date(2026, 9, 20)),
        )
        self.assertEqual(
            closing_period_for(date(2026, 9, 21), 20),
            (date(2026, 9, 21), date(2026, 10, 20)),
        )
        self.assertEqual(
            closing_period_for(date(2026, 9, 9), 31),
            (date(2026, 9, 1), date(2026, 9, 30)),
        )
        self.assertIsNone(closing_period_for(date(2026, 9, 9), None))

    def test_list_shows_unset_closing_day(self):
        contractor = GeneralContractor.objects.create(name="元請C")
        response = self.client.get(reverse("workrecord_list"), {
            "kind": "moto",
            "moto_company": contractor.pk,
        })
        self.assertContains(response, "締め日は未設定")
        self.assertContains(response, "開始日・終了日を手入力")

    def test_list_shows_period_and_unbilled_count(self):
        contractor = GeneralContractor.objects.create(name="元請D", closing_day=20)
        start, end = contractor.current_closing_period(timezone.localdate())
        WorkRecord.objects.create(
            voucher_no="IN",
            date=start,
            site="現場Y",
            work_type="圧接",
            worker="元請",
            general_contractor="元請D",
            total_price=100,
        )
        WorkRecord.objects.create(
            voucher_no="OUT",
            date=start - timedelta(days=1),
            site="現場Y",
            work_type="圧接",
            worker="元請",
            general_contractor="元請D",
            total_price=50,
        )
        response = self.client.get(reverse("workrecord_list"), {
            "kind": "moto",
            "moto_company": contractor.pk,
        })
        self.assertContains(response, "毎月 20 日")
        self.assertContains(response, str(start))
        self.assertContains(response, str(end))
        self.assertContains(response, "未請求：1 件")
        self.assertContains(response, "この期間で印刷")

    def test_save_closing_day_from_list(self):
        contractor = GeneralContractor.objects.create(name="元請E")
        response = self.client.post(reverse("workrecord_list"), {
            "save_closing_day": "1",
            "kind": "moto",
            "moto_company": contractor.pk,
            "closing_day": "25",
        })
        self.assertEqual(response.status_code, 302)
        contractor.refresh_from_db()
        self.assertEqual(contractor.closing_day, 25)

    def test_period_print_manual_dates_without_closing_day(self):
        contractor = GeneralContractor.objects.create(name="元請F")
        WorkRecord.objects.create(
            voucher_no="V2",
            date=date(2026, 9, 5),
            site="現場Z",
            work_type="圧接",
            worker="元請",
            general_contractor="元請F",
            total_price=300,
        )
        response = self.client.get(reverse("workrecord_print_period"), {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "moto",
            "moto_company": contractor.pk,
        })
        self.assertContains(response, "請求書")
        self.assertContains(response, "300")

    def test_period_print_prefills_closing_period(self):
        contractor = GeneralContractor.objects.create(name="元請G", closing_day=20)
        start, end = contractor.current_closing_period(timezone.localdate())
        response = self.client.get(reverse("workrecord_print_period"), {
            "kind": "moto",
            "moto_company": contractor.pk,
        })
        self.assertContains(response, f'value="{start.isoformat()}"')
        self.assertContains(response, f'value="{end.isoformat()}"')


class LoginAndPrintTests(LoggedInTestCase):
    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse("workrecord_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_login_page_renders(self):
        self.client.logout()
        response = self.client.get(reverse("login"))
        self.assertContains(response, "請求支払い入力")
        self.assertContains(response, "ユーザー名")

    def test_mark_printed_freezes_amount_and_locks_edit(self):
        contractor = GeneralContractor.objects.create(name="元請H", closing_day=31)
        record = WorkRecord.objects.create(
            voucher_no="P1",
            date=date(2026, 9, 5),
            site="現場P",
            work_type="圧接",
            worker="元請",
            general_contractor="元請H",
            total_price=1000,
        )
        period = reverse("workrecord_print_period")
        params = {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "moto",
            "moto_company": str(contractor.pk),
        }
        mark = self.client.post(period, {**params, "action": "mark_printed"})
        self.assertEqual(mark.status_code, 302)

        record.total_price = 9999
        record.save(update_fields=["total_price"])

        frozen = self.client.get(period, params)
        self.assertContains(frozen, "印刷済み")
        self.assertContains(frozen, "1000")
        self.assertNotContains(frozen, "9999")

        listed = self.client.get(reverse("workrecord_list"), {
            "kind": "moto",
            "moto_company": contractor.pk,
        })
        self.assertContains(listed, "印刷済み")
        self.assertContains(listed, "未請求：0 件")

        edit = self.client.get(reverse("workrecord_edit", args=[record.pk]))
        self.assertContains(edit, "印刷済みのため")

        cancel = self.client.post(period, {
            **params,
            "action": "cancel_printed",
            "document_id": frozen.context["printed_document"].id,
        })
        self.assertEqual(cancel.status_code, 302)
        edit_again = self.client.get(reverse("workrecord_edit", args=[record.pk]), follow=True)
        self.assertNotContains(edit_again, "印刷済みのため")
        self.assertContains(edit_again, "伝票の編集")

    def test_single_print_uses_payment_title(self):
        record = WorkRecord.objects.create(
            voucher_no="S1",
            date=date(2026, 9, 5),
            site="現場S",
            work_type="圧接",
            worker="職人",
            helpers="職人太郎",
            total_price=800,
        )
        response = self.client.get(reverse("workrecord_print", args=[record.pk]), {"kind": "worker"})
        self.assertContains(response, "支払書")
        self.assertNotContains(response, "支払書（職人）")
        self.assertContains(response, "請求支払い入力")

    def test_moto_print_column_order(self):
        contractor = GeneralContractor.objects.create(name="元請I")
        WorkRecord.objects.create(
            voucher_no="M1",
            date=date(2026, 9, 5),
            site="現場M",
            work_type="圧接",
            worker="元請",
            general_contractor="元請I",
            work_size="D19",
            work_amount=10,
            unit_price=100,
            total_price=1000,
        )
        html = self.client.get(reverse("workrecord_print_period"), {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "moto",
            "moto_company": contractor.pk,
        }).content.decode()
        self.assertIn(
            "<th>伝票番号</th>",
            html,
        )
        self.assertLess(html.find("伝票番号"), html.find("日付"))
        self.assertLess(html.find(">日付</th>"), html.find(">現場名</th>"))
        self.assertLess(html.find(">現場名</th>"), html.find(">上位企業</th>"))
        self.assertLess(html.find(">上位企業</th>"), html.find(">作業種類</th>"))
        self.assertIn("現場M（元請I）", html)
        self.assertLess(html.find(">作業種類</th>"), html.find(">寸法</th>"))
        self.assertLess(html.find(">寸法</th>"), html.find(">作業量</th>"))
        self.assertLess(html.find(">作業量</th>"), html.find(">単価</th>"))
        self.assertLess(html.find(">単価</th>"), html.find(">合計金額</th>"))

    def test_single_print_shows_all_voucher_work_lines(self):
        contractor = GeneralContractor.objects.create(name="元請J")
        first = WorkRecord.objects.create(
            voucher_no="M2",
            date=date(2026, 9, 5),
            site="現場J",
            work_type="圧接",
            worker="元請",
            general_contractor="元請J",
            total_price=1000,
        )
        WorkRecord.objects.create(
            voucher_no="M2",
            date=date(2026, 9, 5),
            site="現場J",
            work_type="溶接",
            worker="元請",
            general_contractor="元請J",
            total_price=400,
        )
        html = self.client.get(
            reverse("workrecord_print", args=[first.pk]),
            {"kind": "moto"},
        )
        self.assertContains(html, "圧接")
        self.assertContains(html, "溶接")
        self.assertContains(html, "1000")
        self.assertContains(html, "400")

    def test_shokunin_list_and_print_show_helper_minus(self):
        worker = Worker.objects.create(name="職人次郎", worker_type="職人")
        record = WorkRecord.objects.create(
            voucher_no="H1",
            date=date(2026, 9, 5),
            site="現場H",
            work_type="圧接",
            worker="職人",
            helpers="職人次郎",
            general_contractor="元請A",
            work_size="D19",
            work_amount=10,
            unit_price=1000,
            total_price=6500,
            helper_count=1,
            temoto1="手元花子",
            shokunin_deduction_percent=35,
            remark="備考テスト",
        )
        WorkRecord.objects.create(
            voucher_no="H1",
            date=date(2026, 9, 5),
            site="現場H",
            work_type="溶接",
            worker="職人",
            helpers="職人次郎",
            general_contractor="元請A",
            work_size="D22",
            work_amount=4,
            unit_price=500,
            total_price=1300,
            helper_count=1,
            temoto1="手元花子",
            shokunin_deduction_percent=35,
            remark="2行目",
        )
        listed = self.client.get(reverse("workrecord_list"), {
            "kind": "shokunin",
            "worker_id": worker.pk,
        }).content.decode()
        remark_pos = listed.find(">備考</th>")
        amount_pos = listed.find(">合計金額</th>")
        self.assertGreater(remark_pos, amount_pos)
        self.assertEqual(listed.count("手元（手元花子）"), 1)
        self.assertIn("−4200", listed)
        self.assertEqual(listed.count(">印刷</a>"), 1)
        self.assertEqual(listed.count(">編集</a>"), 1)

        printed = self.client.get(reverse("workrecord_print", args=[record.pk]), {
            "kind": "shokunin",
        }).content.decode()
        self.assertLess(printed.find(">伝票番号</th>"), printed.find(">日付</th>"))
        self.assertLess(printed.find(">現場名</th>"), printed.find(">上位企業</th>"))
        self.assertEqual(printed.count("手元（手元花子）"), 1)
        self.assertIn("−4200", printed)
        self.assertIn("圧接", printed)
        self.assertIn("溶接", printed)


class VoucherSearchTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.worker = Worker.objects.create(name="検索職人", worker_type="職人")
        self.record = WorkRecord.objects.create(
            voucher_no="S-100",
            date=date(2026, 9, 8),
            site="検索現場",
            work_type="圧接",
            worker="職人",
            helpers="検索職人",
            general_contractor="元請Z",
            total_price=500,
        )
        WorkRecord.objects.create(
            voucher_no="S-100",
            date=date(2026, 9, 8),
            site="検索現場",
            work_type="溶接",
            worker="職人",
            helpers="検索職人",
            general_contractor="元請Z",
            total_price=200,
        )
        WorkRecord.objects.create(
            voucher_no="OTHER",
            date=date(2026, 9, 1),
            site="別現場",
            work_type="圧接",
            worker="職人",
            total_price=100,
        )

    def test_search_requires_condition(self):
        response = self.client.get(reverse("workrecord_search"))
        self.assertContains(response, "条件を入れて")
        self.assertNotContains(response, "S-100")

    def test_search_by_voucher_and_site(self):
        response = self.client.get(reverse("workrecord_search"), {
            "voucher_no": "S-1",
            "site": "検索",
        })
        self.assertContains(response, "S-100")
        self.assertContains(response, "検索現場")
        self.assertNotContains(response, "OTHER")
        self.assertContains(response, "2")
        self.assertContains(response, "詳細")

    def test_search_by_worker_and_date(self):
        response = self.client.get(reverse("workrecord_search"), {
            "date": "2026-09-08",
            "party": f"shokunin-{self.worker.pk}",
        })
        self.assertContains(response, "S-100")
        self.assertNotContains(response, "OTHER")

    def test_result_opens_voucher_detail(self):
        response = self.client.get(reverse("workrecord_voucher_detail", args=[self.record.pk]))
        self.assertContains(response, "伝票詳細")
        self.assertContains(response, "S-100")
        self.assertContains(response, "圧接")
        self.assertContains(response, "溶接")
        self.assertContains(response, "編集")
        self.assertContains(response, "この伝票を編集")


class AdminAutocompleteTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()

    def test_site_admin_uses_contractor_autocomplete(self):
        response = self.client.get(reverse("admin:workapp_site_add"))
        self.assertContains(response, "admin-autocomplete")
        self.assertContains(response, "general_contractor")

    def test_worker_admin_hides_worker_type(self):
        response = self.client.get(reverse("admin:workapp_worker_add"))
        self.assertNotContains(response, 'name="worker_type"')
        self.assertContains(response, "作業員名")
        self.assertContains(response, 'name="use_common_rate"')
        self.assertContains(response, "独自単価")
        self.assertContains(response, "workerrate_set")


class WorkerRateChoiceTests(TestCase):
    def setUp(self):
        self.size = WorkSize.objects.create(name="D19")
        WorkerDefaultRate.objects.create(work_size=self.size, unit_price=800)
        self.common_worker = Worker.objects.create(name="共通さん", use_common_rate=True)
        self.own_worker = Worker.objects.create(name="独自さん", use_common_rate=False)
        WorkerRate.objects.create(worker=self.common_worker, work_size=self.size, unit_price=999)
        WorkerRate.objects.create(worker=self.own_worker, work_size=self.size, unit_price=650)

    def test_common_rate_ignores_own_row(self):
        self.assertEqual(_worker_unit_price(self.common_worker, self.size), 800)

    def test_own_rate_ignores_common(self):
        self.assertEqual(_worker_unit_price(self.own_worker, self.size), 650)

    def test_get_unit_price_api_follows_flag(self):
        user = User.objects.create_user("rate-user", password="test-pass-123")
        self.client.force_login(user)
        common = self.client.get(reverse("get_unit_price"), {
            "type": "worker",
            "id": self.common_worker.pk,
            "size": self.size.pk,
        })
        own = self.client.get(reverse("get_unit_price"), {
            "type": "worker",
            "id": self.own_worker.pk,
            "size": self.size.pk,
        })
        self.assertEqual(common.json()["unit_price"], 800)
        self.assertEqual(own.json()["unit_price"], 650)




