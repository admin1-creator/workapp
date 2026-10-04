import hmac

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .constants import HELPER_FIELDS
from .models import (
    WorkRecord,
    Site,
    GeneralContractor,
    Worker,
    Company,
    unique_named,
)


class ReadingSelect(forms.Select):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        instance = getattr(value, "instance", None)
        reading = getattr(instance, "name_kana", "") if instance is not None else ""
        if reading:
            option["attrs"]["data-reading"] = reading
        return option


class SiteByContractorSelect(ReadingSelect):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        site = getattr(value, "instance", None)
        if site is not None and getattr(site, "general_contractor_id", None):
            option["attrs"]["data-contractor"] = str(site.general_contractor_id)
            option["attrs"]["data-contractor-name"] = site.general_contractor.name
        return option


class WorkRecordBasicForm(forms.ModelForm):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    site = forms.ModelChoiceField(
        queryset=Site.objects.filter(is_active=True)
        .select_related("general_contractor")
        .order_by("name"),
        label="現場名",
        widget=SiteByContractorSelect,
        empty_label="選択してください",
    )

    general_contractor_display = forms.CharField(
        label="元請",
        required=False,
        disabled=True,
    )
    primary_company = forms.ModelChoiceField(
        queryset=GeneralContractor.objects.order_by("name"),
        required=False,
        label="1次企業",
        empty_label="なし（現場の元請に請求）",
    )
    billing_contractor_display = forms.CharField(
        label="請求先",
        required=False,
        disabled=True,
    )

    worker = forms.ModelChoiceField(
        queryset=Worker.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="職人",
        empty_label="なし",
        widget=ReadingSelect,
    )

    temoto1 = forms.ModelChoiceField(
        queryset=Worker.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="手元1",
        empty_label="なし",
        widget=ReadingSelect,
    )
    temoto2 = forms.ModelChoiceField(
        queryset=Worker.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="手元2",
        empty_label="なし",
        widget=ReadingSelect,
    )
    temoto3 = forms.ModelChoiceField(
        queryset=Worker.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="手元3",
        empty_label="なし",
        widget=ReadingSelect,
    )

    company = forms.ModelChoiceField(
        queryset=Company.objects.all(), required=False, label="応援企業"
    )

    work_type = forms.CharField(
        required=False,
        label="作業内容",
        widget=forms.TextInput(
            attrs={
                "class": "ime-hiragana",
                "lang": "ja",
                "autocomplete": "off",
            }
        ),
    )
    work_size = forms.CharField(
        required=False,
        label="寸法",
    )
    work_amount = forms.IntegerField(
        required=False,
        label="作業量",
    )
    remark = forms.CharField(
        required=False,
        label="備考",
    )
    unit_price = forms.IntegerField(
        required=False,
        label="単価",
    )
    total_price = forms.IntegerField(
        required=False,
        label="合計金額",
    )

    field_order = [
        "voucher_no",
        "date",
        "site",
        "general_contractor_display",
        "primary_company",
        "billing_contractor_display",
        "worker",
        "temoto1",
        "temoto2",
        "temoto3",
        "company",
        "work_type",
        "work_size",
        "work_amount",
        "remark",
        "unit_price",
        "total_price",
    ]

    class Meta:
        model = WorkRecord
        fields = [
            "voucher_no",
            "date",
            "site",
            "company",
            "work_type",
            "work_size",
            "work_amount",
            "remark",
            "unit_price",
            "total_price",
        ]
        labels = {
            "voucher_no": "伝票番号",
            "date": "日付",
            "site": "現場名",
            "company": "応援企業",
            "work_type": "作業内容",
            "work_size": "寸法",
            "work_amount": "作業量",
            "remark": "備考",
            "unit_price": "単価",
            "total_price": "合計金額",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " field-input").strip()
        self._include_saved_masters()
        for key in (
            "work_type",
            "work_size",
            "work_amount",
            "remark",
            "unit_price",
            "total_price",
        ):
            self.fields.pop(key, None)
        self._sync_contractor_displays()

    def _include_saved_masters(self):
        pairs = (
            ("site", Site),
            ("worker", Worker),
            ("temoto1", Worker),
            ("temoto2", Worker),
            ("temoto3", Worker),
        )
        for field_name, model in pairs:
            raw = self.data.get(field_name) if self.is_bound else None
            if raw in (None, ""):
                raw = self.initial.get(field_name)
            if raw in (None, ""):
                continue
            obj = model.objects.filter(pk=raw).first()
            self._keep_inactive_choice(field_name, obj)

    def _keep_inactive_choice(self, field_name, obj):
        if obj is None or not hasattr(obj, "is_active") or obj.is_active:
            return
        field = self.fields[field_name]
        field.queryset = field.queryset | obj.__class__.objects.filter(pk=obj.pk)

    def _lookup_site(self):
        if self.is_bound:
            site_id = self.data.get("site")
            if site_id:
                return (
                    Site.objects.select_related("general_contractor")
                    .filter(pk=site_id)
                    .first()
                )
            return None
        value = self.initial.get("site") or self.fields["site"].initial
        if isinstance(value, Site):
            return (
                Site.objects.select_related("general_contractor")
                .filter(pk=value.pk)
                .first()
            )
        if isinstance(value, int) or (isinstance(value, str) and str(value).isdigit()):
            return (
                Site.objects.select_related("general_contractor")
                .filter(pk=value)
                .first()
            )
        if value:
            site = unique_named(Site, value)
            if site is None:
                return None
            return (
                Site.objects.select_related("general_contractor")
                .filter(pk=site.pk)
                .first()
            )
        return None

    def _lookup_primary(self):
        if self.is_bound:
            pk = self.data.get("primary_company")
            if pk:
                return GeneralContractor.objects.filter(pk=pk).first()
            return None
        value = (
            self.initial.get("primary_company")
            or self.fields["primary_company"].initial
        )
        if hasattr(value, "pk"):
            return value
        if value:
            return GeneralContractor.objects.filter(pk=value).first()
        return None

    def _sync_contractor_displays(self):
        site = self._lookup_site()
        gc_name = (
            site.general_contractor.name if site and site.general_contractor_id else ""
        )
        primary = self._lookup_primary()
        billing = (primary.name if primary else "") or gc_name
        self.fields["general_contractor_display"].initial = gc_name
        self.fields["billing_contractor_display"].initial = billing
        self.initial["general_contractor_display"] = gc_name
        self.initial["billing_contractor_display"] = billing

    def clean(self):
        cleaned = super().clean()
        selected = []
        for key in HELPER_FIELDS:
            person = cleaned.get(key)
            if person:
                selected.append(person.pk)
        if len(selected) != len(set(selected)):
            raise forms.ValidationError("手元は同じ人を重複して選べません。")
        worker = cleaned.get("worker")
        if selected and not worker:
            raise forms.ValidationError("手元がいるときは、職人も選んでください。")
        if worker and worker.pk in selected:
            raise forms.ValidationError("同じ人を職人と手元の両方には選べません。")
        site = cleaned.get("site")
        primary = cleaned.get("primary_company")
        gc_name = (
            site.general_contractor.name
            if site and getattr(site, "general_contractor_id", None)
            else ""
        )
        primary_name = primary.name if primary else ""
        cleaned["general_contractor_name"] = gc_name
        cleaned["primary_company_name"] = primary_name
        cleaned["billing_contractor_name"] = primary_name or gc_name
        return cleaned


WorkRecordForm = WorkRecordBasicForm


class SignupForm(UserCreationForm):
    invite_code = forms.CharField(label="招待コード", max_length=100)

    def clean_invite_code(self):
        entered = self.cleaned_data.get("invite_code") or ""
        expected = settings.SIGNUP_INVITE_CODE or ""
        if not expected or not hmac.compare_digest(entered, expected):
            raise ValidationError("招待コードが違います。")
        return entered
