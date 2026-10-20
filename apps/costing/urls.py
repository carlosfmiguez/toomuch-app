from django.urls import path

from . import views

app_name = "costing"

urlpatterns = [
    path("calculadora/", views.calculator, name="calculator"),
    path("lista-de-preco/", views.price_list, name="price_list"),
]
