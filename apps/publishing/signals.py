"""Invalida o cache do catálogo público quando algo que entra no checklist muda."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.catalog.models import Asset, BOMItem, Product, Variant
from apps.costing.models import PriceByChannel

from .views_public import invalidate_public_cache

for model in (Product, Variant, Asset, BOMItem, PriceByChannel):
    receiver(post_save, sender=model, weak=False)(invalidate_public_cache)
    receiver(post_delete, sender=model, weak=False)(invalidate_public_cache)
