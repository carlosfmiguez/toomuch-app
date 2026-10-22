from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.catalog.models import Product
from apps.core.dashboard import shell
from apps.costing.models import Channel

from . import catalog as catalog_service
from .exports import build_export, export_filename, write_csv, write_xlsx


def _channel_from(request) -> Channel | None:
    channel_id = request.GET.get("canal")
    if channel_id:
        return Channel.objects.filter(pk=channel_id).first()
    return Channel.objects.filter(status=Channel.Status.ACTIVE).order_by("order").first()


def _only_ready(request) -> bool:
    return request.GET.get("todos") != "1"


@login_required
def catalog_index(request):
    channel = _channel_from(request)
    doc = catalog_service.build_catalog(channel, only_ready=_only_ready(request))
    return render(request, "publishing/catalog_index.html", {
        **shell(request, "Catálogo"),
        "doc": doc, "channels": Channel.objects.filter(status=Channel.Status.ACTIVE), "channel": channel,
        "only_ready": _only_ready(request),
        "products_total": Product.objects.filter(status__in=[Product.Status.ACTIVE, Product.Status.SEASONAL]).count(),
    })


@login_required
def catalog_html(request):
    channel = _channel_from(request)
    doc = catalog_service.build_catalog(channel, only_ready=_only_ready(request))
    return HttpResponse(catalog_service.render_catalog_html(doc, for_pdf=False))


@login_required
def catalog_pdf(request):
    channel = _channel_from(request)
    doc = catalog_service.build_catalog(channel, only_ready=_only_ready(request))
    pdf = catalog_service.render_catalog_pdf(doc)
    if request.GET.get("registrar") == "1":
        catalog_service.register_publications(doc, request.user)
    stamp = timezone.localdate().strftime("%Y-%m-%d")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="catalogo-toomuch-{stamp}.pdf"'
    return response


@login_required
def product_page_pdf(request, pk):
    """Só a página A4 de um produto (sem capa e sumário)."""
    product = get_object_or_404(Product, pk=pk)
    channel = _channel_from(request)
    doc = catalog_service.build_catalog(channel, only_ready=False)
    page = next((p for p in doc.pages if p.product.pk == product.pk), None) or catalog_service.build_product_page(product, channel)
    page.page_number = page.page_number or 3
    doc.pages = [page]
    doc.categories = [(product.category, page.page_number)]
    pdf = catalog_service.render_catalog_pdf(doc, only_products=True)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{product.sku_base}.pdf"'
    return response


@login_required
def export_marketplace(request, fmt):
    """fmt: ml ou shopee. ?formato=xlsx para planilha Excel; padrão CSV (;)."""
    if fmt not in ("ml", "shopee"):
        messages.error(request, "Formato de exportação desconhecido.")
        return redirect("publishing:catalog_index")
    channel = _channel_from(request)
    rows, columns = build_export(fmt, channel, only_ready=_only_ready(request), base_url=request.build_absolute_uri("/"))
    if not rows:
        messages.warning(request, "Nenhuma variante com preço nesse canal pronta para exportar (confira o checklist e os preços).")
        return redirect("publishing:catalog_index")
    if request.GET.get("formato") == "xlsx":
        data, content_type, ext = write_xlsx(rows, columns), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        data, content_type, ext = write_csv(rows, columns), "text/csv; charset=utf-8", "csv"
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{export_filename(fmt, ext)}"'
    return response
