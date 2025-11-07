# pp_front/urls.py
from django.contrib import admin              # ← FALTA ESTA LÍNEA
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("bonita/", include("bonita.urls")),          # páginas HTML
    path("api/bonita/", include("bonita.urls.api")),  # API
]
