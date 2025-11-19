# api/bonita/urls.py  (endpoints API)

from django.urls import path
from bonita.views import (
    consejo_evaluar_page,
    enviar_evaluacion_consejo_api,
    login_api,
    iniciar_proyecto_api,
    obtener_datos_evaluacion_api,
    responder_observacion_bonita_api,
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
    revisar_compromisos_api,
    evaluar_propuestas_api,
    resumen_proyecto_api,
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
    path(
        "consejo/proyectos/<int:proyecto_id>/observaciones/",
        ver_observaciones_proyecto_api,
        name="bonita_ver_observaciones",
    ),
    path("consejo/observaciones/", enviar_observaciones_consejo_api, name="bonita_consejo_observaciones"),

    # Evaluar propuestas / monitoreo
    path("revisar-compromisos/", revisar_compromisos_api,  name="bonita_revisar_compromisos"),
    path("evaluar-propuestas/",  evaluar_propuestas_api,   name="bonita_evaluar_propuestas"),
    path("resumen-proyecto/",    resumen_proyecto_api,     name="bonita_resumen_proyecto"),
    path("responder-observacion/", responder_observacion_bonita_api, name="bonita_responder_obs"),

    path("consejo/evaluar/", enviar_evaluacion_consejo_api, name="bonita_consejo_evaluar_api"),
    path("consejo/datos-evaluacion/", obtener_datos_evaluacion_api, name="bonita_datos_evaluacion"),
]
