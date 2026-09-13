from django.contrib import admin
from .models import (
    GeneralContractor, Company, Site, WorkType,
    Worker, WorkSize, WorkerDefaultRate, GeneralContractorRate, WorkerRate, CompanyRate,
    PrintedDocument, PrintedDocumentItem,
)


@admin.register(GeneralContractor)
class GeneralContractorAdmin(admin.ModelAdmin):
    list_display = ("name", "closing_day")
    list_editable = ("closing_day",)
    search_fields = ("name",)


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "general_contractor")
    search_fields = ("name",)
    list_filter = ("general_contractor",)
    autocomplete_fields = ("general_contractor",)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(WorkType)
class WorkTypeAdmin(admin.ModelAdmin):
    search_fields = ("name",)


class WorkerRateInline(admin.TabularInline):
    model = WorkerRate
    extra = 1
    autocomplete_fields = ("work_size",)
    verbose_name = "独自単価"
    verbose_name_plural = "独自単価（寸法ごと）"


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ("name", "use_common_rate", "temoto_percent", "company")
    list_filter = ("use_common_rate",)
    search_fields = ("name",)
    autocomplete_fields = ("company",)
    exclude = ("worker_type",)
    inlines = (WorkerRateInline,)
    fieldsets = (
        (None, {
            "fields": ("name", "temoto_percent", "company"),
        }),
        ("単価", {
            "fields": ("use_common_rate",),
            "description": "共通単価を使う場合はチェックを付けたままにします。"
                           "この人だけ単価が違う場合はチェックを外し、下の表で寸法ごとの単価を入力します。",
        }),
    )

    class Media:
        js = ("workapp/js/worker_admin.js",)


@admin.register(WorkSize)
class WorkSizeAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(WorkerDefaultRate)
class WorkerDefaultRateAdmin(admin.ModelAdmin):
    list_display = ("work_size", "unit_price")
    autocomplete_fields = ("work_size",)


@admin.register(GeneralContractorRate)
class GeneralContractorRateAdmin(admin.ModelAdmin):
    list_display = ("general_contractor", "work_size", "unit_price")
    autocomplete_fields = ("general_contractor", "work_size")


@admin.register(WorkerRate)
class WorkerRateAdmin(admin.ModelAdmin):
    list_display = ("worker", "work_size", "unit_price")
    autocomplete_fields = ("worker", "work_size")


@admin.register(CompanyRate)
class CompanyRateAdmin(admin.ModelAdmin):
    list_display = ("company", "work_size", "unit_price")
    autocomplete_fields = ("company", "work_size")


class PrintedDocumentItemInline(admin.TabularInline):
    model = PrintedDocumentItem
    extra = 0
    can_delete = False
    readonly_fields = ("work_record", "line_total", "snapshot")
    verbose_name = "印刷時点の明細"
    verbose_name_plural = "印刷時点の明細（手で直す必要はありません）"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PrintedDocument)
class PrintedDocumentAdmin(admin.ModelAdmin):
    list_display = ("kind", "party_name", "period_start", "period_end", "status", "total_amount")
    search_fields = ("party_name",)
    autocomplete_fields = ("party_contractor", "party_worker", "party_company")
    inlines = (PrintedDocumentItemInline,)
    readonly_fields = (
        "kind", "party_name", "party_contractor", "party_worker", "party_company",
        "period_start", "period_end", "total_amount", "status", "printed_at", "created_at",
    )


@admin.register(PrintedDocumentItem)
class PrintedDocumentItemAdmin(admin.ModelAdmin):
    list_display = ("document", "work_record", "line_total")
    readonly_fields = ("document", "work_record", "line_total", "snapshot")

    def has_module_permission(self, request):
        return False
