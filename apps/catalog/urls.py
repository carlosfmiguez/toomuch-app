from django.urls import path

from . import views, views_import

app_name = "catalog"

urlpatterns = [
    path("", views.product_list, name="product_list"),
    path("novo/", views.product_create, name="product_create"),
    path("<int:pk>/", views.product_detail, name="product_detail"),
    path("<int:pk>/dados/", views.product_edit, name="product_edit"),
    path("<int:pk>/status/", views.product_status, name="product_status"),
    path("<int:pk>/pecas/nova/", views.part_add, name="part_add"),
    path("<int:pk>/pecas/usar/", views.part_reuse, name="part_reuse"),
    path("<int:pk>/pecas/do-arquivo/", views.part_from_file, name="part_from_file"),
    path("<int:pk>/pecas/<int:part_pk>/desvincular/", views.part_unlink, name="part_unlink"),
    path("<int:pk>/variantes/nova/", views.variant_add, name="variant_add"),
    path("<int:pk>/fotos/", views.photo_upload, name="photo_upload"),
    path("peca/<int:pk>/salvar/", views.part_update, name="part_update"),
    path("peca/<int:pk>/remover/", views.part_delete, name="part_delete"),
    path("peca/<int:pk>/3mf/", views.part_upload, name="part_upload"),
    path("arquivo/<int:pk>/remover/", views.asset_delete, name="asset_delete"),
    path("foto/<int:pk>/principal/", views.photo_main, name="photo_main"),
    path("variante/<int:pk>/", views.variant_edit, name="variant_edit"),
    path("variante/<int:pk>/remover/", views.variant_delete, name="variant_delete"),
    path("variante/<int:pk>/recalcular/", views.variant_recalculate, name="variant_recalculate"),
    path("variante/<int:pk>/precos/", views.variant_prices, name="variant_prices"),
    path("importar/", views_import.import_upload, name="import_upload"),
    path("importar/revisar/", views_import.import_review, name="import_review"),
    path("importar/andamento/", views_import.import_progress, name="import_progress"),
    path("importar/passo/", views_import.import_step, name="import_step"),
]
