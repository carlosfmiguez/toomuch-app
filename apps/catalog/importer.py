"""
Importação em lote: de uma pasta (ou ZIP) com os modelos e fotos para produtos em rascunho.

Fluxo em duas etapas, para revisar antes de criar:

1. ``scan(pasta)`` varre a pasta. Cada subpasta de primeiro nível vira um produto;
   arquivos soltos na raiz são agrupados pelo nome base (``vaso-face.stl`` +
   ``vaso-face.jpg`` → produto "Vaso face"). Gera uma planilha de mapeamento
   (XLSX) com nome, categoria sugerida, SKU-base sugerido e os arquivos encontrados.
2. ``apply(pasta, planilha)`` lê a planilha revisada e cria os produtos em rascunho
   com peças, arquivos 3D e fotos. Idempotente: arquivo já importado (mesmo hash)
   é ignorado; produto com o mesmo SKU-base é reaproveitado.

Também funciona sem planilha (``apply(pasta)``), usando as sugestões do scan.

Manifesto opcional: um ``produtos.csv`` na raiz do ZIP/pasta (separado por ``;`` ou ``,``)
com as colunas ``Pasta; Nome; Categoria; SKU-base; Origem; Autor; Link; Termos;
Descrição curta; Atribuição``. Ele substitui as sugestões automáticas da pasta
correspondente, para a importação já chegar com nome, categoria e licença certos.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from django.core.files import File
from django.db import transaction

from .models import Asset, Category, Part, Product, Variant

EXT_KIND = {
    ".3mf": Asset.Kind.THREEMF_PROJECT,
    ".stl": Asset.Kind.STL,
    ".step": Asset.Kind.STEP, ".stp": Asset.Kind.STEP,
    ".f3d": Asset.Kind.F3D,
    ".jpg": Asset.Kind.PHOTO, ".jpeg": Asset.Kind.PHOTO, ".png": Asset.Kind.PHOTO, ".webp": Asset.Kind.PHOTO,
    ".pdf": Asset.Kind.PDF,
}
IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}

# palavras no nome → código de categoria
CATEGORY_HINTS = [
    (("natal", "christmas", "papai noel"), "NAT"),
    (("pascoa", "páscoa", "easter"), "PAS"),
    (("luminaria", "luminária", "abajur", "lampada", "lâmpada", "lamp", "luz"), "LUM"),
    (("vaso", "cachepot", "cachepô", "planter", "jarra"), "VAS"),
    (("porta", "organizador", "suporte", "bandeja", "caixa", "holder", "stand", "tray", "box", "escova"), "UTI"),
    (("cruz", "anjo", "nossa senhora", "santo", "santa", "oratorio", "oratório", "terço", "terco", "jesus"), "REL"),
    (("natal", "christmas", "arvore", "árvore", "rena", "sino", "papai noel"), "NAT"),
    (("pascoa", "páscoa", "easter", "coelho", "ovo"), "PAS"),
    (("presente", "dia das", "namorados", "halloween"), "SAZ"),
]

MANIFEST_NAMES = ("produtos.csv", "_produtos.csv", "manifesto.csv")


@dataclass
class ScannedProduct:
    folder: str
    name: str
    category_code: str
    sku_base: str
    models: list[Path] = field(default_factory=list)
    photos: list[Path] = field(default_factory=list)
    others: list[Path] = field(default_factory=list)
    origin: str = "own"
    author: str = ""
    license_url: str = ""
    license_terms: str = ""
    short_description: str = ""
    attribution: bool = False
    note: str = ""

    @property
    def files(self) -> list[Path]:
        return self.models + self.photos + self.others


def slugify_ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", " ", text).strip()


def humanize(name: str) -> str:
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\b(v\d+|final|novo|new|ok)\b", "", name, flags=re.I).strip()
    return name[:1].upper() + name[1:] if name else name


def suggest_category(name: str, default: str = "UTI") -> str:
    low = slugify_ascii(name).lower() + " " + name.lower()
    for words, code in CATEGORY_HINTS:
        if any(w in low for w in words):
            return code
    return default


def suggest_sku(name: str, category_code: str, taken: set[str]) -> str:
    words = [w for w in slugify_ascii(name).upper().split() if w not in ("DE", "DA", "DO", "COM", "E", "3D")]
    # pula a palavra da categoria (ex.: "Luminária Solaris" → SOL)
    skip = {"LUMINARIA", "VASO", "PORTA", "CRUZ", "ANJO", "SUPORTE", "ORGANIZADOR"}
    core = [w for w in words if w not in skip] or words or ["MOD"]
    base = core[0][:3] if len(core) == 1 else (core[0][:2] + core[1][:1])
    if len(core) > 1 and len(core[0]) >= 3:
        base = core[0][:3]
    candidate = f"TM-{category_code}-{base}"
    n = 2
    while candidate in taken:
        candidate = f"TM-{category_code}-{base}{n}"
        n += 1
    taken.add(candidate)
    return candidate


def _group_root_files(root: Path) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for f in sorted(root.iterdir()):
        if f.is_file() and f.suffix.lower() in EXT_KIND and f.name.lower() not in IGNORED_NAMES:
            # "vaso-face-1", "vaso_face 2", "vaso-face-b" → "vaso-face"; "porta-escova" fica inteiro
            stem = re.sub(r"[\s_\-]+(\d+|[a-z])$", "", f.stem, flags=re.I)
            groups.setdefault(stem.strip().lower(), []).append(f)
    return groups


def scan(root: Path | str) -> list[ScannedProduct]:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"Pasta não encontrada: {root}")
    taken = set(Product.objects.values_list("sku_base", flat=True))
    found: list[ScannedProduct] = []

    def classify(sp: ScannedProduct, files: list[Path]) -> None:
        for f in files:
            kind = EXT_KIND.get(f.suffix.lower())
            if kind is None or f.name.lower() in IGNORED_NAMES:
                continue
            if kind == Asset.Kind.PHOTO:
                sp.photos.append(f)
            elif kind in (Asset.Kind.STL, Asset.Kind.THREEMF_PROJECT, Asset.Kind.STEP, Asset.Kind.F3D):
                sp.models.append(f)
            else:
                sp.others.append(f)

    for entry in sorted(root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            name = humanize(entry.name)
            code = suggest_category(name)
            sp = ScannedProduct(folder=entry.name, name=name, category_code=code, sku_base=suggest_sku(name, code, taken))
            classify(sp, [f for f in sorted(entry.rglob("*")) if f.is_file()])
            if sp.files:
                found.append(sp)
    for stem, files in _group_root_files(root).items():
        name = humanize(stem)
        code = suggest_category(name)
        sp = ScannedProduct(folder="", name=name, category_code=code, sku_base=suggest_sku(name, code, taken))
        classify(sp, files)
        if sp.files:
            found.append(sp)
    for sp in found:
        if not sp.models:
            sp.note = "só fotos, sem arquivo 3D"
        elif not sp.photos:
            sp.note = "sem foto"
    _apply_manifest(root, found)
    return found


def read_manifest(root: Path) -> dict[str, dict]:
    """produtos.csv da raiz → {pasta: linha}. Aceita ; ou , e UTF-8 com ou sem BOM."""
    for name in MANIFEST_NAMES:
        path = root / name
        if path.is_file():
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
            dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,")
            rows = csv.DictReader(io.StringIO(text), dialect=dialect)
            return {str(r.get("Pasta") or "").strip(): {k.strip(): (v or "").strip() for k, v in r.items() if k} for r in rows}
    return {}


def _apply_manifest(root: Path, found: list[ScannedProduct]) -> None:
    manifest = read_manifest(root)
    if not manifest:
        return
    for sp in found:
        row = manifest.get(sp.folder)
        if not row:
            continue
        sp.name = row.get("Nome") or sp.name
        sp.category_code = (row.get("Categoria") or sp.category_code).upper()
        sp.sku_base = (row.get("SKU-base") or sp.sku_base).upper()
        origin = (row.get("Origem") or "").lower()
        if origin in Product.Origin.values:
            sp.origin = origin
        sp.author = row.get("Autor", "")
        sp.license_url = row.get("Link", "")
        sp.license_terms = row.get("Termos", "")
        sp.short_description = row.get("Descrição curta", "")
        sp.attribution = (row.get("Atribuição", "") or "").lower() in ("sim", "s", "yes", "1", "true")


MAPPING_COLUMNS = ["Pasta", "Nome", "Categoria (código)", "SKU-base", "Origem", "Autor/licença", "Arquivos 3D", "Fotos", "Outros", "Observação", "Importar?"]


def write_mapping(products: list[ScannedProduct], path: Path | str) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Mapeamento"
    ws.append(MAPPING_COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for sp in products:
        ws.append([
            sp.folder, sp.name, sp.category_code, sp.sku_base, "own", "",
            "; ".join(p.name for p in sp.models),
            "; ".join(p.name for p in sp.photos), "; ".join(p.name for p in sp.others), sp.note, "sim",
        ])
    cats = Category.objects.values_list("code", flat=True)
    dv_cat = DataValidation(type="list", formula1='"' + ",".join(cats) + '"', allow_blank=False)
    dv_orig = DataValidation(type="list", formula1='"own,licensed,unlicensed"', allow_blank=False)
    dv_imp = DataValidation(type="list", formula1='"sim,não"', allow_blank=False)
    ws.add_data_validation(dv_cat)
    ws.add_data_validation(dv_orig)
    ws.add_data_validation(dv_imp)
    last = max(2, ws.max_row)
    dv_cat.add(f"C2:C{last}")
    dv_orig.add(f"E2:E{last}")
    dv_imp.add(f"K2:K{last}")
    widths = [22, 32, 12, 16, 11, 24, 40, 40, 20, 24, 10]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    legend = wb.create_sheet("Legenda")
    legend.append(["Categoria", "Nome"])
    for c in Category.objects.all():
        legend.append([c.code, c.name])
    legend.append([])
    legend.append(["Origem", "own = autoral · licensed = terceiros com licença comercial · unlicensed = terceiros sem licença de venda"])
    legend.append(["Importar?", "não = pular esta linha"])
    path = Path(path)
    wb.save(path)
    return path


def read_mapping(path: Path | str) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Mapeamento"] if "Mapeamento" in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h or "").strip() for h in rows[0]]
    out = []
    for raw in rows[1:]:
        if not raw or all(v in (None, "") for v in raw):
            continue
        row = {header[i]: raw[i] for i in range(min(len(header), len(raw)))}
        out.append(row)
    return out


def _is_sliced(path: Path) -> bool:
    from apps.files3d import readers

    try:
        with open(path, "rb") as fh:
            data = readers.read_sliced_3mf(fh)
        return bool(data.get("grams_total"))
    except Exception:  # noqa: BLE001 - 3MF de projeto, sem fatiamento
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ImportReport:
    created_products: int = 0
    reused_products: int = 0
    files_added: int = 0
    files_skipped: int = 0
    skipped_rows: int = 0
    errors: list[str] = field(default_factory=list)


def apply(root: Path | str, mapping: Path | str | None = None, user=None, default_category: str = "UTI") -> ImportReport:
    root = Path(root)
    scanned = {(sp.folder, sp.name): sp for sp in scan(root)}
    by_folder = {sp.folder: sp for sp in scanned.values() if sp.folder}
    by_name = {sp.name: sp for sp in scanned.values()}
    rows = read_mapping(mapping) if mapping else [
        {"Pasta": sp.folder, "Nome": sp.name, "Categoria (código)": sp.category_code, "SKU-base": sp.sku_base,
         "Origem": sp.origin, "Autor/licença": sp.author, "Importar?": "sim"} for sp in scanned.values()
    ]
    report = ImportReport()
    for row in rows:
        if str(row.get("Importar?") or "sim").strip().lower() in ("não", "nao", "n", "no", "0"):
            report.skipped_rows += 1
            continue
        folder = str(row.get("Pasta") or "").strip()
        name = str(row.get("Nome") or "").strip()
        sp = by_folder.get(folder) if folder else by_name.get(name)
        if sp is None:
            report.errors.append(f"Linha '{name or folder}': pasta/arquivos não encontrados.")
            continue
        try:
            with transaction.atomic():
                _import_one(sp, row, root, user, default_category, report)
        except Exception as exc:  # noqa: BLE001 - uma linha ruim não derruba o lote
            report.errors.append(f"{name or folder}: {type(exc).__name__}: {exc}")
    return report


def _import_one(sp: ScannedProduct, row: dict, root: Path, user, default_category: str, report: ImportReport) -> None:
    code = str(row.get("Categoria (código)") or sp.category_code or default_category).strip().upper()
    category = Category.objects.filter(code=code).first() or Category.objects.filter(code=default_category).first() or Category.objects.first()
    sku_base = str(row.get("SKU-base") or sp.sku_base).strip().upper()
    name = str(row.get("Nome") or sp.name).strip()
    origin = str(row.get("Origem") or sp.origin or "own").strip().lower()
    if origin not in Product.Origin.values:
        origin = Product.Origin.OWN
    product, created = Product.objects.get_or_create(
        sku_base=sku_base,
        defaults={"name": name, "category": category, "status": Product.Status.DRAFT, "origin": origin,
                  "license_author": (str(row.get("Autor/licença") or "").strip() or sp.author)[:120],
                  "license_url": sp.license_url[:200], "license_terms": sp.license_terms[:200],
                  "short_description": sp.short_description[:200], "license_attribution_required": sp.attribution},
    )
    if created:
        report.created_products += 1
    else:
        report.reused_products += 1
    if not product.variants.exists():
        Variant.objects.create(product=product, sku=f"{sku_base}-U", size="Único", is_default=True)

    existing_hashes = set(Asset.objects.filter(product=product).values_list("sha256", flat=True))
    for path in sp.files:
        digest = _sha256(path)
        if digest in existing_hashes:
            report.files_skipped += 1
            continue
        kind = EXT_KIND[path.suffix.lower()]
        production = False
        if kind == Asset.Kind.THREEMF_PROJECT and _is_sliced(path):
            kind, production = Asset.Kind.THREEMF_SLICED, True  # já tem gramas e tempo: vira arquivo de produção
        part = None
        if kind in (Asset.Kind.STL, Asset.Kind.THREEMF_PROJECT, Asset.Kind.THREEMF_SLICED, Asset.Kind.STEP, Asset.Kind.F3D):
            part_name = humanize(path.stem) if len(sp.models) > 1 else "Peça única"
            part, _ = Part.objects.get_or_create(product=product, name=part_name[:80])
        asset = Asset(product=product, part=part, kind=kind, uploaded_by=user, name=path.name, is_production=production,
                      is_main=(kind == Asset.Kind.PHOTO and not product.assets.filter(kind=Asset.Kind.PHOTO).exists()))
        with open(path, "rb") as fh:
            asset.file.save(path.name, File(fh), save=True)
        existing_hashes.add(asset.sha256)
        report.files_added += 1
