from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Asset
from .services import parse_asset


@receiver(post_save, sender=Asset)
def parse_uploaded_asset(sender, instance: Asset, created, update_fields=None, **kwargs):
    """Lê metadados logo depois do upload (ou quando o arquivo troca)."""
    if update_fields:
        return  # chamadas internas do próprio parser
    if instance.file and instance.parsed_at is None and not instance.parse_error:
        parse_asset(instance)
