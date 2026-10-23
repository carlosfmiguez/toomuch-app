"""Serviços do catálogo: leitura de metadados de arquivos e geração de thumbnails."""

from __future__ import annotations

import io
from decimal import Decimal

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.files3d import readers

from .models import Asset

PHOTO_THUMB_PX = 800


def parse_asset(asset: Asset) -> Asset:
    """Lê o arquivo conforme o tipo e grava o resultado em meta/grams/tempo/thumbnail."""
    if not asset.file:
        return asset
    update = ["meta", "grams_total", "print_time_min", "parsed_at", "parse_error", "thumbnail"]
    asset.parse_error = ""
    try:
        asset.file.open("rb")
        if asset.kind == Asset.Kind.THREEMF_SLICED:
            data = readers.read_sliced_3mf(asset.file)
            thumb = data.pop("thumbnail_png", None)
            old_mode = (asset.meta or {}).get("plates_mode")
            if old_mode and len((asset.meta or {}).get("plates", [])) == len(data.get("plates", [])):
                data["plates_mode"] = old_mode  # reler o mesmo arquivo não desfaz a decisão tamanhos/partes
            asset.meta = data
            asset.grams_total = readers.decimal_grams(data["grams_total"])
            asset.print_time_min = data["time_min"]
            if thumb and not asset.thumbnail:
                asset.thumbnail.save(f"{asset.pk or 'novo'}-3mf.png", ContentFile(thumb), save=False)
        elif asset.kind == Asset.Kind.THREEMF_PROJECT:
            asset.meta = readers.read_project_3mf(asset.file)
        elif asset.kind == Asset.Kind.STL:
            density = 1.24
            asset.meta = readers.read_stl(asset.file, density_g_cm3=density)
        elif asset.kind in (Asset.Kind.PHOTO, Asset.Kind.RENDER):
            asset.file.seek(0)
            _make_photo_thumbnail(asset)
        asset.parsed_at = timezone.now()
    except readers.ReadError as exc:
        asset.parse_error = str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - nunca derrubar o upload por causa da leitura
        asset.parse_error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        try:
            asset.file.close()
        except Exception:  # noqa: BLE001
            pass
    asset.save(update_fields=update)
    return asset


def _make_photo_thumbnail(asset: Asset) -> None:
    from PIL import Image, ImageOps

    img = Image.open(asset.file)
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    asset.meta = {**asset.meta, "width": w, "height": h, "ratio": round(w / h, 3) if h else None}
    img.thumbnail((PHOTO_THUMB_PX, PHOTO_THUMB_PX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    asset.thumbnail.save(f"{asset.pk or 'novo'}-thumb.jpg", ContentFile(buf.getvalue()), save=False)


def grams_for_variant_preview(variant) -> Decimal:
    """Soma de gramas estimadas das peças da variante (para listas rápidas)."""
    total = Decimal("0")
    for item in variant.bom_items.filter(kind="part").select_related("part"):
        asset = item.part.production_asset()
        if asset and asset.grams_total:
            total += asset.grams_total * item.quantity
    return total
