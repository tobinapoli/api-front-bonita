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


class SesionBonita(models.Model):
    """
    Guarda la relación entre un usuario de la API y su caso activo en Bonita.
    Esto permite retomar el flujo cuando el usuario vuelve a loguearse.
    """
    api_username = models.CharField(max_length=255, unique=True, db_index=True)
    case_id = models.CharField(max_length=100)
    proceso = models.CharField(max_length=100)  # 'ProjectPlanning' o 'Consejo Directivo'
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.api_username} → caso {self.case_id}"

    class Meta:
        verbose_name = "Sesión Bonita"
        verbose_name_plural = "Sesiones Bonita"
