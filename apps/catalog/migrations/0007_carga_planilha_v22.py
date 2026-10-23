"""Roda uma única vez a carga da planilha V2.2 (ver o comando carregar_planilha_v22).

Nunca derruba o deploy: qualquer erro é registrado e a migração segue.
"""

from django.db import migrations


def carregar(apps, schema_editor):
    from django.core.management import call_command

    try:
        call_command("carregar_planilha_v22")
    except Exception as exc:  # noqa: BLE001
        print(f"[planilha v2.2] carga não concluída: {exc}")


class Migration(migrations.Migration):
    atomic = False  # cada produto é gravado na própria transação
    dependencies = [
        ("catalog", "0006_estimativa_e_tamanho_da_peca"),
        ("inventory", "0002_initial"),
        ("costing", "0001_initial"),
    ]
    operations = [migrations.RunPython(carregar, migrations.RunPython.noop)]
