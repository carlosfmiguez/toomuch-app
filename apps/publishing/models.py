"""Publicação: o que foi enviado para onde, quando e com qual preço."""

from django.db import models


class Publication(models.Model):
    class Destination(models.TextChoices):
        PDF = "pdf", "Catálogo PDF"
        WEB = "web", "Catálogo web"
        ML = "ml", "Mercado Livre"
        SHOPEE = "shopee", "Shopee"
        TINY = "tiny", "Tiny ERP"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        SENT = "sent", "Enviado"
        LIVE = "live", "Publicado"
        REMOVED = "removed", "Removido"
        ERROR = "error", "Erro"

    variant = models.ForeignKey("catalog.Variant", verbose_name="variante", on_delete=models.CASCADE, related_name="publications")
    destination = models.CharField("destino", max_length=10, choices=Destination.choices)
    channel = models.ForeignKey("costing.Channel", verbose_name="canal", on_delete=models.SET_NULL, null=True, blank=True)
    price_sent = models.DecimalField("preço enviado (R$)", max_digits=10, decimal_places=2, null=True, blank=True)
    external_id = models.CharField("id externo", max_length=80, blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.PENDING)
    published_at = models.DateTimeField("publicado em", null=True, blank=True)
    note = models.CharField("observação", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "publicação"
        verbose_name_plural = "publicações"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.variant} → {self.get_destination_display()} ({self.get_status_display()})"
