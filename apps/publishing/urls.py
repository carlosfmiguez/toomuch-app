from django.urls import path

from . import views

app_name = "publishing"

urlpatterns = [
    path("", views.catalog_index, name="catalog_index"),
    path("html/", views.catalog_html, name="catalog_html"),
    path("pdf/", views.catalog_pdf, name="catalog_pdf"),
    path("produto/<int:pk>/pdf/", views.product_page_pdf, name="product_page_pdf"),
    path("exportar/<str:fmt>/", views.export_marketplace, name="export_marketplace"),
]
