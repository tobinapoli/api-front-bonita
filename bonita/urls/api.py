from django.urls import path
from bonita.views import (
    login_api,
    iniciar_proyecto_api,
    revisar_proyectos_api,
    registrar_pedido_api,
    elegir_proyecto_api,
    revisar_pedidos_proyecto_api,
    finalizar_revision_pedidos_api,  # <-- NUEVO
)

urlpatterns = [
    path("login/",           login_api,                    name="bonita_login_api"),
    path("iniciar/",         iniciar_proyecto_api,         name="bonita_iniciar"),
    path("revisar/",         revisar_proyectos_api,        name="bonita_revisar"),
    path("pedido/",          registrar_pedido_api,         name="bonita_pedido_api"),
    path("elegir-proyecto/", elegir_proyecto_api,          name="bonita_elegir_proyecto"),
    path("revisar-pedidos/", revisar_pedidos_proyecto_api, name="bonita_revisar_pedidos"),
    path(
        "revisar-pedidos/finalizar/",
        finalizar_revision_pedidos_api,
        name="bonita_finalizar_revision_pedidos",
    ),
]
