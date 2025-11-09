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


# ---------------------------
# Helpers
# ---------------------------
def _json(req: HttpRequest) -> Dict[str, Any]:
    try:
        return json.loads(req.body.decode("utf-8")) if req.body else {}
    except Exception:
        return {}


# ---------------------------
# Pages (HTML)
# ---------------------------
def login_page(req: HttpRequest):
    return render(req, "bonita/login.html")


def nuevo_proyecto_page(req: HttpRequest):
    return render(req, "bonita/nuevo.html")


# ---------------------------
# API: Login -> instancia proceso CON CONECTOR DE ENTRADA
# ---------------------------
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
        cli.login()  # Login del cliente Python a Bonita (necesario)

        proc_id = cli.get_process_definition_id(
            settings.BONITA_PROCESS_NAME if hasattr(settings, "BONITA_PROCESS_NAME") else "ProjectPlanning",
            settings.BONITA_PROCESS_VERSION if hasattr(settings, "BONITA_PROCESS_VERSION") else "1.0",
        )
        if not proc_id:
            return JsonResponse({"ok": False, "error": "Proceso ProjectPlanning 1.0 no encontrado"}, status=500)

        # 1) Instanciar el caso PASANDO LAS CREDENCIALES AL CONTRATO
        # El contrato de Bonita (paso 1) debe llamarse 'apiUser' y 'apiPass'
        payload = {
            "apiUser": api_user,
            "apiPass": api_pass
        }
        inst = cli.instantiate_process(proc_id, payload)
        case_id = (inst or {}).get("caseId") or (inst or {}).get("id")
        if not case_id:
            return JsonResponse({"ok": False, "error": "No se obtuvo caseId"}, status=500)
        case_id = str(case_id)

        # 2) YA NO SE ESPERA, ASIGNA NI EJECUTA LA TAREA "Login".
        # El conector de entrada se ejecutó DURANTE la instanciación.

        # 3) Confirmar que apareció la siguiente tarea
        # Si esto falla, significa que el CONECTOR DE ENTRADA falló (malas credenciales, etc.)
        # y el Gateway en Bonita desvió el flujo a un fin de error.
        nxt = cli.wait_ready_task_in_case(
            case_id, task_name="Definir plan de trabajo y economico", timeout_sec=30
        )
        
        if not nxt:
            # El conector de entrada falló. La API externa rechazó las credenciales.
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Login fallido. Verifique credenciales.",
                    "detail": "El conector de entrada del proceso Bonita no pudo autenticarse contra la API externa.",
                },
                status=401, # 401 Unauthorized es apropiado
            )

        # ¡Éxito! El conector funcionó y la siguiente tarea está lista.
        return JsonResponse({"ok": True, "caseId": case_id}, status=200)

    except Exception as e:
        # Esto captura errores de conexión con Bonita, timeouts, etc.
        return JsonResponse({"ok": False, "error": "Fallo integración Bonita", "detail": str(e)}, status=500)


# ---------------------------
# API: Iniciar/continuar proyecto
# ---------------------------
@csrf_exempt
def iniciar_proyecto_api(req: HttpRequest):
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id_in = str(data.get("caseId") or "").strip()

    errs = validate_iniciar_payload(data)
    if errs:
        return JsonResponse({"ok": False, "errors": errs}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse({"error": "Usuario Bonita no encontrado", "detail": assignee_username}, status=500)

        # 1) Si no vino caseId, instanciamos y pasamos login en el conector
        if case_id_in:
            case_id = case_id_in
        else:
            # Esta lógica se usa si el usuario va directo a 'nuevo.html' sin
            # pasar por 'login.html'. Asumimos que el payload 'data'
            # también trae 'apiUser' y 'apiPass'.
            proc_id = cli.get_process_definition_id(
                settings.BONITA_PROCESS_NAME if hasattr(settings, "BONITA_PROCESS_NAME") else "ProjectPlanning",
                settings.BONITA_PROCESS_VERSION if hasattr(settings, "BONITA_PROCESS_VERSION") else "1.0",
            )
            if not proc_id:
                return JsonResponse({"error": "No se encontró ProjectPlanning 1.0"}, status=500)

            api_user = str(data.get("apiUser") or data.get("username") or "").strip()
            api_pass = str(data.get("apiPass") or data.get("password") or "").strip()
            
            # Instanciamos pasando las credenciales al conector de entrada
            payload_instanciacion = {
                "apiUser": api_user,
                "apiPass": api_pass
            }
            inst = cli.instantiate_process(proc_id, payload_instanciacion)
            case_id = str((inst or {}).get("caseId") or (inst or {}).get("id") or "")
            if not case_id:
                return JsonResponse({"error": "No se obtuvo caseId"}, status=500)

            # --- TODO EL BLOQUE DE "wait_ready_task_in_case(..., task_name="Login")" SE ELIMINA ---

        # 2) Ejecutar "Definir plan de trabajo y economico"
        # Usamos este 'wait' para verificar que el caso (nuevo o existente)
        # está en esta tarea. Si es un caso nuevo, también confirma
        # que el conector de entrada de login funcionó.
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

        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "nombre": str(data.get("nombre") or ""),
            "planTrabajo": json.dumps(data.get("planTrabajo") or {}, ensure_ascii=False),
            "planEconomico": json.dumps(data.get("planEconomico") or {}, ensure_ascii=False),
        }
        cli.execute_task(task["id"], payload_contrato)

        return JsonResponse({"ok": True, "caseId": case_id, "avanzado": True}, status=201)

    except Exception as e:
        return JsonResponse({"error": "Error integrando con Bonita", "detail": str(e)}, status=500)