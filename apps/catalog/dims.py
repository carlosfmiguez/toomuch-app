"""
Medidas sugeridas a partir dos arquivos 3D (3MF fatiado/projeto ou STL).

Altura = Z; largura = o maior lado da base; profundidade = o menor. Para peça com placa do 3MF
escolhida (um tamanho por placa), usa a caixa daquela placa, com a escala aplicada.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Asset, Part, Product

MODEL_KINDS = (Asset.Kind.THREEMF_SLICED, Asset.Kind.THREEMF_PROJECT, Asset.Kind.STL)


@dataclass
class Dims:
    label: str
    height: int
    width: int
    depth: int
    source: str

    @property
    def text(self) -> str:
        return f"{self.height} × {self.width} × {self.depth} mm"

    def as_dict(self) -> dict:
        return {"rotulo": self.label, "altura_mm": self.height, "largura_mm": self.width,
                "profundidade_mm": self.depth, "fonte": self.source}


def _from_box(box: dict | None, label: str, source: str) -> Dims | None:
    if not box or not box.get("z"):
        return None
    a, b = sorted((float(box.get("x") or 0), float(box.get("y") or 0)), reverse=True)
    return Dims(label, round(float(box["z"])), round(a), round(b), source)


def ensure_geometry(asset: Asset) -> dict | None:
    """Geometria com transformações; arquivos lidos antes desta versão são medidos uma vez e guardados."""
    from apps.files3d import readers

    geo = (asset.meta or {}).get("geometry")
    if geo and geo.get("v") == readers.GEOMETRY_VERSION:
        return geo
    if asset.kind not in (Asset.Kind.THREEMF_SLICED, Asset.Kind.THREEMF_PROJECT) or not asset.file:
        return None
    try:
        asset.file.open("rb")
        geo = readers.read_geometry(asset.file)
    except Exception:  # noqa: BLE001 - sugestão nunca quebra a tela
        geo = None
    finally:
        try:
            asset.file.close()
        except Exception:  # noqa: BLE001
            pass
    asset.meta = {**(asset.meta or {}), "geometry": geo or {"v": readers.GEOMETRY_VERSION, "overall": None, "plates": {}}}
    asset.save(update_fields=["meta"])
    return geo


def asset_dims(asset: Asset, plate_index: int | None, label: str, compute: bool) -> Dims | None:
    if asset.kind == Asset.Kind.STL:
        return _from_box((asset.meta or {}).get("bbox_mm"), label, asset.name)
    geo = ensure_geometry(asset) if compute else (asset.meta or {}).get("geometry")
    if not geo:
        return None
    plates = geo.get("plates") or {}
    if plate_index is None and asset.kind == Asset.Kind.THREEMF_SLICED and len(asset.plates) == 1:
        plate_index = asset.plates[0].get("index")  # 3MF de uma placa só: mede só ela, não o projeto todo
    box = plates.get(str(plate_index)) if plate_index else None
    if box is None and len(plates) > 1 and plate_index is None:
        return None  # projeto com várias placas sem saber qual: não chuta
    return _from_box(box or geo.get("overall"), label, asset.name)


def part_dims(part: Part, compute: bool = False) -> Dims | None:
    label = part.only_sizes or part.name
    prod = part.production_assets().first()
    if prod is not None:
        d = asset_dims(prod, part.plate_index, label, compute)
        if d:
            return d
    for asset in part.assets.filter(kind__in=MODEL_KINDS).exclude(pk=getattr(prod, "pk", None)).order_by("-created_at"):
        d = asset_dims(asset, None, label, compute)
        if d:
            return d
    if part.plate_index:
        # placa ainda não fatiada (ex.: tamanho 100% de um projeto em que só a 70% foi exportada):
        # a geometria do projeto inteiro vem dentro do 3MF da peça irmã
        for asset in Asset.objects.filter(product=part.product, kind=Asset.Kind.THREEMF_SLICED, is_production=True):
            geo = ensure_geometry(asset) if compute else (asset.meta or {}).get("geometry")
            box = ((geo or {}).get("plates") or {}).get(str(part.plate_index))
            if box:
                return _from_box(box, label, f"{asset.name} (placa {part.plate_index})")
    return None


def suggestions(product: Product, compute: bool = False) -> tuple[list[Dims], Dims | None]:
    """(medidas por peça/tamanho, sugestão para a ficha). A sugestão só existe quando é inequívoca:
    peça única, ou uma peça por tamanho (usa o maior tamanho)."""
    parts = product.all_parts()
    rows = [d for d in (part_dims(p, compute) for p in parts) if d]
    if not rows:
        lib = [a for a in product.assets.filter(part__isnull=True, kind__in=MODEL_KINDS)]
        if len(lib) == 1:
            d = asset_dims(lib[0], None, lib[0].name, compute)
            rows = [d] if d else []
        return rows, (rows[0] if len(rows) == 1 and len(parts) <= 1 else None)
    if len(parts) == 1 and len(rows) == 1:
        return rows, rows[0]
    if all(p.only_sizes for p in parts) and len(rows) == len(parts) and len({p.only_sizes.upper() for p in parts}) == len(parts):
        return rows, max(rows, key=lambda d: d.height)
    return rows, None
