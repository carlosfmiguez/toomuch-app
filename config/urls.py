from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Sistema Toomuch"
admin.site.site_title = "Sistema Toomuch"
admin.site.index_title = "Painel"

handler400 = "apps.core.views.bad_request"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.connector.urls")),
    path("", include("apps.core.urls")),
    path("produtos/", include("apps.catalog.urls")),
    path("custo/", include("apps.costing.urls")),
    path("catalogo/", include("apps.publishing.urls")),
    path("producao/", include("apps.production.urls")),
    path("loja/", include("apps.publishing.urls_public")),
    path("integracoes/tiny/", include("apps.integrations.tiny.urls")),
]

# Arquivos enviados: servidos pelo Django quando ficam em disco (volume); com bucket S3/R2 o storage gera as URLs.
if settings.STORAGES["default"]["BACKEND"].endswith("FileSystemStorage"):
    from django.views.static import serve

    from django.urls import re_path

    def serve_media(request, path):
        # Nomes de upload não se repetem (o storage acrescenta sufixo), então o navegador pode guardar por
        # 30 dias: sem isso, cada lista com 200 miniaturas ocupava todos os workers do servidor.
        response = serve(request, path, document_root=settings.MEDIA_ROOT)
        if response.status_code in (200, 304):
            response["Cache-Control"] = "public, max-age=2592000"
        return response

    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve_media)]
