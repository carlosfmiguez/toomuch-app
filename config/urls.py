from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Sistema Toomuch"
admin.site.site_title = "Sistema Toomuch"
admin.site.index_title = "Painel"

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

    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]
