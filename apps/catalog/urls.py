from django.urls import path

from . import views, views_import

app_name = "catalog"

urlpatterns = [
    path("", views.product_list, name="product_list"),
    path("<int:pk>/", views.product_detail, name="product_detail"),
    path("variante/<int:pk>/recalcular/", views.variant_recalculate, name="variant_recalculate"),
    path("importar/", views_import.import_upload, name="import_upload"),
    path("importar/revisar/", views_import.import_review, name="import_review"),
]
