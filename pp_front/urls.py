from django.contrib import admin
from django.urls import path, include
urlpatterns = [
    path("admin/", admin.site.urls),
    path("bonita/", include("bonita.urls")),        # páginas
    path("api/bonita/", include("bonita.urls.api")) # endpoints
]
