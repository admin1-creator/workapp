import os
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Company,
    GeneralContractor,
    PrintedDocument,
    PrintedDocumentItem,
    Site,
    WorkRecord,
    WorkSize,
    Worker,
    WorkerDefaultRate,
    WorkerRate,
    closing_period_for,
    previous_period_for,
)
from unittest.mock import patch

from .views import (
    _amount_at_percent,
    _temoto_allocation,
    _apply_voucher_save,
    _collect_posted_work_lines,
    _moto_work_rows,
    _next_month_end_label,
    _ouen_work_rows,
    _pay_tax_context,
    _shokunin_work_rows,
    _temoto_line_amounts,
    _temoto_work_rows,
    _with_temoto_deduction,
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
            party_kind="元請",
            general_contractor="元請A",
            total_price=1000,
        )
        WorkRecord.objects.create(
            voucher_no="V1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            party_kind="職人",
            helpers="職人太郎",
            general_contractor="元請A",
            total_price=800,
        )
        WorkRecord.objects.create(
            voucher_no="V1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            party_kind="手元",
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
            party_kind="応援",
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
        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "worker",
                "worker_id": self.shokunin.pk,
            },
        )
        self.assertContains(listed, "現場X（元請A）")
        self.assertContains(listed, "<th>上位企業</th>")

        WorkRecord.objects.filter(helpers="職人太郎").update(primary_company="元請B")
        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "worker",
                "worker_id": self.shokunin.pk,
            },
        )
        self.assertContains(listed, "現場X（元請A）")
        self.assertContains(listed, "元請B")
        self.assertNotContains(listed, "<th>元請</th>")

    def test_list_worker_includes_craftsman_pay(self):
        response = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "worker",
                "worker_id": self.shokunin.pk,
            },
        )
        self.assertContains(response, "作業員の一覧")
        self.assertContains(response, "800")
        self.assertNotContains(response, "手元の一覧")

    def test_list_worker_includes_helper_pay(self):
        response = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "worker",
                "worker_id": self.temoto.pk,
            },
        )
        self.assertContains(response, "作業員の一覧")
        self.assertContains(response, "200")
        self.assertNotContains(response, "職人の一覧")

    def test_old_shokunin_url_with_temoto_id_still_works(self):
        response = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "shokunin",
                "worker_id": self.temoto.pk,
            },
        )
        self.assertContains(response, "手元花子")
        self.assertContains(response, "作業員の一覧")
        self.assertNotContains(response, "職人の一覧")

    def test_same_worker_craftsman_and_helper_are_combined(self):
        WorkRecord.objects.create(
            voucher_no="V2",
            date=date(2026, 9, 2),
            site="現場X",
            work_type="溶接",
            party_kind="手元",
            helpers="職人太郎",
            temoto1="職人太郎",
            general_contractor="元請A",
            total_price=150,
        )
        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "worker",
                "worker_id": self.shokunin.pk,
            },
        )
        self.assertContains(listed, "800")
        self.assertContains(listed, "150")
        self.assertContains(listed, "作業員の一覧")
        printed = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.shokunin.pk,
            },
        )
        self.assertContains(printed, "支払明細")
        self.assertContains(printed, "800")
        self.assertContains(printed, "150")

    def test_any_worker_can_be_craftsman_or_helper(self):
        from .forms import WorkRecordForm

        site = Site.objects.create(name="現場X", general_contractor=self.gc_a)
        form = WorkRecordForm(
            data={
                "voucher_no": "V9",
                "date": "2026-09-01",
                "site": str(site.pk),
                "worker": str(self.temoto.pk),
                "temoto1": str(self.shokunin.pk),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        create = self.client.get(reverse("workrecord_create"))
        self.assertContains(create, 'id="id_worker"')
        self.assertContains(create, "職人太郎")
        self.assertContains(create, "手元花子")
        self.assertContains(create, ">戻る</a>")

    def test_ouen_only_does_not_require_worker(self):
        from .forms import WorkRecordForm

        site = Site.objects.create(name="現場X", general_contractor=self.gc_a)
        form = WorkRecordForm(
            data={
                "voucher_no": "V8",
                "date": "2026-09-01",
                "site": str(site.pk),
                "company": str(self.company.pk),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_helper_without_craftsman_is_invalid(self):
        from .forms import WorkRecordForm

        site = Site.objects.create(name="現場X", general_contractor=self.gc_a)
        form = WorkRecordForm(
            data={
                "voucher_no": "V8",
                "date": "2026-09-01",
                "site": str(site.pk),
                "temoto1": str(self.temoto.pk),
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("手元がいるときは", str(form.errors))

    def test_craftsman_and_company_cannot_share_a_voucher(self):
        from .forms import WorkRecordForm

        site = Site.objects.create(name="現場X", general_contractor=self.gc_a)
        both = WorkRecordForm(
            data={
                "voucher_no": "V8",
                "date": "2026-09-01",
                "site": str(site.pk),
                "worker": str(self.shokunin.pk),
                "company": str(self.company.pk),
            }
        )
        self.assertFalse(both.is_valid())
        self.assertIn("職人と応援", str(both.errors))
        ouen_helper = WorkRecordForm(
            data={
                "voucher_no": "V8",
                "date": "2026-09-01",
                "site": str(site.pk),
                "company": str(self.company.pk),
                "temoto1": str(self.temoto.pk),
            }
        )
        self.assertFalse(ouen_helper.is_valid())
        self.assertIn("応援の伝票に手元", str(ouen_helper.errors))
        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "shokunin",
                "worker_id": self.shokunin.pk,
            },
        )
        self.assertNotContains(listed, ">区分</th>")

    def test_period_print_titles(self):
        base = reverse("workrecord_print_period")
        moto = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "moto",
                "moto_company": self.gc_a.pk,
            },
        )
        self.assertContains(moto, "請求書")
        self.assertContains(moto, "1,000")
        self.assertContains(moto, "元請A")
        self.assertContains(moto, "御中")
        self.assertContains(moto, "下記の通り、ご請求申し上げます。")
        self.assertContains(moto, "請求先")
        self.assertContains(moto, "発行")
        self.assertContains(moto, "自社")
        self.assertContains(moto, "〒000-0000　自社県自社市1-1-1")
        self.assertContains(moto, "TEL　000-0000-0000")
        self.assertContains(moto, "代表取締役　自社社長名")
        self.assertContains(moto, "税込合計金額")
        self.assertContains(moto, "tax-underline")
        self.assertContains(moto, "¥1,100")
        self.assertContains(moto, "税抜合計 1,000")
        self.assertContains(moto, "消費税 10％ 100")
        self.assertNotContains(moto, "税抜合計 1,000（100）")

        pay = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.shokunin.pk,
            },
        )
        self.assertContains(pay, "支払明細")
        self.assertNotContains(pay, "支払明細（職人）")
        self.assertContains(pay, "800")
        self.assertContains(pay, "職人太郎")
        self.assertContains(pay, "様")
        self.assertContains(pay, "支払先")
        self.assertContains(pay, "税込合計金額")
        self.assertContains(pay, "¥880")
        self.assertContains(pay, "税抜合計 800")
        self.assertContains(pay, "消費税 10％ 80")
        self.assertNotContains(pay, "税抜合計 800（80）")
        self.assertNotContains(pay, "下記の通りお支払いいたします。")
        self.assertNotContains(pay, "ご請求申し上げます。")
        self.assertContains(pay, 'data-sheet-title="支払明細"')
        self.assertContains(pay, 'data-party-name="職人太郎"')
        self.assertContains(pay, "sheet-page-no")
        self.assertContains(pay, '"No." + page + "/" + totalPages')
        self.assertContains(pay, "代表取締役　自社社長名")
        self.assertContains(pay, "支払書をつける")
        self.assertNotContains(pay, "<h1>支払書</h1>")
        self.assertNotContains(pay, "手元の一覧")
        self.assertNotContains(pay, "鑑（支払）")

        with_kagami = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.shokunin.pk,
                "kagami": "1",
            },
        )
        self.assertContains(with_kagami, "支払書")
        self.assertContains(with_kagami, "<h1>支払書</h1>")
        self.assertContains(with_kagami, "支払明細")
        self.assertContains(with_kagami, "代表取締役　自社社長名")
        self.assertContains(with_kagami, "税込合計金額")
        self.assertContains(with_kagami, "税抜合計 800")
        self.assertContains(with_kagami, "消費税 10％ 80")
        self.assertContains(with_kagami, "下記の通りお支払いいたします。")
        self.assertContains(with_kagami, "売上")
        self.assertContains(with_kagami, 'name="kagami_amount_1"')
        self.assertContains(with_kagami, 'name="kagami_amount_10"')
        self.assertContains(with_kagami, ">合計<")
        self.assertContains(with_kagami, "800")
        self.assertContains(with_kagami, "支払書を保存")
        self.assertContains(with_kagami, "2026年10月末")
        self.assertContains(with_kagami, "対象期間：")

        helper_kagami = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.temoto.pk,
                "kagami": "1",
            },
        )
        self.assertContains(helper_kagami, "<h1>支払書</h1>")
        self.assertContains(helper_kagami, "2026年10月末")

        moto_kagami = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "moto",
                "moto_company": self.gc_a.pk,
                "kagami": "1",
            },
        )
        self.assertNotContains(moto_kagami, "<h1>支払書</h1>")

        helper = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.temoto.pk,
            },
        )
        self.assertContains(helper, "支払明細")
        self.assertNotContains(helper, "支払明細（手元）")
        self.assertContains(helper, "200")

        ouen = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "ouen",
                "company_id": self.company.pk,
            },
        )
        self.assertContains(ouen, "支払明細（応援）")
        self.assertContains(ouen, "900")
        self.assertNotContains(ouen, "支払書をつける")
        ouen_kagami = self.client.get(
            base,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "ouen",
                "company_id": self.company.pk,
                "kagami": "1",
            },
        )
        self.assertNotContains(ouen_kagami, "<h1>支払書</h1>")
        self.assertNotContains(ouen_kagami, "支払書をつける")

    def test_print_keeps_voucher_together_and_uses_page_caps(self):
        html = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "moto",
                "moto_company": self.gc_a.pk,
            },
        ).content.decode()
        self.assertIn("var firstPage = 16;", html)
        self.assertIn("var nextPage = 25;", html)
        self.assertIn("function voucherGroups(rows)", html)
        self.assertIn("current.length + group.length > cap()", html)
        self.assertIn('data-voucher="V1"', html)

    def test_worker_kagami_shows_next_month_end_not_period(self):
        html = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.shokunin.pk,
                "kagami": "1",
            },
        ).content.decode()
        start = html.find('class="kagami"')
        end = html.find("</section>", start)
        block = html[start:end]
        self.assertIn("2026年10月末", block)
        self.assertNotIn("対象期間", block)
        self.assertIn("<h1>支払書</h1>", block)

    def test_manual_unit_and_amount_print_on_moto_sheet(self):
        contractor = GeneralContractor.objects.create(name="元請手入力")
        WorkRecord.objects.create(
            voucher_no="MAN1",
            date=date(2026, 9, 10),
            site="現場手",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請手入力",
            work_size="D19",
            work_amount=4,
            unit_price=250,
            total_price=1000,
            price_mode="manual",
        )
        html = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        ).content.decode()
        self.assertIn("250", html)
        self.assertIn("1,000", html)
        self.assertIn("D19", html)

    def test_kagami_saves_lines_and_uses_kagami_tax(self):
        period = reverse("workrecord_print_period")
        params = {
            "from_date": "2026-09-01",
            "to_date": "2026-09-30",
            "kind": "worker",
            "worker_id": str(self.shokunin.pk),
            "kagami": "1",
        }
        save = self.client.post(
            period,
            {
                **params,
                "action": "save_kagami",
                "kagami_item_1": "売上",
                "kagami_amount_1": "800",
                "kagami_item_2": "調整",
                "kagami_amount_2": "100",
            },
        )
        self.assertEqual(save.status_code, 302)
        self.assertIn("kagami=1", save.url)

        shown = self.client.get(period, params)
        self.assertContains(shown, 'value="調整"')
        self.assertContains(shown, 'name="kagami_amount_2"')
        self.assertContains(shown, 'value="100"')
        self.assertContains(shown, "税抜合計 900")
        self.assertContains(shown, "消費税 10％ 90")
        self.assertContains(shown, "¥990")
        self.assertContains(shown, "税抜合計 800")
        self.assertContains(shown, "消費税 10％ 80")
        self.assertContains(shown, "¥880")

        record = WorkRecord.objects.get(party_kind="職人", helpers="職人太郎")
        single = reverse("workrecord_print", args=[record.pk])
        single_save = self.client.post(
            single,
            {
                "kind": "worker",
                "worker_id": str(self.shokunin.pk),
                "kagami": "1",
                "action": "save_kagami",
                "kagami_item_1": "売上",
                "kagami_amount_1": "800",
                "kagami_item_2": "手当",
                "kagami_amount_2": "50",
            },
        )
        self.assertEqual(single_save.status_code, 302)
        single_shown = self.client.get(
            single,
            {
                "kind": "worker",
                "worker_id": self.shokunin.pk,
                "kagami": "1",
            },
        )
        self.assertContains(single_shown, 'value="手当"')
        self.assertContains(single_shown, "税抜合計 850")
        self.assertContains(single_shown, "消費税 10％ 85")
        self.assertContains(single_shown, "税抜合計 800")
        self.assertContains(single_shown, "消費税 10％ 80")

    def test_temoto_and_ouen_print_match_shokunin_columns(self):
        headers = [
            "<th>伝票番号</th>",
            "<th>日付</th>",
            "<th>現場名</th>",
            "<th>上位企業</th>",
            "<th>作業内容</th>",
            "<th>寸法</th>",
            "<th>数量</th>",
            "<th>単価</th>",
            '<th class="col-amount">金額</th>',
        ]
        period = reverse("workrecord_print_period")
        shokunin = self.client.get(
            period,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.shokunin.pk,
            },
        )
        temoto = self.client.get(
            period,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.temoto.pk,
            },
        )
        ouen = self.client.get(
            period,
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "ouen",
                "company_id": self.company.pk,
            },
        )
        for html in (shokunin, temoto, ouen):
            for header in headers:
                self.assertContains(html, header)
        self.assertNotContains(temoto, 'class="minus"')
        self.assertNotContains(ouen, 'class="minus"')

        temoto_row = WorkRecord.objects.get(party_kind="手元")
        ouen_row = WorkRecord.objects.get(party_kind="応援")
        temoto_one = self.client.get(
            reverse("workrecord_print", args=[temoto_row.pk]), {"kind": "temoto"}
        )
        ouen_one = self.client.get(
            reverse("workrecord_print", args=[ouen_row.pk]), {"kind": "ouen"}
        )
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
            party_kind="手元",
            helpers="手元花子",
            general_contractor="元請A",
            temoto1="手元花子",
            unit_price=50,
            work_amount=2,
            total_price=50,
        )
        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "worker",
                "worker_id": self.temoto.pk,
            },
        )
        self.assertContains(listed, "V1", count=1)
        self.assertContains(listed, "圧接、溶接")
        self.assertContains(listed, "250")
        self.assertNotContains(listed, ">50<")

        printed = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "worker",
                "worker_id": self.temoto.pk,
            },
        )
        self.assertContains(printed, "<td>V1</td>", count=1)
        self.assertContains(printed, "圧接、溶接")
        self.assertContains(printed, "250")

    def test_create_form_is_grouped(self):
        response = self.client.get(reverse("workrecord_create"))
        self.assertContains(response, "1. 基本")
        self.assertContains(response, "2. 職人・手元・応援")
        self.assertContains(response, "3. 作業内容")
        self.assertContains(response, 'name="work_type_1"')
        self.assertContains(response, 'class="ime-hiragana"')
        self.assertContains(response, 'lang="ja"')
        self.assertContains(response, 'style="ime-mode: active"')
        self.assertContains(response, 'name="rate_billing_1"')
        self.assertContains(response, 'name="voucher_no"')
        self.assertContains(response, 'name="price_mode_1"')
        self.assertContains(response, ">印</th>")
        self.assertContains(response, 'name="size_mark_1"')
        self.assertContains(response, ">請求単価</th>")
        self.assertContains(response, ">請求額</th>")
        self.assertContains(response, ">支払単価</th>")
        self.assertContains(response, ">支払額</th>")
        self.assertContains(response, ">応援単価</th>")
        self.assertContains(response, ">応援額</th>")
        self.assertContains(response, 'class="rate-unit rate-billing"')
        self.assertContains(response, "col-rate")
        self.assertContains(response, "col-sum")
        self.assertNotContains(response, 'class="mode">単価</th>')

    def test_empty_work_type_inherits_previous_line(self):
        lines = _collect_posted_work_lines(
            {
                "work_type_1": "圧接",
                "work_size_1": "1",
                "work_amount_1": "2",
                "work_type_2": "",
                "work_size_2": "2",
                "work_amount_2": "3",
                "work_type_3": "",
                "work_size_3": "",
                "work_amount_3": "",
            }
        )
        self.assertEqual(lines["work_types"], ["圧接", "圧接"])
        self.assertEqual(lines["work_amounts"], [2, 3])
        self.assertFalse(lines["type_missing"])
        self.assertEqual(lines["line_error"], "")

    def test_line_over_db_limits_is_rejected(self):
        long_type = _collect_posted_work_lines({"work_type_1": "圧" * 256})
        self.assertIn("255文字", long_type["line_error"])
        huge_amount = _collect_posted_work_lines(
            {"work_type_1": "圧接", "work_amount_1": "3000000000"}
        )
        self.assertIn("作業量", huge_amount["line_error"])
        huge_money = _collect_posted_work_lines(
            {
                "work_type_1": "圧接",
                "work_amount_1": "2",
                "manual_billing_1": "2000000000",
            }
        )
        self.assertIn("金額", huge_money["line_error"])

    def test_overwrite_keeps_rows_when_save_fails(self):
        record = WorkRecord.objects.create(
            voucher_no="KEEP1",
            date=date(2026, 9, 1),
            site="現場X",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請A",
            total_price=100,
        )
        with patch(
            "workapp.views._save_work_record",
            side_effect=RuntimeError("save failed"),
        ):
            with self.assertRaises(RuntimeError):
                _apply_voucher_save(
                    "overwrite",
                    [record.pk],
                    [
                        {
                            "voucher_no": "KEEP1",
                            "date": date(2026, 9, 1),
                            "site": "現場X",
                            "work_type": "溶接",
                            "party_kind": "元請",
                            "general_contractor": "元請A",
                        }
                    ],
                )
        self.assertTrue(WorkRecord.objects.filter(pk=record.pk).exists())

    def test_continue_input_after_save_opens_blank_form(self):
        session = self.client.session
        session["basic_record"] = {
            "voucher_no": "NEXT1",
            "date": "2026-09-01",
            "site": "現場X",
            "general_contractor": "元請A",
            "primary_company": "",
            "billing_contractor": "元請A",
            "worker": "職人太郎",
            "temoto1": "",
            "temoto2": "",
            "temoto3": "",
            "company": "",
            "work_types": ["圧接"],
            "work_sizes": [""],
            "work_amounts": [1],
            "remarks": [""],
            "price_modes": ["master"],
            "manual_billings": [None],
            "manual_pays": [None],
            "manual_ouens": [None],
        }
        session["review_saved"] = True
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        review = self.client.get(reverse("workrecord_review"))
        self.assertContains(review, "保存しました。")
        self.assertContains(review, "続けて入力")
        blank = self.client.get(reverse("workrecord_create"))
        self.assertContains(blank, "伝票入力")
        self.assertNotContains(blank, "伝票の編集")
        self.assertNotContains(blank, 'value="NEXT1"')

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
        shokunin_rows, shokunin_totals, _alloc = _shokunin_work_rows(
            data, self.shokunin
        )
        self.assertEqual(shokunin_totals["subtotal"], 3000)
        self.assertEqual(shokunin_totals["total"], 1950)
        _temoto_rows, temoto_totals, _alloc, _shares = _temoto_work_rows(
            data, self.shokunin
        )
        self.assertEqual(temoto_totals["total"], 1050)
        ouen_rows, ouen_totals, _alloc = _ouen_work_rows(data)
        self.assertEqual(ouen_rows[0]["total_price"], 2000)
        self.assertEqual(ouen_totals["total"], 2000)
        self.assertNotEqual(ouen_rows[0]["total_price"], shokunin_totals["subtotal"])

    def test_manual_line_uses_typed_unit_times_qty(self):
        data = {
            "voucher_no": "X2",
            "date": date(2026, 9, 1),
            "site": "現場X",
            "general_contractor": "元請A",
            "worker": "職人太郎",
            "worker_id": self.shokunin.pk,
            "company": "応援B",
            "work_types": ["その他"],
            "work_sizes": [""],
            "work_amounts": [10],
            "remarks": [""],
            "price_modes": ["manual"],
            "manual_billings": [None],
            "manual_pays": [None],
            "manual_ouens": [None],
            "rate_billings": [200],
            "rate_pays": [150],
            "rate_ouens": [100],
        }
        moto_rows, moto_totals, _alloc = _moto_work_rows(data)
        self.assertEqual(moto_rows[0]["unit_price"], 200)
        self.assertEqual(moto_rows[0]["total_price"], 2000)
        self.assertEqual(moto_totals["total"], 2000)
        shokunin_rows, shokunin_totals, _alloc = _shokunin_work_rows(
            data, self.shokunin
        )
        self.assertEqual(shokunin_rows[0]["unit_price"], 150)
        self.assertEqual(shokunin_totals["subtotal"], 1500)
        ouen_rows, ouen_totals, _alloc = _ouen_work_rows(data)
        self.assertEqual(ouen_rows[0]["unit_price"], 100)
        self.assertEqual(ouen_rows[0]["total_price"], 1000)

    def test_edit_form_is_grouped(self):
        Site.objects.create(name="現場X", general_contractor=self.gc_a)
        shokunin_row = WorkRecord.objects.get(party_kind="職人")
        response = self.client.get(
            reverse("workrecord_edit", args=[shokunin_row.pk]), follow=True
        )
        self.assertContains(response, "伝票の編集")
        self.assertContains(response, "1. 基本")
        self.assertContains(response, "2. 職人・手元・応援")
        self.assertContains(response, "3. 作業内容")
        self.assertContains(response, 'name="work_type_1"')
        self.assertContains(response, "圧接")
        self.assertContains(response, "職人太郎")
        self.assertNotContains(response, "この行は")

        moto_row = WorkRecord.objects.get(party_kind="元請")
        moto_edit = self.client.get(
            reverse("workrecord_edit", args=[moto_row.pk]), follow=True
        )
        self.assertContains(moto_edit, "伝票の編集")
        self.assertContains(moto_edit, "圧接")

    def test_edit_form_shows_saved_billing_rate_and_amount(self):
        WorkSize.objects.create(name="D19表示")
        Site.objects.create(name="現場X", general_contractor=self.gc_a)
        WorkRecord.objects.filter(voucher_no="V1").update(
            work_size="D19表示",
            work_amount=2,
        )
        WorkRecord.objects.filter(voucher_no="V1", party_kind="元請").update(
            unit_price=3210
        )
        row = WorkRecord.objects.get(voucher_no="V1", party_kind="職人")
        edit = self.client.get(reverse("workrecord_edit", args=[row.pk]), follow=True)
        self.assertContains(edit, 'name="rate_billing_1" value="3210"')
        self.assertContains(edit, 'name="manual_billing_1" value="6420"')
        self.assertContains(edit, "if (billingId) setNum")
        self.assertContains(
            edit, "if (window.refreshWorkLinePrices) window.refreshWorkLinePrices();"
        )

    def test_create_and_edit_use_searchable_site_and_contractor(self):
        site = Site.objects.create(
            name="現場X",
            name_kana="ゲンバエックス",
            general_contractor=self.gc_a,
        )
        self.shokunin.name_kana = "ショクニンタロウ"
        self.shokunin.save(update_fields=["name_kana"])
        create = self.client.get(reverse("workrecord_create"))
        self.assertContains(create, "select2.full.min.js")
        self.assertContains(create, "select_search.js")
        self.assertContains(create, f'data-contractor="{self.gc_a.pk}"')
        self.assertContains(create, 'data-reading="ゲンバエックス"')
        self.assertContains(create, 'data-reading="ショクニンタロウ"')
        self.assertContains(create, "現場X")

        row = WorkRecord.objects.get(party_kind="職人")
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
            party_kind="元請",
            general_contractor="元請A",
            primary_company="元請B",
            billing_contractor="元請B",
            total_price=111,
        )
        a_list = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "moto",
                "moto_company": self.gc_a.pk,
            },
        )
        self.assertNotContains(a_list, "BILL")
        b_list = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "moto",
                "moto_company": self.gc_b.pk,
            },
        )
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
        self.assertEqual(
            previous_period_for((date(2026, 8, 21), date(2026, 9, 20)), 20),
            (date(2026, 7, 21), date(2026, 8, 20)),
        )
        self.assertEqual(
            previous_period_for((date(2026, 9, 21), date(2026, 10, 20)), 20),
            (date(2026, 8, 21), date(2026, 9, 20)),
        )

    def test_list_shows_unset_closing_day(self):
        contractor = GeneralContractor.objects.create(name="元請C")
        response = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        )
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
            party_kind="元請",
            general_contractor="元請D",
            total_price=100,
        )
        WorkRecord.objects.create(
            voucher_no="OUT",
            date=start - timedelta(days=1),
            site="現場Y",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請D",
            total_price=50,
        )
        response = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        )
        self.assertContains(response, "毎月 20 日")
        self.assertContains(response, str(start))
        self.assertContains(response, str(end))
        self.assertContains(response, "未請求：1 件")
        self.assertContains(response, "この期間で印刷")
        self.assertContains(response, "前期")
        prev_start, prev_end = contractor.previous_closing_period(timezone.localdate())
        self.assertContains(response, str(prev_start))
        self.assertContains(response, str(prev_end))

    def test_save_closing_day_from_list(self):
        contractor = GeneralContractor.objects.create(name="元請E")
        response = self.client.post(
            reverse("workrecord_list"),
            {
                "save_closing_day": "1",
                "kind": "moto",
                "moto_company": contractor.pk,
                "closing_day": "25",
            },
        )
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
            party_kind="元請",
            general_contractor="元請F",
            total_price=300,
        )
        response = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        )
        self.assertContains(response, "請求書")
        self.assertContains(response, "300")

    def test_period_print_prefills_closing_period(self):
        contractor = GeneralContractor.objects.create(name="元請G", closing_day=20)
        start, end = contractor.current_closing_period(timezone.localdate())
        response = self.client.get(
            reverse("workrecord_print_period"),
            {
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        )
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
        self.assertContains(response, "株式会社　自社")

    def test_mark_printed_freezes_amount_and_locks_edit(self):
        contractor = GeneralContractor.objects.create(name="元請H", closing_day=31)
        record = WorkRecord.objects.create(
            voucher_no="P1",
            date=date(2026, 9, 5),
            site="現場P",
            work_type="圧接",
            party_kind="元請",
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
        self.assertContains(frozen, 'class="stamp screen-only"')
        self.assertContains(frozen, "金額は印刷時点で固定")
        self.assertContains(frozen, "1,000")
        self.assertNotContains(frozen, "9999")

        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        )
        self.assertContains(listed, "印刷済み")
        self.assertContains(listed, "未請求：0 件")

        edit = self.client.get(reverse("workrecord_edit", args=[record.pk]))
        self.assertContains(edit, "印刷済みのため")

        cancel = self.client.post(
            period,
            {
                **params,
                "action": "cancel_printed",
                "document_id": frozen.context["printed_document"].id,
            },
        )
        self.assertEqual(cancel.status_code, 302)
        edit_again = self.client.get(
            reverse("workrecord_edit", args=[record.pk]), follow=True
        )
        self.assertNotContains(edit_again, "印刷済みのため")
        self.assertContains(edit_again, "伝票の編集")

    def test_cancel_one_party_allows_edit_when_other_party_still_printed(self):
        contractor = GeneralContractor.objects.create(name="元請J")
        company = Company.objects.create(name="応援J")
        moto = WorkRecord.objects.create(
            voucher_no="P2",
            date=date(2026, 8, 15),
            site="現場P2",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請J",
            total_price=2000,
        )
        ouen = WorkRecord.objects.create(
            voucher_no="P2",
            date=date(2026, 8, 15),
            site="現場P2",
            work_type="圧接",
            party_kind="応援",
            company="応援J",
            general_contractor="元請J",
            total_price=1500,
        )
        period = reverse("workrecord_print_period")
        moto_params = {
            "from_date": "2026-08-01",
            "to_date": "2026-08-31",
            "kind": "moto",
            "moto_company": str(contractor.pk),
        }
        ouen_params = {
            "from_date": "2026-08-01",
            "to_date": "2026-08-31",
            "kind": "ouen",
            "company_id": str(company.pk),
        }
        self.assertEqual(
            self.client.post(
                period, {**moto_params, "action": "mark_printed"}
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                period, {**ouen_params, "action": "mark_printed"}
            ).status_code,
            302,
        )
        ouen_printed = self.client.get(period, ouen_params)
        cancel = self.client.post(
            period,
            {
                **ouen_params,
                "action": "cancel_printed",
                "document_id": ouen_printed.context["printed_document"].id,
            },
        )
        self.assertEqual(cancel.status_code, 302)
        self.assertEqual(
            PrintedDocument.objects.get(
                pk=ouen_printed.context["printed_document"].id
            ).status,
            "cancelled",
        )

        ouen_edit = self.client.get(
            reverse("workrecord_edit", args=[ouen.pk]), follow=True
        )
        self.assertNotContains(ouen_edit, "印刷済みのため")
        self.assertContains(ouen_edit, "伝票の編集")
        self.assertContains(ouen_edit, "印刷済みの元請の行は、保存しても変更しません")

        moto_edit = self.client.get(reverse("workrecord_edit", args=[moto.pk]))
        self.assertContains(moto_edit, "印刷済みのため")

        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "ouen",
                "company_id": company.pk,
            },
        )
        self.assertContains(listed, "未請求")
        self.assertNotContains(listed, "印刷済み")

        reprint = self.client.post(period, {**ouen_params, "action": "mark_printed"})
        self.assertEqual(reprint.status_code, 302)
        new_doc = PrintedDocument.objects.filter(
            kind="ouen",
            party_company=company,
            status="printed",
        ).latest("id")
        self.assertNotEqual(new_doc.id, ouen_printed.context["printed_document"].id)
        self.assertTrue(WorkRecord.objects.filter(pk=moto.pk).exists())

    def test_single_print_uses_payment_title(self):
        record = WorkRecord.objects.create(
            voucher_no="S1",
            date=date(2026, 9, 5),
            site="現場S",
            work_type="圧接",
            party_kind="職人",
            helpers="職人太郎",
            total_price=800,
        )
        response = self.client.get(
            reverse("workrecord_print", args=[record.pk]), {"kind": "worker"}
        )
        self.assertContains(response, "支払明細")
        self.assertNotContains(response, "支払明細（職人）")
        self.assertContains(response, "請求支払い入力")
        self.assertContains(response, "職人太郎")
        self.assertContains(response, "様")
        self.assertContains(response, "自社")

    def test_moto_print_column_order(self):
        contractor = GeneralContractor.objects.create(name="元請I")
        WorkRecord.objects.create(
            voucher_no="M1",
            date=date(2026, 9, 5),
            site="現場M",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請I",
            work_size="D19",
            work_amount=10,
            unit_price=100,
            total_price=1000,
        )
        html = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        ).content.decode()
        self.assertIn(
            "<th>伝票番号</th>",
            html,
        )
        self.assertLess(html.find("伝票番号"), html.find("日付"))
        self.assertLess(html.find(">日付</th>"), html.find(">現場名</th>"))
        self.assertLess(html.find(">現場名</th>"), html.find(">上位企業</th>"))
        self.assertLess(html.find(">上位企業</th>"), html.find(">作業内容</th>"))
        self.assertIn("現場M", html)
        self.assertIn("元請I", html)
        self.assertNotIn("現場M（元請I）", html)
        self.assertIn('class="site-span" colspan="7">現場M</td>', html)
        self.assertIn("site-row", html)
        self.assertLess(html.find("site-row"), html.find(">元請I</td>"))
        self.assertIn("page-fill", html)
        self.assertLess(html.find(">作業内容</th>"), html.find(">寸法</th>"))
        self.assertLess(html.find(">寸法</th>"), html.find(">数量</th>"))
        self.assertLess(html.find(">数量</th>"), html.find(">単価</th>"))
        self.assertLess(html.find(">単価</th>"), html.find(">金額</th>"))
        self.assertIn("col.col-site { width: 12%; }", html)
        self.assertIn("col.col-work { width: 20%; }", html)
        self.assertIn("col.col-size { width: 14%; }", html)
        self.assertNotIn(
            "table.data th.col-amount {\n            text-decoration: underline", html
        )

    def test_single_print_shows_all_voucher_work_lines(self):
        first = WorkRecord.objects.create(
            voucher_no="M2",
            date=date(2026, 9, 5),
            site="現場J",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請J",
            total_price=1000,
        )
        WorkRecord.objects.create(
            voucher_no="M2",
            date=date(2026, 9, 5),
            site="現場J",
            work_type="溶接",
            party_kind="元請",
            general_contractor="元請J",
            total_price=400,
        )
        html = self.client.get(
            reverse("workrecord_print", args=[first.pk]),
            {"kind": "moto"},
        )
        self.assertContains(html, "圧接")
        self.assertContains(html, "溶接")
        self.assertContains(html, "1,000")
        self.assertContains(html, "400")

    def test_print_blanks_repeated_fields_within_voucher(self):
        contractor = GeneralContractor.objects.create(name="元請R")
        WorkRecord.objects.create(
            voucher_no="R1",
            date=date(2026, 9, 5),
            site="現場R",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請R",
            primary_company="一次R",
            total_price=1000,
        )
        WorkRecord.objects.create(
            voucher_no="R1",
            date=date(2026, 9, 5),
            site="現場R",
            work_type="圧接",
            party_kind="元請",
            general_contractor="元請R",
            primary_company="一次R",
            total_price=400,
        )
        WorkRecord.objects.create(
            voucher_no="R1",
            date=date(2026, 9, 6),
            site="現場R2",
            work_type="溶接",
            party_kind="元請",
            general_contractor="元請R",
            primary_company="一次R",
            total_price=200,
        )
        html = self.client.get(
            reverse("workrecord_print_period"),
            {
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "kind": "moto",
                "moto_company": contractor.pk,
            },
        ).content.decode()
        self.assertEqual(html.count("<td>R1</td>"), 1)
        self.assertEqual(html.count(">9/5</td>"), 1)
        self.assertEqual(html.count(">9/6</td>"), 1)
        self.assertEqual(html.count(">現場R<"), 1)
        self.assertEqual(html.count(">現場R2<"), 1)
        self.assertEqual(html.count(">一次R</td>"), 1)
        self.assertEqual(html.count(">圧接</td>"), 1)
        self.assertEqual(html.count(">溶接</td>"), 1)

    def test_shokunin_list_and_print_show_helper_minus(self):
        worker = Worker.objects.create(name="職人次郎", worker_type="職人")
        record = WorkRecord.objects.create(
            voucher_no="H1",
            date=date(2026, 9, 5),
            site="現場H",
            work_type="圧接",
            party_kind="職人",
            helpers="職人次郎",
            general_contractor="元請A",
            work_size="D19",
            size_mark="長尺",
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
            party_kind="職人",
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
        listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "shokunin",
                "worker_id": worker.pk,
            },
        ).content.decode()
        remark_pos = listed.find(">備考</th>")
        amount_pos = listed.find(">合計金額</th>")
        self.assertGreater(remark_pos, amount_pos)
        self.assertEqual(listed.count("手元（手元花子）"), 1)
        self.assertIn("−4200", listed)
        self.assertLess(listed.find(">圧接</td>"), listed.find(">溶接</td>"))
        self.assertLess(listed.find(">溶接</td>"), listed.find("手元（手元花子）"))
        worker_listed = self.client.get(
            reverse("workrecord_list"),
            {
                "kind": "worker",
                "worker_id": worker.pk,
            },
        ).content.decode()
        self.assertLess(
            worker_listed.find(">圧接</td>"), worker_listed.find(">溶接</td>")
        )
        self.assertLess(
            worker_listed.find(">溶接</td>"), worker_listed.find("手元（手元花子）")
        )
        self.assertEqual(listed.count(">印刷</a>"), 1)
        self.assertEqual(listed.count(">編集</a>"), 1)

        printed = self.client.get(
            reverse("workrecord_print", args=[record.pk]),
            {
                "kind": "shokunin",
            },
        ).content.decode()
        self.assertLess(printed.find(">伝票番号</th>"), printed.find(">日付</th>"))
        self.assertLess(printed.find(">現場名</th>"), printed.find(">上位企業</th>"))
        self.assertEqual(printed.count("手元（手元花子）"), 1)
        self.assertIn("−4,200", printed)
        self.assertLess(printed.find(">圧接</td>"), printed.find(">溶接</td>"))
        self.assertLess(printed.find(">溶接</td>"), printed.find("手元（手元花子）"))
        self.assertIn('data-voucher="H1"', printed)
        self.assertIn("D19　長尺", printed)
        self.assertIn("圧接", printed)
        self.assertIn("溶接", printed)


class VoucherSearchTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.worker = Worker.objects.create(
            name="検索職人",
            name_kana="ケンサクショクニン",
            worker_type="職人",
        )
        self.site = Site.objects.create(
            name="検索現場",
            name_kana="ケンサクゲンバ",
            general_contractor=GeneralContractor.objects.create(name="元請Z"),
        )
        self.record = WorkRecord.objects.create(
            voucher_no="S-100",
            date=date(2026, 9, 8),
            site="検索現場",
            work_type="圧接",
            party_kind="職人",
            helpers="検索職人",
            general_contractor="元請Z",
            total_price=500,
        )
        WorkRecord.objects.create(
            voucher_no="S-100",
            date=date(2026, 9, 8),
            site="検索現場",
            work_type="溶接",
            party_kind="職人",
            helpers="検索職人",
            general_contractor="元請Z",
            total_price=200,
        )
        WorkRecord.objects.create(
            voucher_no="OTHER",
            date=date(2026, 9, 1),
            site="別現場",
            work_type="圧接",
            party_kind="職人",
            total_price=100,
        )

    def test_search_requires_condition(self):
        response = self.client.get(reverse("workrecord_search"))
        self.assertContains(response, "条件を入れて")
        self.assertNotContains(response, "S-100")

    def test_search_by_voucher_and_site(self):
        response = self.client.get(
            reverse("workrecord_search"),
            {
                "voucher_no": "S-1",
                "site": self.site.pk,
            },
        )
        html = response.content.decode()
        self.assertContains(response, "S-100")
        self.assertContains(response, "検索現場")
        self.assertNotContains(response, "OTHER")
        self.assertContains(response, "2")
        self.assertContains(response, "詳細")
        self.assertIn('<select name="site">', html)
        self.assertNotIn('<input type="text" name="site"', html)
        self.assertIn('data-reading="ケンサクゲンバ"', html)
        self.assertIn('data-reading="ケンサクショクニン"', html)
        self.assertIn("select_search.js", html)
        script_path = Path(__file__).resolve().parent / "static" / "workapp" / "js"
        script = (script_path / "select_search.js").read_text(encoding="utf-8")
        self.assertIn('normalize("NFKC")', script)
        self.assertIn("compositionstart", script)
        self.assertIn("compositionend", script)

    def test_search_by_worker_and_date(self):
        response = self.client.get(
            reverse("workrecord_search"),
            {
                "date": "2026-09-08",
                "party": f"shokunin-{self.worker.pk}",
            },
        )
        self.assertContains(response, "S-100")
        self.assertNotContains(response, "OTHER")

    def test_result_opens_voucher_detail(self):
        response = self.client.get(
            reverse("workrecord_voucher_detail", args=[self.record.pk])
        )
        self.assertContains(response, "伝票詳細")
        self.assertContains(response, "S-100")
        self.assertContains(response, "圧接")
        self.assertContains(response, "溶接")
        self.assertContains(response, "編集")
        self.assertContains(response, "この伝票を編集")


class WorkRecordDeleteTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.record = WorkRecord.objects.create(
            voucher_no="DEL-1",
            date=date(2026, 10, 1),
            site="北町",
            work_type="圧接",
            party_kind="職人",
            total_price=1000,
        )

    def test_get_delete_is_not_allowed_and_keeps_voucher(self):
        response = self.client.get(reverse("workrecord_delete", args=[self.record.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(WorkRecord.objects.filter(pk=self.record.pk).exists())

    def test_post_delete_removes_voucher(self):
        response = self.client.post(
            reverse("workrecord_delete", args=[self.record.pk]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "伝票を削除しました。")
        self.assertFalse(WorkRecord.objects.filter(pk=self.record.pk).exists())

    def test_printed_voucher_is_not_deleted_and_explains_why(self):
        document = PrintedDocument.objects.create(
            kind="shokunin",
            party_name="職人太郎",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 31),
            total_amount=1000,
        )
        PrintedDocumentItem.objects.create(
            document=document,
            work_record=self.record,
            line_total=1000,
            snapshot={},
        )
        response = self.client.post(
            reverse("workrecord_delete", args=[self.record.pk]), follow=True
        )
        self.assertContains(response, "印刷済みのため削除できません。")
        self.assertTrue(WorkRecord.objects.filter(pk=self.record.pk).exists())

    def test_list_delete_controls_are_post_forms(self):
        contractor = GeneralContractor.objects.create(name="青葉建設")
        worker = Worker.objects.create(name="山田太郎", worker_type="職人")
        moto = WorkRecord.objects.create(
            voucher_no="DEL-M",
            date=date(2026, 10, 1),
            site="北町",
            work_type="圧接",
            party_kind="元請",
            general_contractor="青葉建設",
            billing_contractor="青葉建設",
            total_price=1000,
        )
        craftsman = WorkRecord.objects.create(
            voucher_no="DEL-S",
            date=date(2026, 10, 2),
            site="南校舎",
            work_type="溶接",
            party_kind="職人",
            helpers="山田太郎",
            total_price=800,
        )
        pages = (
            self.client.get(
                reverse("workrecord_list"),
                {
                    "kind": "moto",
                    "moto_company": contractor.pk,
                },
            ),
            self.client.get(
                reverse("workrecord_list"),
                {
                    "kind": "worker",
                    "worker_id": worker.pk,
                },
            ),
        )
        for page in pages:
            self.assertContains(page, 'method="post"')
            self.assertContains(page, "csrfmiddlewaretoken")
            self.assertContains(page, "この伝票をすべて削除しますか？")
        self.assertNotContains(pages[0], f'href="/delete/{moto.pk}/')
        self.assertNotContains(pages[1], f'href="/delete/{craftsman.pk}/')


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

    def test_worker_admin_company_is_text_not_ouen(self):
        Company.objects.create(name="応援は出さない")
        response = self.client.get(reverse("admin:workapp_worker_add"))
        html = response.content.decode()
        self.assertIn('id="id_affiliation"', html)
        self.assertNotIn('select name="affiliation"', html)
        self.assertNotIn("応援は出さない", html)
        self.assertContains(response, "専属のひとり親方")


class WorkerRateChoiceTests(TestCase):
    def setUp(self):
        self.size = WorkSize.objects.create(name="D19")
        WorkerDefaultRate.objects.create(work_size=self.size, unit_price=800)
        self.common_worker = Worker.objects.create(
            name="共通さん", use_common_rate=True
        )
        self.own_worker = Worker.objects.create(name="独自さん", use_common_rate=False)
        WorkerRate.objects.create(
            worker=self.common_worker, work_size=self.size, unit_price=999
        )
        WorkerRate.objects.create(
            worker=self.own_worker, work_size=self.size, unit_price=650
        )

    def test_common_rate_ignores_own_row(self):
        self.assertEqual(_worker_unit_price(self.common_worker, self.size), 800)

    def test_own_rate_ignores_common(self):
        self.assertEqual(_worker_unit_price(self.own_worker, self.size), 650)

    def test_get_unit_price_api_follows_flag(self):
        user = User.objects.create_user("rate-user", password="test-pass-123")
        self.client.force_login(user)
        common = self.client.get(
            reverse("get_unit_price"),
            {
                "type": "worker",
                "id": self.common_worker.pk,
                "size": self.size.pk,
            },
        )
        own = self.client.get(
            reverse("get_unit_price"),
            {
                "type": "worker",
                "id": self.own_worker.pk,
                "size": self.size.pk,
            },
        )
        self.assertEqual(common.json()["unit_price"], 800)
        self.assertEqual(own.json()["unit_price"], 650)


class PercentFloorTests(TestCase):
    def test_percent_result_drops_fraction(self):
        self.assertEqual(_amount_at_percent(851, 10), 85)
        self.assertEqual(_amount_at_percent(1001, 35), 350)
        self.assertEqual(_amount_at_percent(1001, 40), 400)
        self.assertNotEqual(_amount_at_percent(851, 10), 86)

    def test_tax_10_percent_floors(self):
        ctx = _pay_tax_context(851)
        self.assertEqual(ctx["pay_tax"], "85")
        self.assertEqual(ctx["pay_tax_included"], "936")

    def test_next_month_end_label(self):
        self.assertEqual(_next_month_end_label(date(2026, 9, 30)), "2026年10月末")
        self.assertEqual(_next_month_end_label("2026-12-31"), "2027年1月末")

    def test_shokunin_deduction_floors_then_subtracts(self):
        rows = [{"total_price": 1001}]
        totals = _with_temoto_deduction(
            rows,
            {
                "count": 1,
                "temoto_pool": 35,
                "temoto_each": 35,
                "mode": "count",
                "shares": [],
            },
        )
        self.assertEqual(totals["deduction"], 350)
        self.assertEqual(totals["total"], 651)
        self.assertEqual(rows[0]["total_price"], 651)

    def test_temoto_share_floors(self):
        amounts = _temoto_line_amounts(
            1001,
            {
                "count": 1,
                "split_pool": False,
                "temoto_pool": 35,
                "shares": [{"percent": 35}],
            },
        )
        self.assertEqual(amounts, [350])

    def test_helper_percents_follow_blank_or_registered_rate(self):
        blank = type("Person", (), {"name": "空欄", "temoto_percent": None})
        special = type("Person", (), {"name": "登録", "temoto_percent": 22})
        one = _temoto_allocation([blank])
        self.assertEqual(one["shokunin_deduction"], 35)
        self.assertEqual(one["shares"][0]["percent"], 35)
        two = _temoto_allocation([blank, blank])
        self.assertEqual(two["shokunin_deduction"], 40)
        self.assertEqual([share["percent"] for share in two["shares"]], [20, 20])
        three = _temoto_allocation([blank, blank, blank])
        self.assertEqual(three["shokunin_deduction"], 40)
        self.assertEqual([share["percent"] for share in three["shares"]], [13, 13, 13])
        self.assertEqual(_temoto_line_amounts(10000, three), [1300, 1300, 1300])
        mixed = _temoto_allocation([blank, special])
        self.assertEqual(mixed["shokunin_deduction"], 40)
        self.assertEqual([share["percent"] for share in mixed["shares"]], [20, 11])
        solo = _temoto_allocation([special])
        self.assertEqual(solo["shokunin_deduction"], 35)
        self.assertEqual(solo["shares"][0]["percent"], 22)
        rows = [{"total_price": 10000}]
        totals = _with_temoto_deduction(rows, solo)
        self.assertEqual(totals["deduction"], 3500)
        self.assertEqual(rows[0]["total_price"], 6500)
        self.assertEqual(_temoto_line_amounts(10000, solo), [2200])


@override_settings(SIGNUP_INVITE_CODE="invite-demo")
class SignupTests(TestCase):
    def test_login_page_links_to_signup(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, reverse("signup"))
        self.assertContains(response, "新規登録")

    def test_signup_page_asks_for_invite_code(self):
        response = self.client.get(reverse("signup"))
        self.assertContains(response, "招待コード")
        self.assertContains(response, "登録する")

    def test_wrong_invite_code_does_not_create_user(self):
        response = self.client.post(
            reverse("signup"),
            {
                "invite_code": "wrong",
                "username": "new-user",
                "password1": "Signup-pass-123",
                "password2": "Signup-pass-123",
            },
        )
        self.assertContains(response, "招待コードが違います。")
        self.assertFalse(User.objects.filter(username="new-user").exists())

    def test_missing_invite_code_setting_rejects_signup(self):
        with override_settings(SIGNUP_INVITE_CODE=""):
            response = self.client.post(
                reverse("signup"),
                {
                    "invite_code": "invite-demo",
                    "username": "new-user",
                    "password1": "Signup-pass-123",
                    "password2": "Signup-pass-123",
                },
            )
        self.assertContains(response, "招待コードが違います。")
        self.assertFalse(User.objects.filter(username="new-user").exists())

    def test_correct_invite_code_creates_user_who_can_log_in(self):
        response = self.client.post(
            reverse("signup"),
            {
                "invite_code": "invite-demo",
                "username": "new-user",
                "password1": "Signup-pass-123",
                "password2": "Signup-pass-123",
            },
        )
        self.assertRedirects(response, reverse("login") + "?signed_up=1")
        user = User.objects.get(username="new-user")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        logged_in = self.client.login(username="new-user", password="Signup-pass-123")
        self.assertTrue(logged_in)


class DemoSeedTests(TestCase):
    def test_seed_does_nothing_without_demo_seed_flag(self):
        with os_environ_without("DEMO_SEED"):
            call_command("seed_demo_data")
        self.assertFalse(
            WorkRecord.objects.filter(voucher_no__startswith="DEMO-").exists()
        )
        self.assertFalse(Worker.objects.filter(name="山田太郎").exists())

    def test_seed_creates_recent_vouchers_once(self):
        with os_environ_set(DEMO_SEED="1"):
            call_command("seed_demo_data")
            first_count = WorkRecord.objects.filter(
                voucher_no__startswith="DEMO-"
            ).count()
            voucher_numbers = set(
                WorkRecord.objects.filter(voucher_no__startswith="DEMO-").values_list(
                    "voucher_no", flat=True
                )
            )
            WorkRecord.objects.filter(voucher_no="DEMO-1001", party_kind="職人").update(
                total_price=1
            )
            call_command("seed_demo_data")
        self.assertGreaterEqual(len(voucher_numbers), 10)
        self.assertLessEqual(len(voucher_numbers), 20)
        self.assertEqual(
            WorkRecord.objects.filter(voucher_no__startswith="DEMO-").count(),
            first_count,
        )
        today = timezone.localdate()
        dates = set(
            WorkRecord.objects.filter(voucher_no__startswith="DEMO-").values_list(
                "date", flat=True
            )
        )
        self.assertTrue(dates)
        self.assertTrue(all(0 <= (today - day).days <= 6 for day in dates))
        helper_counts = set(
            WorkRecord.objects.filter(
                voucher_no__startswith="DEMO-", party_kind="職人"
            ).values_list("helper_count", flat=True)
        )
        self.assertTrue({0, 1, 2, 3}.issubset(helper_counts))
        self.assertTrue(
            WorkRecord.objects.filter(
                voucher_no__startswith="DEMO-", party_kind="応援"
            ).exists()
        )
        closing_days = set(
            GeneralContractor.objects.filter(
                name__in=["青葉建設", "みどり工務店", "東雲建設"]
            ).values_list("closing_day", flat=True)
        )
        self.assertGreaterEqual(len(closing_days), 2)
        self.assertGreaterEqual(
            Site.objects.filter(name__startswith="北町マンション").count(),
            3,
        )
        self.assertFalse(Worker.objects.filter(name__contains="花子").exists())
        replaced = WorkRecord.objects.get(voucher_no="DEMO-1001", party_kind="職人")
        self.assertNotEqual(replaced.total_price, 1)
        for voucher_no in voucher_numbers:
            kinds = set(
                WorkRecord.objects.filter(voucher_no=voucher_no).values_list(
                    "party_kind", flat=True
                )
            )
            self.assertFalse({"職人", "応援"}.issubset(kinds))
            if "応援" in kinds:
                self.assertNotIn("手元", kinds)
                self.assertFalse(
                    WorkRecord.objects.filter(voucher_no=voucher_no)
                    .exclude(temoto1="")
                    .exists()
                )


class MasterLinkTests(LoggedInTestCase):
    def test_duplicate_names_are_not_linked(self):
        from .models import unique_named

        Worker.objects.create(name="同名", employee_number="A-1")
        Worker.objects.create(name="同名", employee_number="A-2")
        self.assertIsNone(unique_named(Worker, "同名"))
        only = Worker.objects.create(name="一名", employee_number="B-1")
        self.assertEqual(unique_named(Worker, "一名"), only)

    def test_inactive_worker_is_hidden_on_the_new_form(self):
        Worker.objects.create(name="退職者", employee_number="RET-1", is_active=False)
        response = self.client.get(reverse("workrecord_create"))
        self.assertNotContains(response, "退職者")

    def test_master_api_returns_codes_without_prices(self):
        Worker.objects.create(
            name="山田太郎",
            employee_number="E-100",
            name_kana="ヤマダタロウ",
            temoto_percent=22,
        )
        Site.objects.create(
            name="北町",
            site_code="S-100",
            name_kana="キタマチ",
            general_contractor=GeneralContractor.objects.create(name="青葉建設"),
        )
        sites = self.client.get(reverse("api_sites"))
        workers = self.client.get(reverse("api_workers"))
        self.assertEqual(sites.status_code, 200)
        self.assertEqual(sites.json()["sites"][0]["code"], "S-100")
        self.assertNotIn("unit_price", sites.content.decode())
        body = workers.json()["workers"][0]
        self.assertEqual(body["code"], "E-100")
        self.assertEqual(body["name_kana"], "ヤマダタロウ")
        self.assertNotIn("temoto_percent", body)
        self.assertNotIn("unit_price", workers.content.decode())

    def test_master_api_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("api_workers"))
        self.assertEqual(response.status_code, 302)


class EditByMasterIdTests(LoggedInTestCase):
    def _voucher(self, site, worker, gc):
        return WorkRecord.objects.create(
            voucher_no="EDIT-1",
            date=date(2026, 10, 1),
            site=site.name,
            site_master=site,
            party_kind="職人",
            craftsman=worker.name,
            craftsman_worker=worker,
            general_contractor=gc.name,
            contractor_master=gc,
            billing_contractor=gc.name,
            billing_master=gc,
            work_type="圧接",
            total_price=100,
        )

    def _edit_html(self, record):
        response = self.client.get(
            reverse("workrecord_edit", args=[record.pk]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _select_html(self, html, name):
        import re

        match = re.search(rf'<select name="{name}".*?</select>', html, re.S)
        self.assertIsNotNone(match)
        return match.group(0)

    def _selected_value(self, html, name):
        import re

        match = re.search(r'value="(\d+)" selected', self._select_html(html, name))
        self.assertIsNotNone(match)
        return match.group(1)

    def _overwrite_from_edit(self, record, extra=None):
        html = self._edit_html(record)
        data = {
            "voucher_no": record.voucher_no,
            "date": record.date.isoformat(),
            "site": self._selected_value(html, "site"),
            "worker": self._selected_value(html, "worker"),
            "work_type_1": record.work_type or "圧接",
            "work_amount_1": "1",
            "price_mode_1": "master",
        }
        if extra:
            data.update(extra)
        opened = self.client.post(reverse("workrecord_create"), data, follow=True)
        self.assertEqual(opened.status_code, 200)
        self.assertNotContains(opened, "正しく選択してください")
        self.assertContains(opened, "上書きする")
        saved = self.client.post(
            reverse("workrecord_review"), {"action": "overwrite"}, follow=True
        )
        self.assertEqual(saved.status_code, 200)
        return html

    def test_renamed_worker_stays_selected_on_edit(self):
        gc = GeneralContractor.objects.create(name="元請改名")
        site = Site.objects.create(name="現場改名", general_contractor=gc)
        worker = Worker.objects.create(name="職人太郎")
        record = self._voucher(site, worker, gc)
        worker.name = "職人次郎"
        worker.save(update_fields=["name"])
        html = self._overwrite_from_edit(record)
        worker_html = self._select_html(html, "worker")
        self.assertIn(f'value="{worker.pk}" selected', worker_html)
        self.assertIn("職人次郎", worker_html)
        self.assertNotIn("職人太郎", worker_html)
        saved = WorkRecord.objects.get(voucher_no="EDIT-1", party_kind="職人")
        self.assertEqual(saved.craftsman_worker_id, worker.pk)
        self.assertEqual(saved.craftsman, "職人次郎")

    def test_duplicate_worker_name_keeps_the_saved_person(self):
        gc = GeneralContractor.objects.create(name="元請同名")
        site = Site.objects.create(name="現場同名", general_contractor=gc)
        size = WorkSize.objects.create(name="D19同名")
        first = Worker.objects.create(name="同名", use_common_rate=False)
        second = Worker.objects.create(name="同名", use_common_rate=False)
        WorkerRate.objects.create(worker=first, work_size=size, unit_price=500)
        WorkerRate.objects.create(worker=second, work_size=size, unit_price=900)
        record = self._voucher(site, second, gc)
        record.work_size = size.name
        record.work_amount = 1
        record.save(update_fields=["work_size", "work_amount"])
        html = self._overwrite_from_edit(
            record, {"work_size_1": str(size.pk), "work_amount_1": "1"}
        )
        worker_html = self._select_html(html, "worker")
        self.assertIn(f'value="{second.pk}" selected', worker_html)
        self.assertNotIn(f'value="{first.pk}" selected', worker_html)
        saved = WorkRecord.objects.get(voucher_no="EDIT-1", party_kind="職人")
        self.assertEqual(saved.craftsman_worker_id, second.pk)
        self.assertEqual(saved.unit_price, 900)

    def test_inactive_site_remains_a_choice_and_can_be_saved(self):
        gc = GeneralContractor.objects.create(name="元請無効")
        site = Site.objects.create(
            name="現場無効", general_contractor=gc, is_active=False
        )
        worker = Worker.objects.create(name="職人無効", is_active=False)
        record = self._voucher(site, worker, gc)
        html = self._overwrite_from_edit(record)
        site_html = self._select_html(html, "site")
        worker_html = self._select_html(html, "worker")
        self.assertIn(f'value="{site.pk}" selected', site_html)
        self.assertIn("現場無効", site_html)
        self.assertIn(f'value="{worker.pk}" selected', worker_html)
        saved = WorkRecord.objects.get(voucher_no="EDIT-1", party_kind="職人")
        self.assertEqual(saved.site_master_id, site.pk)
        self.assertEqual(saved.craftsman_worker_id, worker.pk)

    def test_billing_and_ouen_rates_use_saved_ids(self):
        from .models import CompanyRate, GeneralContractorRate

        size = WorkSize.objects.create(name="D19編集")
        named = GeneralContractor.objects.create(name="表示名の元請")
        billed = GeneralContractor.objects.create(name="請求先の元請")
        GeneralContractorRate.objects.create(
            general_contractor=named, work_size=size, unit_price=111
        )
        GeneralContractorRate.objects.create(
            general_contractor=billed, work_size=size, unit_price=3210
        )
        named_company = Company.objects.create(name="表示名の応援")
        billed_company = Company.objects.create(name="単価のある応援")
        CompanyRate.objects.create(company=named_company, work_size=size, unit_price=50)
        CompanyRate.objects.create(
            company=billed_company, work_size=size, unit_price=900
        )
        payload = {
            "billing_contractor": named.name,
            "billing_contractor_id": billed.pk,
            "general_contractor": named.name,
            "company": named_company.name,
            "company_id": billed_company.pk,
            "work_types": ["圧接"],
            "work_sizes": [str(size.pk)],
            "work_amounts": [2],
            "price_modes": ["master"],
            "manual_billings": [None],
            "manual_ouens": [None],
            "rate_billings": [None],
            "rate_ouens": [None],
        }
        moto_rows, _totals, _alloc = _moto_work_rows(payload)
        ouen_rows, _ouen_totals, _ouen_alloc = _ouen_work_rows(payload)
        self.assertEqual(moto_rows[0]["unit_price"], 3210)
        self.assertEqual(ouen_rows[0]["unit_price"], 900)


class SecretKeySettingsTests(SimpleTestCase):
    def test_settings_do_not_keep_a_default_secret(self):
        text = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("django-insecure-e8", text)
        self.assertIn("ImproperlyConfigured", text)
        self.assertIn('os.environ.get("DJANGO_DEBUG") == "1"', text)


class _Environ:
    def __init__(self, updates, remove):
        self.updates = updates
        self.remove = remove
        self.previous = {}

    def __enter__(self):
        for key in self.remove:
            self.previous[key] = os.environ.pop(key, None)
        os.environ.update(self.updates)
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in self.updates:
            os.environ.pop(key, None)
        for key, value in self.previous.items():
            if value is not None:
                os.environ[key] = value


def os_environ_set(**updates):
    return _Environ(updates, remove=())


def os_environ_without(*keys):
    return _Environ({}, remove=keys)
