from calendar import monthrange
from datetime import date, timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def _month_day(year, month, day):
    return date(year, month, min(int(day), monthrange(year, month)[1]))


def _shift_month(year, month, delta):
    month = month - 1 + delta
    year += month // 12
    month = month % 12 + 1
    return year, month


def closing_period_for(today, closing_day):
    if today is None:
        today = timezone.localdate()
    try:
        closing_day = int(closing_day)
    except (TypeError, ValueError):
        return None
    if not 1 <= closing_day <= 31:
        return None
    this_end = _month_day(today.year, today.month, closing_day)
    if today <= this_end:
        prev_year, prev_month = _shift_month(today.year, today.month, -1)
        start = _month_day(prev_year, prev_month, closing_day) + timedelta(days=1)
        return start, this_end
    next_year, next_month = _shift_month(today.year, today.month, 1)
    start = this_end + timedelta(days=1)
    return start, _month_day(next_year, next_month, closing_day)


def previous_period_for(current_period, closing_day):
    if not current_period:
        return None
    start, _end = current_period
    return closing_period_for(start - timedelta(days=1), closing_day)


# -----------------------------
#  ゼネコン
# -----------------------------
class GeneralContractor(models.Model):
    name = models.CharField("元請名", max_length=100)
    closing_day = models.PositiveSmallIntegerField(
        "締め日",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="毎月の締め日（1〜31）。未設定なら期間は手入力します。",
    )

    class Meta:
        verbose_name = "元請"
        verbose_name_plural = "元請一覧"

    def __str__(self):
        return self.name

    def current_closing_period(self, today=None):
        return closing_period_for(today, self.closing_day)

    def previous_closing_period(self, today=None):
        return previous_period_for(self.current_closing_period(today), self.closing_day)


# -----------------------------
#  応援企業
# -----------------------------
class Company(models.Model):
    name = models.CharField("応援企業名", max_length=50)

    class Meta:
        verbose_name = "応援企業"
        verbose_name_plural = "応援企業一覧"
    def __str__(self):
        return self.name

# -----------------------------
#  作業員（職人・手元）
# -----------------------------
class Worker(models.Model):
    WORKER_TYPES = (
        ("職人", "職人"),
        ("手元", "手元"),
    )

    name = models.CharField("作業員名", max_length=100)
    worker_type = models.CharField(
        "種別",
        max_length=10,
        choices=WORKER_TYPES,
        default="職人",
        blank=True,
    )
    temoto_percent = models.IntegerField("手元％", null=True, blank=True)
    use_common_rate = models.BooleanField(
        "共通単価を使用する",
        default=True,
        help_text="オフにすると、この作業員だけの単価を下の表で入力します。",
    )
    company = models.CharField(
        "所属企業",
        max_length=100,
        blank=True,
        default="",
        help_text="基本は空欄（自社）です。専属のひとり親方のときだけ、本人名や屋号を入力します。応援の会社名は入れません。",
    )

    class Meta:
        verbose_name = "作業員"
        verbose_name_plural = "作業員一覧"

    def __str__(self):
        return self.name

# -----------------------------
#  現場
# -----------------------------
class Site(models.Model):
    name = models.CharField("現場名", max_length=100)
    general_contractor = models.ForeignKey(
        GeneralContractor,
        verbose_name="元請",
        on_delete=models.CASCADE
    )

    class Meta:
        verbose_name = "現場"
        verbose_name_plural = "現場一覧"

    def __str__(self):
        return f"{self.name}（{self.general_contractor.name}）"


# -----------------------------
#  作業種類（D19圧、D19切、D22圧など）
#  プルダウン＋自由入力に対応するためのマスタ
# -----------------------------
class WorkType(models.Model):
    name = models.CharField("作業種類名", max_length=100)

    class Meta:
        verbose_name = "作業種類"
        verbose_name_plural = "作業種類一覧"
    def __str__(self):
        return self.name



class WorkSize(models.Model):
    name = models.CharField("寸法", max_length=50)

    class Meta:
        verbose_name = "寸法"
        verbose_name_plural = "寸法一覧"

    def __str__(self):
        return self.name    
