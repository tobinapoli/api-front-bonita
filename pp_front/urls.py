from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/bonita/", permanent=False)),
    path("bonita/", include("bonita.urls")),          # paginas HTML
    path("api/bonita/", include("bonita.urls.api")),  # API
]
