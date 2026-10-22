"""
Configuração do django-unfold: o shell do sistema (sidebar, busca global, menu do
usuário, dark mode) com a identidade da Toomuch. Importado pelo settings.
"""

from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

# Escalas OKLCH geradas a partir das cores da marca: base = cinza quente (matiz do
# chocolate), primary = oliva. Mesmos 11 passos do Tailwind.
BASE = {
    "50": "oklch(98.5% 0.005 55)", "100": "oklch(96.7% 0.005 55)", "200": "oklch(92.8% 0.007 55)",
    "300": "oklch(87.2% 0.009 55)", "400": "oklch(70.7% 0.016 55)", "500": "oklch(55.1% 0.022 55)",
    "600": "oklch(44.6% 0.018 55)", "700": "oklch(37.3% 0.015 55)", "800": "oklch(27.8% 0.011 55)",
    "900": "oklch(21% 0.008 55)", "950": "oklch(13% 0.005 55)",
}
PRIMARY = {
    "50": "oklch(98.5% 0.014 122)", "100": "oklch(96.7% 0.017 122)", "200": "oklch(92.8% 0.024 122)",
    "300": "oklch(87.2% 0.033 122)", "400": "oklch(70.7% 0.06 122)", "500": "oklch(55.1% 0.085 122)",
    "600": "oklch(44.6% 0.068 122)", "700": "oklch(37.3% 0.056 122)", "800": "oklch(27.8% 0.041 122)",
    "900": "oklch(21% 0.03 122)", "950": "oklch(13% 0.017 122)",
}


def badge_pendencias(request):
    """Número de pendências do catálogo, mostrado ao lado de 'Painel'."""
    try:
        from apps.core.dashboard import pending_count

        n = pending_count()
        return str(n) if n else None
    except Exception:  # noqa: BLE001 - nunca derrubar o menu
        return None


def badge_producao(request):
    try:
        from apps.production.models import PrintJob

        n = PrintJob.objects.filter(result=PrintJob.Result.PRINTING).count()
        return str(n) if n else None
    except Exception:  # noqa: BLE001
        return None


UNFOLD = {
    "SITE_TITLE": "Sistema Toomuch",
    "SITE_HEADER": "Toomuch",
    "SITE_SUBHEADER": "camada por camada",
    "SITE_SYMBOL": "layers",
    "SITE_URL": "/loja/",
    "SITE_ICON": lambda request: static("img/toomuch-icon.png"),
    "SITE_FAVICONS": [{"rel": "icon", "sizes": "32x32", "type": "image/png", "href": lambda request: static("img/favicon-32.png")}],
    "LOGIN": {"image": lambda request: static("img/login.jpg"), "redirect_after": lambda request: reverse_lazy("admin:index")},
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_BACK_BUTTON": True,
    "BORDER_RADIUS": "8px",
    "COLORS": {"base": BASE, "primary": PRIMARY},
    "STYLES": [lambda request: static("css/toomuch-admin.css")],
    "DASHBOARD_CALLBACK": "apps.core.dashboard.dashboard_callback",
    "COMMAND": {"search_models": True, "show_history": True},
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": _("Operação"),
                "items": [
                    {"title": _("Painel"), "icon": "space_dashboard", "link": reverse_lazy("admin:index"), "badge": "config.unfold.badge_pendencias"},
                    {"title": _("Produtos"), "icon": "deployed_code", "link": reverse_lazy("catalog:product_list")},
                    {"title": _("Produção"), "icon": "precision_manufacturing", "link": reverse_lazy("production:board"), "badge": "config.unfold.badge_producao"},
                    {"title": _("Catálogo"), "icon": "menu_book", "link": reverse_lazy("publishing:catalog_index")},
                    {"title": _("Importar em lote"), "icon": "upload_file", "link": reverse_lazy("catalog:import_upload")},
                    {"title": _("Loja pública"), "icon": "storefront", "link": reverse_lazy("public:home")},
                ],
            },
            {
                "title": _("Custo e preço"),
                "separator": True,
                "items": [
                    {"title": _("Lista de preço"), "icon": "sell", "link": reverse_lazy("costing:price_list")},
                    {"title": _("Calculadora rápida"), "icon": "calculate", "link": reverse_lazy("costing:calculator")},
                    {"title": _("Canais de venda"), "icon": "hub", "link": reverse_lazy("admin:costing_channel_changelist")},
                    {"title": _("Preços por canal"), "icon": "price_check", "link": reverse_lazy("admin:costing_pricebychannel_changelist")},
                    {"title": _("Parâmetros globais"), "icon": "tune", "link": reverse_lazy("admin:costing_globalparams_changelist")},
                    {"title": _("Impressoras"), "icon": "print", "link": reverse_lazy("admin:costing_printer_changelist")},
                ],
            },
            {
                "title": _("Estoque"),
                "separator": True,
                "items": [
                    {"title": _("Filamentos"), "icon": "gradient", "link": reverse_lazy("admin:inventory_filamentsku_changelist")},
                    {"title": _("Carretéis"), "icon": "data_saver_on", "link": reverse_lazy("admin:inventory_spool_changelist")},
                    {"title": _("Compras"), "icon": "shopping_cart", "link": reverse_lazy("admin:inventory_purchase_changelist")},
                    {"title": _("Componentes"), "icon": "category", "link": reverse_lazy("admin:catalog_component_changelist")},
                    {"title": _("Movimentos"), "icon": "swap_vert", "link": reverse_lazy("admin:inventory_stockmovement_changelist")},
                ],
            },
            {
                "title": _("Cadastros"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {"title": _("Produtos (admin)"), "icon": "inventory_2", "link": reverse_lazy("admin:catalog_product_changelist")},
                    {"title": _("Variantes"), "icon": "style", "link": reverse_lazy("admin:catalog_variant_changelist")},
                    {"title": _("Peças impressas"), "icon": "view_in_ar", "link": reverse_lazy("admin:catalog_part_changelist")},
                    {"title": _("Arquivos"), "icon": "folder_open", "link": reverse_lazy("admin:catalog_asset_changelist")},
                    {"title": _("Categorias"), "icon": "label", "link": reverse_lazy("admin:catalog_category_changelist")},
                    {"title": _("Cores"), "icon": "palette", "link": reverse_lazy("admin:catalog_color_changelist")},
                    {"title": _("Ordens de produção"), "icon": "assignment", "link": reverse_lazy("admin:production_productionorder_changelist")},
                    {"title": _("Publicações"), "icon": "publish", "link": reverse_lazy("admin:publishing_publication_changelist")},
                ],
            },
            {
                "title": _("Integrações"),
                "separator": True,
                "items": [
                    {"title": _("Tiny ERP"), "icon": "sync_alt", "link": reverse_lazy("tiny:status")},
                ],
            },
            {
                "title": _("Administração"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {"title": _("Usuários"), "icon": "group", "link": reverse_lazy("admin:auth_user_changelist"), "permission": lambda request: request.user.is_superuser},
                ],
            },
        ],
    },
}
