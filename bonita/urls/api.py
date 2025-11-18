from django.urls import path
from bonita.views import (
    login_api,
    iniciar_proyecto_api,
    revisar_proyectos_api,
    registrar_pedido_api,
    elegir_proyecto_api,
    revisar_pedidos_proyecto_api,
    finalizar_revision_pedidos_api,
    registrar_compromiso_api,
    next_step_api,
    obtener_proyectos_en_ejecucion_api,
    enviar_observaciones_consejo_api,
    ver_observaciones_proyecto_api,
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
    path("compromiso/",      registrar_compromiso_api,     name="bonita_compromiso_api"),
    path("next-step/",       next_step_api,                name="bonita_next_step"),
    
    # Consejo Directivo
    path("consejo/proyectos/", obtener_proyectos_en_ejecucion_api, name="bonita_consejo_proyectos"),
    path("consejo/proyectos/<int:proyecto_id>/observaciones/", ver_observaciones_proyecto_api, name="bonita_ver_observaciones"),
    path("consejo/observaciones/", enviar_observaciones_consejo_api, name="bonita_consejo_observaciones"),
]
