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
# API: Login -> instancia proceso y ejecuta tarea "Login"
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
        cli.login()

        proc_id = cli.get_process_definition_id(
            settings.BONITA_PROCESS_NAME if hasattr(settings, "BONITA_PROCESS_NAME") else "ProjectPlanning",
            settings.BONITA_PROCESS_VERSION if hasattr(settings, "BONITA_PROCESS_VERSION") else "1.0",
        )
        if not proc_id:
            return JsonResponse({"ok": False, "error": "Proceso ProjectPlanning 1.0 no encontrado"}, status=500)

        # 1) Instanciar el caso
        inst = cli.instantiate_process(proc_id, {})
        case_id = (inst or {}).get("caseId") or (inst or {}).get("id")
        if not case_id:
            return JsonResponse({"ok": False, "error": "No se obtuvo caseId"}, status=500)
        case_id = str(case_id)

        # 2) Esperar la tarea "Login", asignarla y ejecutarla con el contract
        task = cli.wait_ready_task_in_case(case_id, task_name="Login", timeout_sec=30)
        if not task:
            return JsonResponse({"ok": False, "error": "La tarea Login no quedó ready"}, status=500)

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse({"ok": False, "error": "Assignee Bonita no encontrado"}, status=500)

        cli.assign_task(task["id"], user_id)
        cli.execute_task(task["id"], {"apiUser": api_user, "apiPass": api_pass})

        # 3) Esperar resultado de la tarea Login (completada o fallida)
        outcome = cli.wait_task_outcome(case_id, "Login", timeout_sec=30, interval_sec=0.6)
        if outcome.get("state") == "failed":
            return JsonResponse(
                {
                    "ok": False,
                    "error": "La tarea Login falló en Bonita (ver Conectores de salida).",
                    "detail": "Revisá cloudApiBase, payload y mapeos del conector; desactivá 'fallar en 4xx/5xx'.",
                },
                status=401,
            )

        # 4) Confirmar que apareció la siguiente tarea
        nxt = cli.wait_ready_task_in_case(
            case_id, task_name="Definir plan de trabajo y economico", timeout_sec=30
        )
        if not nxt:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Login en Bonita no habilitó la siguiente tarea.",
                    "detail": "Si el Login no falló, puede ser demora del conector: ver variables jwtAccess/jwtRefresh/statusCode.",
                },
                status=401,
            )

        return JsonResponse({"ok": True, "caseId": case_id}, status=200)

    except Exception as e:
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

        # 1) Si no vino caseId, instanciamos y pasamos por Login aquí
        if case_id_in:
            case_id = case_id_in
        else:
            proc_id = cli.get_process_definition_id(
                settings.BONITA_PROCESS_NAME if hasattr(settings, "BONITA_PROCESS_NAME") else "ProjectPlanning",
                settings.BONITA_PROCESS_VERSION if hasattr(settings, "BONITA_PROCESS_VERSION") else "1.0",
            )
            if not proc_id:
                return JsonResponse({"error": "No se encontró ProjectPlanning 1.0"}, status=500)

            inst = cli.instantiate_process(proc_id, {})
            case_id = str((inst or {}).get("caseId") or (inst or {}).get("id") or "")
            if not case_id:
                return JsonResponse({"error": "No se obtuvo caseId"}, status=500)

            api_user = str(data.get("apiUser") or data.get("username") or "").strip()
            api_pass = str(data.get("apiPass") or data.get("password") or "").strip()
            login_task = cli.wait_ready_task_in_case(case_id, task_name="Login", timeout_sec=30)
            if login_task:
                cli.assign_task(login_task["id"], user_id)
                payload_login: Dict[str, Any] = {"apiUser": api_user, "apiPass": api_pass} if (api_user and api_pass) else {}
                cli.execute_task(login_task["id"], payload_login)
                outcome = cli.wait_task_outcome(case_id, "Login", timeout_sec=30, interval_sec=0.6)
                if outcome.get("state") == "failed":
                    return JsonResponse(
                        {"ok": False, "error": "La tarea Login falló (ver conector)."}, status=401
                    )

        # 2) Ejecutar "Definir plan de trabajo y economico"
        task = cli.wait_ready_task_in_case(case_id, task_name="Definir plan de trabajo y economico", timeout_sec=30)
        if not task:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "No apareció la tarea 'Definir plan de trabajo y economico'. Revisá si 'Login' quedó fallida.",
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
