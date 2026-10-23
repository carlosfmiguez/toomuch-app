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

        plate_names: dict[int, str] = {}
        project_plates: dict[int, str] = {}
        if "Metadata/model_settings.config" in names:
            try:
                project_plates = _project_plates(z.read("Metadata/model_settings.config"))
                plate_names = {i: n for i, n in project_plates.items() if n}
            except Exception:  # noqa: BLE001 - nome da placa é opcional
                plate_names, project_plates = {}, {}

        thumbnail = None
        for cand in ("Metadata/plate_1.png", "Metadata/plate_1_small.png", "Metadata/top_1.png"):
            if cand in names:
                thumbnail = z.read(cand)
                break

        bbox = geometry = None
        try:
            bbox = _bbox_from_model_files(z)
            geometry = _geometry(z)
        except Exception:  # noqa: BLE001 - geometria é opcional
            pass

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
        # todas as placas do projeto (inclusive as que não foram fatiadas neste arquivo)
        "project_plates": [{"index": i, "name": n} for i, n in sorted(project_plates.items())],
        "filaments": filaments,
        "grams_total": grams_total,
        "time_s": time_s,
        "time_min": int(round(time_s / 60)),
        "object_count": sum(len(p.objects) for p in plates),
        "bbox_mm": bbox,
        "geometry": geometry,
        "thumbnail_png": thumbnail,
    }


def _project_plates(xml_bytes: bytes) -> dict[int, str]:
    """Placas do projeto no Bambu Studio e o nome de cada uma (model_settings.config → plater_id/plater_name)."""
    root = ET.fromstring(xml_bytes)
    out: dict[int, str] = {}
    for plate in root.findall("./plate"):
        meta = {m.get("key"): m.get("value") for m in plate.findall("./metadata")}
        try:
            idx = int(meta.get("plater_id") or 0)
        except ValueError:
            continue
        if idx:
            out[idx] = (meta.get("plater_name") or "").strip()
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


# --- geometria com transformações (escala/posição de cada item) e por placa -------------

GEOMETRY_VERSION = 2
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


def _parse_transform(text: str | None) -> tuple:
    try:
        vals = tuple(float(v) for v in (text or "").split())
    except ValueError:
        return _IDENTITY
    return vals if len(vals) == 12 else _IDENTITY


def _compose(inner: tuple, outer: tuple) -> tuple:
    """Transformação 3MF (linha × matriz 4x3): aplica `inner` e depois `outer`."""
    a, b = inner, outer
    out = []
    for r in range(3):
        for c in range(3):
            out.append(sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3)))
    for c in range(3):
        out.append(sum(a[9 + k] * b[k * 3 + c] for k in range(3)) + b[9 + c])
    return tuple(out)


def _apply_box(lo, hi, t: tuple):
    xs = []
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                xs.append((x * t[0] + y * t[3] + z * t[6] + t[9],
                           x * t[1] + y * t[4] + z * t[7] + t[10],
                           x * t[2] + y * t[5] + z * t[8] + t[11]))
    return [min(p[i] for p in xs) for i in range(3)], [max(p[i] for p in xs) for i in range(3)]


def _box_dict(lo, hi) -> dict:
    return {"x": round(hi[0] - lo[0], 1), "y": round(hi[1] - lo[1], 1), "z": round(hi[2] - lo[2], 1)}


