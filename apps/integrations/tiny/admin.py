from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import TinyConnection, TinyOrderSync, TinyProductLink, TinySyncLog


@admin.register(TinyConnection)
class TinyConnectionAdmin(ModelAdmin):
    list_display = ("__str__", "status", "price_channel", "order_situacoes", "last_order_sync_at", "updated_at")
    readonly_fields = ("status", "last_error", "access_expires_at", "refresh_expires_at", "last_order_sync_at")


@admin.register(TinyProductLink)
class TinyProductLinkAdmin(ModelAdmin):
    list_display = ("variant", "tiny_id", "last_price", "last_pushed_at", "last_error")
    search_fields = ("variant__sku", "tiny_id")


@admin.register(TinyOrderSync)
class TinyOrderSyncAdmin(ModelAdmin):
    list_display = ("numero_pedido", "tiny_order_id", "situacao", "canal", "cliente", "valor", "result", "created_at")
    list_filter = ("result", "canal")
    search_fields = ("numero_pedido", "cliente")
    readonly_fields = [f.name for f in TinyOrderSync._meta.fields]


@admin.register(TinySyncLog)
class TinySyncLogAdmin(ModelAdmin):
    list_display = ("created_at", "kind", "ok", "summary")
    list_filter = ("kind", "ok")
