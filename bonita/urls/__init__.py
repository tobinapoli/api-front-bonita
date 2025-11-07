# bonita/urls/__init__.py
from django.urls import path
from bonita.views import nuevo_proyecto_page, login_page
urlpatterns = [
    path("nuevo/", nuevo_proyecto_page, name="bonita_nuevo"),
    path("login/", login_page, name="bonita_login"),
]
