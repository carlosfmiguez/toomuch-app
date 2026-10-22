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


# --- custo sempre em dia ------------------------------------------------------
# Tudo o que entra no custo de uma variante dispara o recálculo das variantes afetadas.
# (Sem isto, uma compra de filamento lançada depois de criar a variante deixava o filamento a R$ 0.)

def _refresh(variants) -> None:
    from .workflow import refresh_variant

    for v in variants.exclude(product__status="discontinued").select_related("product").distinct():
        refresh_variant(v)


def _cost_fields_changed(update_fields, fields) -> bool:
    return update_fields is None or bool(set(update_fields) & set(fields))


@receiver(post_save, sender="inventory.FilamentSKU")
def filament_cost_changed(sender, instance, update_fields=None, **kwargs):
    if not _cost_fields_changed(update_fields, ["avg_cost_per_g"]):
        return  # baixa de estoque da produção não muda o custo
    from django.db.models import Q

    from .models import Variant

    _refresh(Variant.objects.filter(Q(main_filament=instance) | Q(secondary_filament=instance)))


@receiver(post_save, sender="catalog.Component")
def component_cost_changed(sender, instance, update_fields=None, **kwargs):
    if not _cost_fields_changed(update_fields, ["unit_cost", "is_packaging"]):
        return
    from .models import Variant

    _refresh(Variant.objects.filter(bom_items__component=instance))


def _refresh_all(**kwargs):
    from .models import Variant

    _refresh(Variant.objects.all())


for _model in ("costing.GlobalParams", "costing.Printer", "costing.ChannelFee"):
    post_save.connect(_refresh_all, sender=_model, dispatch_uid=f"refresh-all-{_model}")
