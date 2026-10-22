"""
Tela de importação em lote: sobe um ZIP (ou aponta uma pasta no servidor), revisa a
lista sugerida (nome, categoria, SKU, origem) e confirma. Tudo fica numa pasta
temporária do servidor entre as duas etapas, identificada por um token na sessão.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core.dashboard import shell

from . import importer
from .models import Category, Product

IMPORT_ROOT = Path(getattr(settings, "IMPORT_TMP_DIR", Path(tempfile.gettempdir()) / "toomuch-import"))
MAX_ZIP_MB = 1024
QUEUE_FILE = "_fila.json"


def _workdir(token: str) -> Path:
    path = IMPORT_ROOT / token
    if not path.is_dir() or ".." in token or "/" in token:
        raise FileNotFoundError(token)
    return path


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> int:
    n = 0
    for member in zf.infolist():
        name = member.filename
        if member.is_dir() or name.startswith("__MACOSX") or Path(name).name.startswith("."):
            continue
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest.resolve())):
            continue  # zip-slip
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        n += 1
    return n


def _flatten_single_root(dest: Path) -> None:
    """Se o ZIP tinha uma única pasta raiz, sobe o conteúdo um nível."""
    entries = [e for e in dest.iterdir() if not e.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in list(inner.iterdir()):
            shutil.move(str(child), str(dest / child.name))
        inner.rmdir()


@login_required
@require_http_methods(["GET", "POST"])
def import_upload(request):
    if request.method == "POST":
        upload = request.FILES.get("zip")
        folder = (request.POST.get("folder") or "").strip()
        token = uuid.uuid4().hex
        dest = IMPORT_ROOT / token
        dest.mkdir(parents=True, exist_ok=True)
        try:
            if upload:
                if upload.size > MAX_ZIP_MB * 1024 * 1024:
                    raise ValueError(f"ZIP maior que {MAX_ZIP_MB} MB.")
                with zipfile.ZipFile(upload) as zf:
                    n = _safe_extract(zf, dest)
                _flatten_single_root(dest)
                if n == 0:
                    raise ValueError("O ZIP não tem arquivos.")
            elif folder:
                src = Path(folder)
                if not src.is_dir():
                    raise ValueError("Pasta não encontrada no servidor.")
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                raise ValueError("Envie um ZIP ou informe uma pasta.")
            products = importer.scan(dest)
            if not products:
                raise ValueError("Nenhum arquivo 3D ou foto reconhecido (STL, 3MF, STEP, F3D, JPG, PNG, WebP).")
        except (ValueError, zipfile.BadZipFile) as exc:
            shutil.rmtree(dest, ignore_errors=True)
            messages.error(request, str(exc))
            return redirect("catalog:import_upload")
        request.session["import_token"] = token
        return redirect("catalog:import_review")
    return render(request, "catalog/import_upload.html", {**shell(request, "Importar em lote"), "max_mb": MAX_ZIP_MB})


@login_required
@require_http_methods(["GET", "POST"])
def import_review(request):
    token = request.session.get("import_token")
    try:
        workdir = _workdir(token or "")
    except FileNotFoundError:
        messages.error(request, "Nenhuma importação em andamento. Envie o ZIP de novo.")
        return redirect("catalog:import_upload")
    products = importer.scan(workdir)
    if request.method == "POST":
        rows = []
        for i, sp in enumerate(products):
            if request.POST.get(f"import-{i}") != "on":
                continue
            rows.append({
                "Pasta": sp.folder, "Nome": request.POST.get(f"name-{i}", sp.name).strip() or sp.name,
                "Categoria (código)": request.POST.get(f"cat-{i}", sp.category_code),
                "SKU-base": request.POST.get(f"sku-{i}", sp.sku_base).strip().upper() or sp.sku_base,
                "Origem": request.POST.get(f"origin-{i}", "own"), "Autor/licença": request.POST.get(f"author-{i}", "").strip(),
                "Importar?": "sim",
            })
        # Importa um produto por requisição (tela de progresso): nenhuma requisição longa ou pesada.
        (workdir / QUEUE_FILE).write_text(json.dumps({"rows": rows, "done": 0, "report": {}}), encoding="utf-8")
        return redirect("catalog:import_progress")
    existing = set(Product.objects.values_list("sku_base", flat=True))
    return render(request, "catalog/import_review.html", {
        **shell(request, "Revisar importação"),
        "products": [{"sp": sp, "i": i, "exists": sp.sku_base in existing} for i, sp in enumerate(products)],
        "categories": Category.objects.all(), "origins": Product.Origin.choices,
    })


@login_required
def import_progress(request):
    token = request.session.get("import_token")
    try:
        workdir = _workdir(token or "")
        queue = json.loads((workdir / QUEUE_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        messages.error(request, "Nenhuma importação em andamento. Envie o ZIP de novo.")
        return redirect("catalog:import_upload")
    return render(request, "catalog/import_progress.html", {
        **shell(request, "Importando"), "total": len(queue["rows"]), "done": queue["done"],
    })


@login_required
@require_http_methods(["POST"])
def import_step(request):
    """Importa o próximo produto da fila e devolve o andamento em JSON."""
    token = request.session.get("import_token")
    try:
        workdir = _workdir(token or "")
        qpath = workdir / QUEUE_FILE
        queue = json.loads(qpath.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return JsonResponse({"error": "Nenhuma importação em andamento."}, status=410)
    rows, done = queue["rows"], queue["done"]
    agg = importer.ImportReport(**{k: v for k, v in queue.get("report", {}).items()}) if queue.get("report") else importer.ImportReport()
    current = rows[done]["Nome"] if done < len(rows) else ""
    if done < len(rows):
        before = len(agg.errors)
        importer.apply_rows(workdir, [rows[done]], user=request.user, report=agg)
        done += 1
        queue.update(done=done, report=agg.__dict__)
        qpath.write_text(json.dumps(queue), encoding="utf-8")
        new_errors = agg.errors[before:]
    else:
        new_errors = []
    finished = done >= len(rows)
    payload = {"done": done, "total": len(rows), "current": current, "errors": new_errors, "finished": finished}
    if finished:
        shutil.rmtree(workdir, ignore_errors=True)
        request.session.pop("import_token", None)
        messages.success(request, (
            f"Importação concluída: {agg.created_products} produto(s) criado(s), {agg.reused_products} reaproveitado(s), "
            f"{agg.files_added} arquivo(s) novo(s), {agg.files_skipped} já existiam."
        ))
        for err in agg.errors:
            messages.warning(request, err)
        payload["redirect"] = reverse("catalog:product_list") + "?status=draft"
    return JsonResponse(payload)


def _apply_rows(workdir: Path, rows: list[dict], user) -> importer.ImportReport:
    """Aplica as linhas revisadas na tela (mesmo formato da planilha)."""
    from openpyxl import Workbook

    tmp = workdir / "_mapeamento.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Mapeamento"
    ws.append(importer.MAPPING_COLUMNS)
    for r in rows:
        ws.append([r.get(c, "") for c in importer.MAPPING_COLUMNS])
    wb.save(tmp)
    return importer.apply(workdir, tmp, user=user)
