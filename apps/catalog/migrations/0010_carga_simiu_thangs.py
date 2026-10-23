"""Roda uma única vez a carga dos modelos simiu/Thangs (ver o comando carregar_simiu_thangs)."""

from django.db import migrations


def carregar(apps, schema_editor):
    from django.core.management import call_command

    try:
        call_command("carregar_simiu_thangs")
    except Exception as exc:  # noqa: BLE001
        print(f"[simiu] carga não concluída: {exc}")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("catalog", "0009_reler_3mf_placas")]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
