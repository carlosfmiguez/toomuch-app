"""Relê uma vez os 3MF fatiados para guardar nome/gramas por placa e as placas do projeto não fatiadas."""

from django.db import migrations


def reler(apps, schema_editor):
    from apps.catalog.models import Asset
    from apps.catalog.services import parse_asset

    for asset in Asset.objects.filter(kind=Asset.Kind.THREEMF_SLICED).exclude(file=""):
        if "project_plates" in (asset.meta or {}):
            continue
        try:
            parse_asset(asset)
        except Exception as exc:  # noqa: BLE001 - arquivo ausente etc. não derruba o deploy
            print(f"[3mf placas] {asset.pk}: {exc}")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("catalog", "0008_placa_da_peca")]
    operations = [migrations.RunPython(reler, migrations.RunPython.noop)]
