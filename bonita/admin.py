from django.contrib import admin
from .models import ProyectoMonitoreo, SesionBonita


@admin.register(ProyectoMonitoreo)
class ProyectoMonitoreoAdmin(admin.ModelAdmin):
    list_display = ('proyecto_id', 'nombre', 'creado_en', 'actualizado_en')
    search_fields = ('nombre', 'descripcion')
    readonly_fields = ('creado_en', 'actualizado_en')


@admin.register(SesionBonita)
class SesionBonitaAdmin(admin.ModelAdmin):
    list_display = ('api_username', 'case_id', 'proceso', 'creado_en', 'actualizado_en')
    search_fields = ('api_username', 'case_id', 'proceso')
    readonly_fields = ('creado_en', 'actualizado_en')
    list_filter = ('proceso', 'creado_en')
