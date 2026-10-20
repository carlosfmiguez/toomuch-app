from django.contrib import admin, messages

from .models import PrintJob, ProductionOrder


class PrintJobInline(admin.TabularInline):
    model = PrintJob
    extra = 0
    fields = ("part", "sequence", "printer", "filament", "spool", "est_minutes", "actual_minutes", "est_grams", "actual_grams", "result", "failure_reason")
    readonly_fields = ("part", "sequence", "est_minutes", "est_grams")
    show_change_link = True


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("__str__", "origin", "external_id", "status", "priority", "due_date", "printer", "created_at")
    list_filter = ("status", "origin", "printer")
    search_fields = ("external_id", "variant__sku", "variant__product__name")
    autocomplete_fields = ("variant",)
    inlines = [PrintJobInline]
    actions = ["explode"]

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if not change:
            jobs = obj.explode()
            messages.info(request, f"{len(jobs)} trabalho(s) de impressão criado(s).")

    @admin.action(description="Explodir em trabalhos de impressão")
    def explode(self, request, queryset):
        total = sum(len(o.explode()) for o in queryset)
        messages.success(request, f"{total} trabalho(s) no total.")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = ("__str__", "printer", "filament", "result", "started_at", "finished_at", "actual_minutes", "grams_used", "stock_applied")
    list_filter = ("result", "printer", "filament__material")
    search_fields = ("order__variant__sku", "part__name")
    readonly_fields = ("stock_applied",)
    actions = ["mark_started", "mark_ok", "mark_failed"]

    @admin.action(description="Marcar início agora")
    def mark_started(self, request, queryset):
        for job in queryset:
            job.start(operator=request.user)
        messages.success(request, f"{queryset.count()} trabalho(s) iniciado(s).")

    @admin.action(description="Concluir com sucesso (baixa o filamento)")
    def mark_ok(self, request, queryset):
        for job in queryset:
            job.finish(ok=True, operator=request.user)
        messages.success(request, f"{queryset.count()} trabalho(s) concluído(s); estoque baixado.")

    @admin.action(description="Marcar falha (refugo e reimpressão)")
    def mark_failed(self, request, queryset):
        for job in queryset:
            job.finish(ok=False, failure_reason=job.failure_reason or "Falha na impressão", operator=request.user)
        messages.warning(request, f"{queryset.count()} trabalho(s) com falha; reimpressões abertas.")
