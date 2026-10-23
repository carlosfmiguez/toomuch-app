"""
Leitores de arquivos 3D.

- 3MF fatiado do Bambu Studio / OrcaSlicer (.gcode.3mf): gramas por filamento e
  por placa, tempo estimado, modelo de impressora, altura de camada, thumbnail.
- 3MF de projeto e STL: dimensões (caixa envolvente), volume e área.

Tudo retorna dicionários simples (JSON-serializáveis) que o Asset guarda em `meta`.
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal


class ReadError(Exception):
    pass


@dataclass
class Filament:
    extruder: int
    type: str = ""
    color: str = ""
    used_g: float = 0.0
    used_m: float = 0.0


@dataclass
class Plate:
    index: int
    time_s: int = 0
    weight_g: float = 0.0
    printer_model_id: str = ""
    objects: list[str] = field(default_factory=list)
    filaments: list[Filament] = field(default_factory=list)


_NS_MODEL = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def _open_zip(source) -> zipfile.ZipFile:
    if isinstance(source, (str, bytes)) and not isinstance(source, bytes):
        return zipfile.ZipFile(source)
    if isinstance(source, bytes):
        return zipfile.ZipFile(io.BytesIO(source))
    if hasattr(source, "read"):
        # Arquivo com seek: o zipfile lê direto dele, sem copiar tudo para a memória.
        try:
            source.seek(0)
            return zipfile.ZipFile(source)
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            data = source.read()
            return zipfile.ZipFile(io.BytesIO(data))
    raise ReadError("Fonte de arquivo não suportada")


# ---------------------------------------------------------------------------
# 3MF fatiado
# ---------------------------------------------------------------------------


def _parse_slice_info(xml_bytes: bytes) -> tuple[list[Plate], dict]:
    root = ET.fromstring(xml_bytes)
    header = {}
    for item in root.findall("./header/header_item"):
        header[item.get("key", "")] = item.get("value", "")
    plates: list[Plate] = []
    for p in root.findall("./plate"):
        meta = {m.get("key"): m.get("value") for m in p.findall("./metadata")}
        plate = Plate(
            index=int(meta.get("index", len(plates) + 1) or 0),
            time_s=int(float(meta.get("prediction", 0) or 0)),
            weight_g=float(meta.get("weight", 0) or 0),
            printer_model_id=meta.get("printer_model_id", "") or "",
        )
        for o in p.findall("./object"):
            if o.get("skipped", "false") != "true":
                plate.objects.append(o.get("name", ""))
        for f in p.findall("./filament"):
            plate.filaments.append(Filament(
                extruder=int(f.get("id", 1) or 1), type=f.get("type", "") or "", color=f.get("color", "") or "",
                used_g=float(f.get("used_g", 0) or 0), used_m=float(f.get("used_m", 0) or 0),
            ))
        plates.append(plate)
    return plates, header


_GCODE_PATTERNS = {
    "grams": re.compile(r";\s*(?:total )?filament used \[g\]\s*=\s*([\d.,]+)", re.I),
    "time": re.compile(r";\s*(?:total )?estimated (?:printing )?time(?: \(normal mode\))?[:=]\s*(.+)", re.I),
    "model_time": re.compile(r";\s*model printing time:\s*(.+?);", re.I),
    "layer": re.compile(r";\s*layer_height\s*=\s*([\d.]+)", re.I),
    "printer": re.compile(r";\s*printer_model\s*=\s*(.+)", re.I),
}


def _parse_duration(text: str) -> int:
    """'5h 40m 3s' / '1d 2h' / '20403' → segundos"""
    text = text.strip()
    if text.isdigit():
        return int(text)
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def _parse_gcode_header(gcode: str) -> dict:
    head = gcode[:20000]
    out: dict = {}
    if m := _GCODE_PATTERNS["grams"].search(head):
        out["grams"] = float(m.group(1).replace(",", "."))
    if m := _GCODE_PATTERNS["time"].search(head):
        out["time_s"] = _parse_duration(m.group(1))
    if m := _GCODE_PATTERNS["layer"].search(head):
        out["layer_height"] = float(m.group(1))
    if m := _GCODE_PATTERNS["printer"].search(head):
        out["printer_model"] = m.group(1).strip()
    return out


def read_sliced_3mf(source) -> dict:
    """Lê um .gcode.3mf. Lança ReadError se não houver informação de fatiamento."""
    with _open_zip(source) as z:
        names = set(z.namelist())
        plates: list[Plate] = []
        header: dict = {}
        if "Metadata/slice_info.config" in names:
            plates, header = _parse_slice_info(z.read("Metadata/slice_info.config"))

        settings: dict = {}
        if "Metadata/project_settings.config" in names:
            try:
                settings = json.loads(z.read("Metadata/project_settings.config").decode("utf-8", "replace"))
            except json.JSONDecodeError:
                settings = {}

        if not plates:
            # Fallback: cabeçalho do G-code embutido
            gcodes = sorted(n for n in names if n.startswith("Metadata/plate_") and n.endswith(".gcode"))
            for i, name in enumerate(gcodes, start=1):
                info = _parse_gcode_header(z.read(name).decode("utf-8", "replace"))
                if info:
                    plates.append(Plate(index=i, time_s=info.get("time_s", 0), weight_g=info.get("grams", 0.0),
                                        filaments=[Filament(extruder=1, used_g=info.get("grams", 0.0))]))
                    settings.setdefault("layer_height", info.get("layer_height"))
                    settings.setdefault("printer_model", info.get("printer_model"))
        if not plates:
            raise ReadError("O arquivo não tem dados de fatiamento (Metadata/slice_info.config). Exporte o 3MF fatiado.")

        plate_names = {}
        if "Metadata/model_settings.config" in names:
            try:
                plate_names = _plate_names(z.read("Metadata/model_settings.config"))
            except Exception:  # noqa: BLE001 - nome da placa é opcional
                plate_names = {}

        thumbnail = None
        for cand in ("Metadata/plate_1.png", "Metadata/plate_1_small.png", "Metadata/top_1.png"):
            if cand in names:
                thumbnail = z.read(cand)
                break

        bbox = None
        try:
            bbox = _bbox_from_model_files(z)
        except Exception:  # noqa: BLE001 - geometria é opcional
            bbox = None

    # consolida filamentos por extrusora
    merged: dict[int, dict] = {}
    for p in plates:
        for f in p.filaments:
            m = merged.setdefault(f.extruder, {"extruder": f.extruder, "type": f.type, "color": f.color, "grams": 0.0, "meters": 0.0})
            m["grams"] = round(m["grams"] + f.used_g, 2)
            m["meters"] = round(m["meters"] + f.used_m, 2)
            m["type"] = m["type"] or f.type
            m["color"] = m["color"] or f.color
    filaments = sorted(merged.values(), key=lambda x: x["extruder"])
    grams_total = round(sum(f["grams"] for f in filaments) or sum(p.weight_g for p in plates), 2)
    time_s = sum(p.time_s for p in plates)

    def _first(key):
        v = settings.get(key)
        if isinstance(v, list):
            return v[0] if v else None
        return v

    return {
        "slicer": header.get("X-BBL-Client-Type", "") and f"{header.get('X-BBL-Client-Type')} {header.get('X-BBL-Client-Version', '')}".strip(),
        "printer_model": _first("printer_model") or (plates[0].printer_model_id if plates else ""),
        "layer_height": _to_float(_first("layer_height")),
        "nozzle": _to_float(_first("nozzle_diameter")),
        "plates": [
            {"index": p.index, "name": plate_names.get(p.index, ""), "time_s": p.time_s, "weight_g": p.weight_g,
             "grams": round(sum(f.used_g for f in p.filaments) or p.weight_g, 2), "objects": p.objects,
             "filaments": [f.__dict__ for f in p.filaments]}
            for p in plates
        ],
        "filaments": filaments,
        "grams_total": grams_total,
        "time_s": time_s,
        "time_min": int(round(time_s / 60)),
        "object_count": sum(len(p.objects) for p in plates),
        "bbox_mm": bbox,
        "thumbnail_png": thumbnail,
    }


def _plate_names(xml_bytes: bytes) -> dict[int, str]:
    """Nome que o usuário deu a cada placa no Bambu Studio (model_settings.config → plater_name)."""
    root = ET.fromstring(xml_bytes)
    out: dict[int, str] = {}
    for plate in root.findall("./plate"):
        meta = {m.get("key"): m.get("value") for m in plate.findall("./metadata")}
        try:
            idx = int(meta.get("plater_id") or 0)
        except ValueError:
            continue
        if idx and (meta.get("plater_name") or "").strip():
            out[idx] = meta["plater_name"].strip()
    return out


def _to_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Geometria: 3MF de projeto e STL
# ---------------------------------------------------------------------------


def _bbox_from_model_files(z: zipfile.ZipFile) -> dict | None:
    """Caixa envolvente lendo os .model com expat (SAX): não monta árvore, memória constante."""
    from xml.parsers import expat

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    count = [0]

    def start(name, attrs):
        if name.endswith("vertex"):
            for i, key in enumerate(("x", "y", "z")):
                v = float(attrs.get(key, 0))
                if v < lo[i]:
                    lo[i] = v
                if v > hi[i]:
                    hi[i] = v
            count[0] += 1

    for name in z.namelist():
        if not name.endswith(".model"):
            continue
        parser = expat.ParserCreate()
        parser.StartElementHandler = start
        with z.open(name) as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    if not count[0]:
        return None
    return {"x": round(hi[0] - lo[0], 2), "y": round(hi[1] - lo[1], 2), "z": round(hi[2] - lo[2], 2), "vertices": count[0]}


def read_project_3mf(source) -> dict:
    with _open_zip(source) as z:
        bbox = _bbox_from_model_files(z)
    if not bbox:
        raise ReadError("Nenhuma malha encontrada no 3MF.")
    return {"bbox_mm": bbox}


STL_STREAM_FROM_BYTES = 5 * 1024 * 1024


def _stl_stats_binary(fh, count: int) -> dict:
    """Lê um STL binário em blocos com numpy: caixa, volume assinado e área, sem montar a malha toda."""
    import numpy as np

    dtype = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    volume = 0.0
    area = 0.0
    left = count
    while left > 0:
        k = min(left, 200_000)
        buf = fh.read(k * 50)
        got = len(buf) // 50
        if got == 0:
            break
        tri = np.frombuffer(buf[: got * 50], dtype=dtype)["v"].astype(np.float64)
        lo = np.minimum(lo, tri.reshape(-1, 3).min(axis=0))
        hi = np.maximum(hi, tri.reshape(-1, 3).max(axis=0))
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        volume += float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0
        area += float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()) / 2.0
        left -= got
    return {"extents": (hi - lo).tolist(), "volume": abs(volume), "area": area, "faces": count, "watertight": None}


def read_stl(source, density_g_cm3: float = 1.24, fill_factor: float = 0.4) -> dict:
    """Dimensões, volume e área de um STL; peso estimado = volume × densidade × fator."""
    import struct

    fh = open(source, "rb") if isinstance(source, str) else source
    try:
        try:
            fh.seek(0)
        except (AttributeError, OSError):
            pass
        head = fh.read(84)
        stats = None
        if len(head) == 84:
            count = struct.unpack("<I", head[80:84])[0]
            size = None
            try:
                pos = fh.tell()
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(pos)
            except (AttributeError, OSError):
                pass
            # Grandes (> 5 MB) em fluxo, com memória constante; pequenos pelo trimesh, que também diz se é fechado.
            if size is not None and size == 84 + count * 50 and size > STL_STREAM_FROM_BYTES:
                stats = _stl_stats_binary(fh, count)
        if stats is None:
            # STL em texto (raro e pequeno): usa o trimesh
            import trimesh

            fh.seek(0)
            mesh = trimesh.load(io.BytesIO(fh.read()), file_type="stl")
            if isinstance(mesh, trimesh.Scene):
                mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))
            stats = {"extents": [float(v) for v in mesh.extents], "volume": abs(float(mesh.volume)),
                     "area": float(mesh.area), "faces": int(len(mesh.faces)), "watertight": bool(mesh.is_watertight)}
    finally:
        if isinstance(source, str):
            fh.close()
    extents = [round(float(v), 2) for v in stats["extents"]]
    volume_cm3 = stats["volume"] / 1000.0
    solid_g = volume_cm3 * density_g_cm3
    return {
        "bbox_mm": {"x": extents[0], "y": extents[1], "z": extents[2]},
        "volume_cm3": round(volume_cm3, 2),
        "area_cm2": round(stats["area"] / 100.0, 2),
        "watertight": stats["watertight"],
        "weight_solid_g": round(solid_g, 1),
        "weight_estimate_g": round(solid_g * fill_factor, 1),
        "fill_factor": fill_factor,
        "faces": int(stats["faces"]),
    }


def decimal_grams(value) -> Decimal:
    return Decimal(str(round(float(value or 0), 2)))
