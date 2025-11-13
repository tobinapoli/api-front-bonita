from django.urls import path
from bonita.views import (
    index_page,
    home_page,
    login_page,
    nuevo_proyecto_page,
    revisar_proyectos_page,
    pedido_page,
)

urlpatterns = [
    path("", index_page, name="bonita_index"),
    path("home/", home_page, name="bonita_home"),
    path("nuevo/", nuevo_proyecto_page, name="bonita_nuevo"),
    path("revisar/", revisar_proyectos_page, name="bonita_revisar_page"),
    path("pedido/", pedido_page, name="bonita_pedido"),
    path("login/", login_page, name="bonita_login"),
]