# -----------------------------
#  単価テーブル（ゼネコン）
# -----------------------------
class GeneralContractorRate(models.Model):
    general_contractor = models.ForeignKey(
        GeneralContractor,
        verbose_name="元請",
        on_delete=models.CASCADE
    )
    work_size = models.ForeignKey(
        WorkSize,
        verbose_name="寸法",
        on_delete=models.CASCADE
    )
    unit_price = models.IntegerField("単価")

    class Meta:
        verbose_name = "元請単価"
        verbose_name_plural = "元請単価一覧"

    def __str__(self):
        return f"{self.general_contractor.name} - {self.work_size.name} : {self.unit_price}円"

# -----------------------------
#  単価テーブル（応援）
# -----------------------------
class CompanyRate(models.Model):
    company = models.ForeignKey(Company, verbose_name="応援企業", on_delete=models.CASCADE)
    work_size = models.ForeignKey(WorkSize, verbose_name="寸法", on_delete=models.CASCADE)
    unit_price = models.IntegerField("単価")

    class Meta:
        verbose_name = "応援企業単価"
        verbose_name_plural = "応援企業単価一覧"

    def __str__(self):
        return f"{self.company.name} - {self.work_size.name}：{self.unit_price}"


# -----------------------------
#  単価テーブル（職人）
# -----------------------------
class WorkerRate(models.Model):
    worker = models.ForeignKey(Worker, verbose_name="作業員", on_delete=models.CASCADE)
    work_size = models.ForeignKey(WorkSize, verbose_name="寸法", on_delete=models.CASCADE)
    unit_price = models.IntegerField("単価")

    class Meta:
        verbose_name = "作業員の独自単価"
        verbose_name_plural = "作業員の独自単価"
        unique_together = ("worker", "work_size")

    def __str__(self):
        return f"{self.worker.name} - {self.work_size.name}：{self.unit_price}"

class WorkerDefaultRate(models.Model):
    work_size = models.ForeignKey(WorkSize, on_delete=models.CASCADE)
    unit_price = models.IntegerField()

    class Meta:
        verbose_name = "作業員共通単価"
        verbose_name_plural = "作業員共通単価一覧"

    def __str__(self):
        return f"{self.work_size.name} : {self.unit_price}円"


