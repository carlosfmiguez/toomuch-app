"""
Compila locale/*/LC_MESSAGES/django.po em django.mo sem precisar do gettext instalado.
Uso: python scripts/compile_messages.py   (roda também no build do Docker)
"""

from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
for po_path in (ROOT / "locale").glob("*/LC_MESSAGES/*.po"):
    po = polib.pofile(str(po_path))
    po.save_as_mofile(str(po_path.with_suffix(".mo")))
    print(f"{po_path.relative_to(ROOT)} -> {len(po)} mensagens")
