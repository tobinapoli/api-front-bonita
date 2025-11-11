from django.urls import path
from bonita.views import login_api, iniciar_proyecto_api, revisar_pedidos_api

urlpatterns = [
    path("login/",   login_api,            name="bonita_login_api"),
    path("iniciar/", iniciar_proyecto_api, name="bonita_iniciar"),
    path("revisar/", revisar_pedidos_api,  name="bonita_revisar"),
]