# -----------------------------
#  作業記録（1画面入力の中心）
# -----------------------------
class WorkRecord(models.Model):
    voucher_no = models.CharField(max_length=50, default="")
    date = models.DateField()
    site = models.CharField(max_length=255)

    work_type = models.CharField(max_length=255)
    work_size = models.CharField(max_length=255, null=True, blank=True, default="")
    dimension = models.CharField("寸法", max_length=255, null=True, blank=True)
    work_amount = models.IntegerField(null=True, blank=True)
    remark = models.CharField("備考", max_length=255, blank=True, default="")
    size_mark = models.CharField("印", max_length=20, blank=True, default="")
    price_mode = models.CharField(
        "単価区分",
        max_length=10,
        default="master",
        blank=True,
        choices=(("master", "マスタ単価"), ("manual", "手入力")),
    )
    manual_billing = models.IntegerField("手入力・請求額", null=True, blank=True)
    manual_pay = models.IntegerField("手入力・支払額", null=True, blank=True)
    manual_ouen = models.IntegerField("手入力・応援額", null=True, blank=True)

    # 現場に紐づく元請
    general_contractor = models.CharField("元請", max_length=255, default="")
    # 1次企業（上位企業）。未選択なら請求先は現場の元請
    primary_company = models.CharField("1次企業", max_length=255, blank=True, default="")
    # 請求先（1次企業があれば1次企業、なければ現場の元請）
    billing_contractor = models.CharField("請求先", max_length=255, default="")

    # 作業員
    worker = models.CharField(max_length=50, default="")

    # 応援企業（追加）
    company = models.CharField(max_length=255, blank=True, default="")

    shokunin_deduction_percent = models.IntegerField(null=True, blank=True)
    temoto_percent = models.IntegerField(null=True, blank=True)
    helper_count = models.IntegerField(default=0)
    helpers = models.CharField(max_length=255, blank=True, default="")
    temoto1 = models.CharField("手元1", max_length=100, blank=True, default="")
    temoto2 = models.CharField("手元2", max_length=100, blank=True, default="")
    temoto3 = models.CharField("手元3", max_length=100, blank=True, default="")

    unit_price = models.IntegerField(null=True, blank=True)
    total_price = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PrintedDocument(models.Model):
    KIND_CHOICES = [
        ("moto", "請求書（元請）"),
        ("worker", "支払明細（作業員）"),
        ("shokunin", "支払明細（職人）"),
        ("temoto", "支払明細（手元）"),
        ("ouen", "支払明細（応援）"),
    ]
    STATUS_CHOICES = [
        ("printed", "印刷済み"),
        ("cancelled", "取消"),
    ]

    kind = models.CharField("帳票種別", max_length=20, choices=KIND_CHOICES)
    party_name = models.CharField("相手名", max_length=255)
    party_contractor = models.ForeignKey(
        GeneralContractor, verbose_name="元請",
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    party_worker = models.ForeignKey(
        Worker, verbose_name="作業員",
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    party_company = models.ForeignKey(
        Company, verbose_name="応援企業",
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    period_start = models.DateField("開始日")
    period_end = models.DateField("終了日")
    total_amount = models.IntegerField("合計（固定）", default=0)
    status = models.CharField("状態", max_length=20, choices=STATUS_CHOICES, default="printed")
    printed_at = models.DateTimeField("印刷日時", auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "印刷書類"
        verbose_name_plural = "印刷書類一覧"

    def __str__(self):
        return f"{self.get_kind_display()} {self.party_name} {self.period_start}〜{self.period_end}"


class KagamiSheet(models.Model):
    scope_key = models.CharField("対象キー", max_length=255, unique=True)
    lines = models.JSONField("支払書の行", default=list, blank=True)
    total_amount = models.IntegerField("支払書の合計", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "支払書"
        verbose_name_plural = "支払書"

    def __str__(self):
        return self.scope_key


class PrintedDocumentItem(models.Model):
    document = models.ForeignKey(
        PrintedDocument, verbose_name="印刷書類",
        on_delete=models.CASCADE, related_name="items",
    )
    work_record = models.ForeignKey(
        WorkRecord, verbose_name="作業記録",
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    line_total = models.IntegerField("行金額（固定）", null=True, blank=True)
    snapshot = models.JSONField("印刷時点の内容", default=dict, blank=True)

    class Meta:
        verbose_name = "印刷書類明細"
        verbose_name_plural = "印刷書類明細"

    def __str__(self):
        return f"{self.document_id} / {self.work_record_id}"


class UnitMaster(models.Model):
    UNIT_CHOICES = [
        ('moto', '元請'),
        ('shokunin', '職人'),
        ('temoto', '手元'),
        ('ouen', '応援'),
    ]

    unit_type = models.CharField(max_length=50, choices=UNIT_CHOICES)
    unit_price = models.IntegerField(null=True, blank=True)  # 手元は使わない
    temoto_percent = models.IntegerField(null=True, blank=True)  # 手元のみ使用

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_unit_type_display()}"



# -----------------------------
#  請求書（ゼネコン向け）
# -----------------------------
class Invoice(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    period_start = models.DateField()
    period_end = models.DateField()
    due_date = models.DateField()
    total_amount = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.site.name} 請求書（{self.period_start}〜{self.period_end}）"


# -----------------------------
#  支払明細（作業員・手元・応援企業）
# -----------------------------
class Payment(models.Model):
    worker = models.ForeignKey(Worker, on_delete=models.CASCADE, null=True, blank=True)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    total_amount = models.IntegerField(default=0)

    def __str__(self):
        if self.worker:
            return f"{self.worker.name} 支払明細（{self.period_start}〜{self.period_end}）"
        else:
            return f"{self.company.name} 支払明細（{self.period_start}〜{self.period_end}）"

