from django import forms
from .models import WorkRecord, Site, GeneralContractor, Worker, Company, WorkSize


class SiteByContractorSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        site = getattr(value, "instance", None)
        if site is not None and getattr(site, "general_contractor_id", None):
            option["attrs"]["data-contractor"] = str(site.general_contractor_id)
            option["attrs"]["data-contractor-name"] = site.general_contractor.name
        return option


class WorkRecordBasicForm(forms.ModelForm):

    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    site = forms.ModelChoiceField(
        queryset=Site.objects.select_related("general_contractor").order_by("name"),
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
        queryset=Worker.objects.order_by("name"),
        required=False,
        label="職人",
        empty_label="なし",
    )

    temoto1 = forms.ModelChoiceField(
        queryset=Worker.objects.order_by("name"),
        required=False,
        label="手元1",
        empty_label="なし",
    )
    temoto2 = forms.ModelChoiceField(
        queryset=Worker.objects.order_by("name"),
        required=False,
        label="手元2",
        empty_label="なし",
    )
    temoto3 = forms.ModelChoiceField(
        queryset=Worker.objects.order_by("name"),
        required=False,
        label="手元3",
        empty_label="なし",
    )

    company = forms.ModelChoiceField(
        queryset=Company.objects.all(),
        required=False,
        label="応援企業"
    )

    work_type = forms.CharField(
        required=False,
        label="作業内容",
        widget=forms.TextInput(attrs={
            "class": "ime-hiragana",
            "lang": "ja",
            "autocomplete": "off",
        }),
    )
    work_size = forms.CharField(
        required=False,
        label="寸法",
    )
    dimension = forms.CharField(
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
        'voucher_no',
        'date',
        'site',
        'general_contractor_display',
        'primary_company',
        'billing_contractor_display',
        'worker',
        'temoto1',
        'temoto2',
        'temoto3',
        'company',
        'work_type',
        'work_size',
        'dimension',
        'work_amount',
        'remark',
        'unit_price',
        'total_price',
    ]

    class Meta:
        model = WorkRecord
        fields = [
            'voucher_no',
            'date',
            'site',
            'worker',
            'company',
            'work_type',
            'work_size',
            'dimension',
            'work_amount',
            'remark',
            'unit_price',
            'total_price',
        ]
        labels = {
            'voucher_no': '伝票番号',
            'date': '日付',
            'site': '現場名',
            'worker': '職人',
            'company': '応援企業',
            'work_type': '作業内容',
            'work_size': '寸法',
            'dimension': '寸法',
            'work_amount': '作業量',
            'remark': '備考',
            'unit_price': '単価',
            'total_price': '合計金額',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " field-input").strip()
        instance = kwargs.get('instance')
        if not instance or not instance.pk:
            for key in ("work_type", "work_size", "dimension", "work_amount", "remark", "unit_price", "total_price"):
                self.fields.pop(key, None)
            self._sync_contractor_displays()
            return
        for key in ("work_type", "work_size", "dimension", "work_amount", "remark", "unit_price", "total_price"):
            self.fields[key].required = False
        self.fields["work_size"].widget = forms.HiddenInput()
        self._bind_named_choices(instance)
        size_name = (instance.dimension or instance.work_size or "").strip()
        size_names = list(WorkSize.objects.order_by("name").values_list("name", flat=True))
        if size_name and size_name not in size_names:
            size_names = [size_name] + size_names
        self.fields["dimension"].widget = forms.Select(
            choices=[("", "選択してください")] + [(name, name) for name in size_names]
        )
        self.fields["dimension"].initial = size_name
        self.initial["dimension"] = size_name
        dim_css = self.fields["dimension"].widget.attrs.get("class", "")
        self.fields["dimension"].widget.attrs["class"] = (dim_css + " field-input").strip()
        for key in ("temoto1", "temoto2", "temoto3"):
            name = getattr(instance, key, "") or ""
            if name:
                helper = Worker.objects.filter(name=name).first()
                if helper:
                    self.fields[key].initial = helper
        role = (instance.worker or "").strip()
        if role in ("手元", "temoto") and not getattr(instance, "temoto1", "") and instance.helpers:
            names = [n.strip() for n in instance.helpers.replace("、", ",").split(",") if n.strip()]
            for i, name in enumerate(names[:3], start=1):
                helper = Worker.objects.filter(name=name).first()
                if helper:
                    self.fields[f"temoto{i}"].initial = helper
        self._sync_contractor_displays()

    def _bind_named_choices(self, instance):
        site = (
            Site.objects.select_related("general_contractor").filter(name=instance.site).first()
            if instance.site else None
        )
        if site:
            self.initial["site"] = site.pk
            self.fields["site"].initial = site
        primary_name = getattr(instance, "primary_company", "") or ""
        if primary_name:
            primary = GeneralContractor.objects.filter(name=primary_name).first()
            if primary:
                self.initial["primary_company"] = primary.pk
                self.fields["primary_company"].initial = primary
        company = Company.objects.filter(name=instance.company).first() if instance.company else None
        if company:
            self.initial["company"] = company.pk
            self.fields["company"].initial = company
        self.fields["worker"].required = False
        self.fields["worker"].empty_label = "なし"
        craftsman_name = ""
        role = (instance.worker or "").strip()
        if role in ("職人", "shokunin"):
            craftsman_name = (instance.helpers or "").split("、")[0].strip()
        elif role not in ("元請", "moto", "手元", "temoto", "応援", "ouen"):
            craftsman_name = role
        if craftsman_name:
            craftsman = Worker.objects.filter(name=craftsman_name).first()
            if craftsman:
                self.initial["worker"] = craftsman.pk
                self.fields["worker"].initial = craftsman

    def _lookup_site(self):
        if self.is_bound:
            site_id = self.data.get("site")
            if site_id:
                return Site.objects.select_related("general_contractor").filter(pk=site_id).first()
            return None
        value = self.initial.get("site") or self.fields["site"].initial
        if isinstance(value, Site):
            return Site.objects.select_related("general_contractor").filter(pk=value.pk).first()
        if isinstance(value, int) or (isinstance(value, str) and str(value).isdigit()):
            return Site.objects.select_related("general_contractor").filter(pk=value).first()
        if value:
            return Site.objects.select_related("general_contractor").filter(name=value).first()
        return None

    def _lookup_primary(self):
        if self.is_bound:
            pk = self.data.get("primary_company")
            if pk:
                return GeneralContractor.objects.filter(pk=pk).first()
            return None
        value = self.initial.get("primary_company") or self.fields["primary_company"].initial
        if hasattr(value, "pk"):
            return value
        if value:
            return GeneralContractor.objects.filter(pk=value).first()
        return None

    def _sync_contractor_displays(self):
        site = self._lookup_site()
        gc_name = site.general_contractor.name if site and site.general_contractor_id else ""
        if not gc_name and getattr(self.instance, "pk", None):
            gc_name = self.instance.general_contractor or ""
        primary = self._lookup_primary()
        billing = (primary.name if primary else "") or gc_name
        if not billing and getattr(self.instance, "pk", None):
            billing = self.instance.billing_contractor or gc_name
        self.fields["general_contractor_display"].initial = gc_name
        self.fields["billing_contractor_display"].initial = billing
        self.initial["general_contractor_display"] = gc_name
        self.initial["billing_contractor_display"] = billing

    def clean(self):
        cleaned = super().clean()
        selected = []
        for key in ("temoto1", "temoto2", "temoto3"):
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
        if "dimension" in self.fields:
            size = (cleaned.get("dimension") or cleaned.get("work_size") or "").strip()
            cleaned["dimension"] = size or None
            cleaned["work_size"] = size or None
        site = cleaned.get("site")
        primary = cleaned.get("primary_company")
        gc_name = site.general_contractor.name if site and getattr(site, "general_contractor_id", None) else ""
        primary_name = primary.name if primary else ""
        cleaned["general_contractor_name"] = gc_name
        cleaned["primary_company_name"] = primary_name
        cleaned["billing_contractor_name"] = primary_name or gc_name
        return cleaned


WorkRecordForm = WorkRecordBasicForm
