from django.urls import path

from . import views

app_name = "production"

urlpatterns = [
    path("", views.board, name="board"),
    path("trabalho/<int:pk>/iniciar/", views.job_start, name="job_start"),
    path("trabalho/<int:pk>/concluir/", views.job_finish, name="job_finish"),
    path("trabalho/<int:pk>/falha/", views.job_fail, name="job_fail"),
    path("ordem/<int:pk>/avancar/", views.order_advance, name="order_advance"),
]
