from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from .models import AccessToken, OAuthClient


@admin.register(AccessToken)
class AccessTokenAdmin(ModelAdmin):
    list_display = ("client", "user", "created_at", "last_used_at", "expires_at", "refresh_expires_at", "revoked")
    list_filter = ("revoked",)
    readonly_fields = [f.name for f in AccessToken._meta.fields if f.name != "revoked"]
    actions = ["revoke"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Revogar acesso")
    def revoke(self, request, queryset):
        n = queryset.update(revoked=True)
        messages.success(request, f"{n} acesso(s) revogado(s). O Claude vai pedir login de novo.")


@admin.register(OAuthClient)
class OAuthClientAdmin(ModelAdmin):
    list_display = ("name", "client_id", "created_at")
    readonly_fields = ("client_id", "redirect_uris", "created_at")
