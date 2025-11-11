from django.urls import path
from bonita.views import nuevo_proyecto_page, login_page, revisar_pedidos_page

urlpatterns = [
    path("nuevo/", nuevo_proyecto_page, name="bonita_nuevo"),
    path("revisar/", revisar_pedidos_page, name="bonita_revisar_page"),
    path("login/", login_page, name="bonita_login"),
]
