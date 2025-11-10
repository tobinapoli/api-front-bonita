# bonita/views.py
from __future__ import annotations
import json
from typing import Any, Dict
from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from .bonita_client import BonitaClient
from .validators import validate_iniciar_payload

# --------------------------- Pages (HTML) ---------------------------
def login_page(req: HttpRequest):
    return render(req, "bonita/login.html")

def nuevo_proyecto_page(req: HttpRequest):
    return render(req, "bonita/nuevo.html")

# --------------------------- Helpers ---------------------------
def _json(req: HttpRequest) -> Dict[str, Any]:
    try:
        return json.loads(req.body.decode("utf-8")) if req.body else {}
    except Exception:
        return {}

# --------------------------- API: Login -> instancia proceso con conector de entrada ---------------------------
@csrf_exempt
def login_api(req: HttpRequest):
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    body = _json(req)
    api_user = str(body.get("user") or body.get("username") or "").strip()
    api_pass = str(body.get("pass") or body.get("password") or "").strip()
    if not api_user or not api_pass:
        return JsonResponse({"ok": False, "error": "Faltan credenciales"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        proc_id = cli.get_process_definition_id(
            getattr(settings, "BONITA_PROCESS_NAME", "ProjectPlanning"),
            getattr(settings, "BONITA_PROCESS_VERSION", "1.0"),
        )
        if not proc_id:
            return JsonResponse({"ok": False, "error": "Proceso ProjectPlanning 1.0 no encontrado"}, status=500)

        # Instancio el caso pasando user/pass al CONTRATO de inicio (ejecuta el conector de ENTRADA “login”)
        inst = cli.instantiate_process(proc_id, {"apiUser": api_user, "apiPass": api_pass})
        case_id = (inst or {}).get("caseId") or (inst or {}).get("id")
        if not case_id:
            return JsonResponse({"ok": False, "error": "No se obtuvo caseId"}, status=500)
        case_id = str(case_id)

        # Verifico que apareció la siguiente tarea; si no, el login del conector falló y el gateway cortó el flujo
        nxt = cli.wait_ready_task_in_case(case_id, task_name="Definir plan de trabajo y economico", timeout_sec=30)
        if not nxt:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Login fallido. Verifique credenciales.",
                    "detail": "El conector de entrada del proceso Bonita no pudo autenticarse contra la API externa.",
                },
                status=401,
            )

        return JsonResponse({"ok": True, "caseId": case_id}, status=200)

    except Exception as e:
        return JsonResponse({"ok": False, "error": "Fallo integración Bonita", "detail": str(e)}, status=500)

# --------------------------- API: Iniciar/continuar proyecto ---------------------------
@csrf_exempt
def iniciar_proyecto_api(req: HttpRequest):
    """
    Recibe los datos del formulario externo y EMPUJA el contrato de la tarea
    “Definir plan de trabajo y economico”. La creación real del Proyecto en API
    NO se hace acá; la hace Bonita con un CONECTOR DE SALIDA (ON_FINISH) usando el JWT 'access'.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)

    # Validación del payload “de negocio” (fechas, etapas, etc.) para tu propio front si querés mantenerla
    errs = validate_iniciar_payload(data)
    if errs:
        return JsonResponse({"ok": False, "errors": errs}, status=400)

    case_id_in = str(data.get("caseId") or "").strip()

    try:
        cli = BonitaClient()
        cli.login()

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse({"error": "Usuario Bonita no encontrado", "detail": assignee_username}, status=500)

        # Caso: venís del login.html (con caseId). Si no, instancio y el conector de entrada hará el login.
        if case_id_in:
            case_id = case_id_in
        else:
            proc_id = cli.get_process_definition_id(
                getattr(settings, "BONITA_PROCESS_NAME", "ProjectPlanning"),
                getattr(settings, "BONITA_PROCESS_VERSION", "1.0"),
            )
            if not proc_id:
                return JsonResponse({"error": "No se encontró ProjectPlanning 1.0"}, status=500)

            api_user = str(data.get("apiUser") or data.get("username") or "").strip()
            api_pass = str(data.get("apiPass") or data.get("password") or "").strip()

            inst = cli.instantiate_process(proc_id, {"apiUser": api_user, "apiPass": api_pass})
            case_id = str((inst or {}).get("caseId") or (inst or {}).get("id") or "")
            if not case_id:
                return JsonResponse({"error": "No se obtuvo caseId"}, status=500)

        # Esperamos la tarea “Definir plan de trabajo y economico”
        task = cli.wait_ready_task_in_case(case_id, task_name="Definir plan de trabajo y economico", timeout_sec=30)
        if not task:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "No apareció la tarea 'Definir plan de trabajo y economico'.",
                    "detail": "Si es un caso nuevo, el login (conector de entrada) pudo haber fallado.",
                },
                status=409,
            )

        # Asignamos y ejecutamos la tarea con el contrato.
        # IMPORTANTE: ahora incluimos 'descripcion' además de 'nombre';
        # 'planTrabajo' y 'planEconomico' siguen viajando como JSON string si los necesitás adentro del proceso.
        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "nombre": str(data.get("nombre") or ""),
            "descripcion": str(data.get("descripcion") or ""),
            "planTrabajo": json.dumps(data.get("planTrabajo") or {}, ensure_ascii=False),
            "planEconomico": json.dumps(data.get("planEconomico") or {}, ensure_ascii=False),
        }
        cli.execute_task(task["id"], payload_contrato)

        return JsonResponse({"ok": True, "caseId": case_id, "avanzado": True}, status=201)

    except Exception as e:
        return JsonResponse({"error": "Error integrando con Bonita", "detail": str(e)}, status=500)
