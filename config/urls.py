from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Sistema Toomuch"
admin.site.site_title = "Sistema Toomuch"
admin.site.index_title = "Back-office"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
    path("produtos/", include("apps.catalog.urls")),
    path("custo/", include("apps.costing.urls")),
    path("catalogo/", include("apps.publishing.urls")),
    path("producao/", include("apps.production.urls")),
    path("loja/", include("apps.publishing.urls_public")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