def _geometry(z: zipfile.ZipFile) -> dict | None:
    """
    Caixa de cada item do build com as transformações aplicadas (escala de placa, rotação, componentes),
    e a caixa por placa do Bambu Studio. Mede a malha de cada objeto uma vez (expat, memória constante).
    """
    from xml.parsers import expat

    objects: dict[tuple, dict] = {}   # (arquivo, id) -> {"lo","hi","components":[(arquivo,id,T)]}
    build: list[tuple] = []           # (arquivo, id, T)
    for name in z.namelist():
        if not name.endswith(".model"):
            continue
        path = "/" + name
        state = {"obj": None, "in_build": False}

        def start(tag, attrs, path=path, state=state):
            tag = tag.rsplit(":", 1)[-1] if ":" in tag and not tag.startswith("p:") else tag
            if tag == "object":
                state["obj"] = objects.setdefault((path, attrs.get("id")), {"lo": None, "hi": None, "components": []})
            elif tag == "vertex" and state["obj"] is not None:
                o = state["obj"]
                v = (float(attrs.get("x", 0)), float(attrs.get("y", 0)), float(attrs.get("z", 0)))
                if o["lo"] is None:
                    o["lo"], o["hi"] = list(v), list(v)
                else:
                    for i in range(3):
                        if v[i] < o["lo"][i]:
                            o["lo"][i] = v[i]
                        elif v[i] > o["hi"][i]:
                            o["hi"][i] = v[i]
            elif tag == "component" and state["obj"] is not None:
                state["obj"]["components"].append(
                    (attrs.get("p:path") or path, attrs.get("objectid"), _parse_transform(attrs.get("transform"))))
            elif tag == "build":
                state["in_build"] = True
            elif tag == "item" and state["in_build"]:
                build.append((attrs.get("p:path") or path, attrs.get("objectid"), _parse_transform(attrs.get("transform"))))

        def end(tag, state=state):
            if tag.endswith("object"):
                state["obj"] = None
            elif tag.endswith("build"):
                state["in_build"] = False

        parser = expat.ParserCreate()
        parser.StartElementHandler = start
        parser.EndElementHandler = end
        with z.open(name) as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                parser.Parse(chunk, False)
            parser.Parse(b"", True)

    def box(key, t, depth=0):
        o = objects.get(key)
        if o is None or depth > 8:
            return None
        boxes = []
        if o["lo"] is not None:
            boxes.append(_apply_box(o["lo"], o["hi"], t))
        for cpath, cid, ct in o["components"]:
            b = box((cpath, cid), _compose(ct, t), depth + 1)
            if b:
                boxes.append(b)
        if not boxes:
            return None
        return [min(b[0][i] for b in boxes) for i in range(3)], [max(b[1][i] for b in boxes) for i in range(3)]

    items = []
    for bpath, bid, t in build:
        b = box((bpath, bid), t)
        if b:
            items.append({"objectid": bid, "lo": b[0], "hi": b[1]})
    if not items:
        return None

    plate_objs: dict[int, set] = {}
    if "Metadata/model_settings.config" in z.namelist():
        try:
            root = ET.fromstring(z.read("Metadata/model_settings.config"))
            for plate in root.findall("./plate"):
                meta = {m.get("key"): m.get("value") for m in plate.findall("./metadata")}
                ids = {m.get("value") for inst in plate.findall("./model_instance")
                       for m in inst.findall("./metadata") if m.get("key") == "object_id"}
                if meta.get("plater_id"):
                    plate_objs[int(meta["plater_id"])] = ids
        except (ET.ParseError, ValueError):
            plate_objs = {}

    def union(rows):
        return _box_dict([min(r["lo"][i] for r in rows) for i in range(3)], [max(r["hi"][i] for r in rows) for i in range(3)])

    plates = {}
    for idx, ids in plate_objs.items():
        rows = [it for it in items if it["objectid"] in ids]
        if rows:
            plates[str(idx)] = union(rows)
    return {"v": GEOMETRY_VERSION, "overall": union(items), "plates": plates, "items": len(items)}


def read_geometry(source) -> dict | None:
    with _open_zip(source) as z:
        return _geometry(z)


def read_project_3mf(source) -> dict:
    with _open_zip(source) as z:
        bbox = _bbox_from_model_files(z)
        try:
            geometry = _geometry(z)
        except Exception:  # noqa: BLE001 - geometria é sugestão; nunca derruba a leitura
            geometry = None
    if not bbox:
        raise ReadError("Nenhuma malha encontrada no 3MF.")
    return {"bbox_mm": bbox, "geometry": geometry}


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
