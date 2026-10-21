from django.urls import path

from . import views_public

app_name = "public"

urlpatterns = [
    path("", views_public.home, name="home"),
    path("c/<str:code>/", views_public.category, name="category"),
    path("p/<str:sku_base>/", views_public.product, name="product"),
]
