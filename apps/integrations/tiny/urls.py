from django.urls import path

from . import views

app_name = "tiny"

urlpatterns = [
    path("", views.status, name="status"),
    path("conectar/", views.connect, name="connect"),
    path("callback/", views.callback, name="callback"),
    path("desconectar/", views.disconnect, name="disconnect"),
    path("enviar-produtos/", views.push_products, name="push_products"),
    path("importar-pedidos/", views.pull_orders, name="pull_orders"),
]
