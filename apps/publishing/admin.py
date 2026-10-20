from django.contrib import admin

from .models import Publication


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ("variant", "destination", "channel", "status", "price_sent", "external_id", "published_at")
    list_filter = ("destination", "status", "channel")
    search_fields = ("variant__sku", "external_id")
    autocomplete_fields = ("variant",)
