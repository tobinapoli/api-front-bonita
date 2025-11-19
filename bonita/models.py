from django.db import models


class ProyectoMonitoreo(models.Model):
    proyecto_id = models.IntegerField(primary_key=True)
    nombre = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True)
    plan_trabajo = models.JSONField(default=dict, blank=True)
    compromisos_aceptados = models.JSONField(default=list, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[{self.proyecto_id}] {self.nombre}"
