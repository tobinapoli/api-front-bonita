# bonita/urls/api.py
from django.urls import path
from bonita.views import iniciar_proyecto_api
urlpatterns = [ path("iniciar/", iniciar_proyecto_api, name="bonita_iniciar") ]
