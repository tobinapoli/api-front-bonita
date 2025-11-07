import json
from typing import Any, Dict, Optional
from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from .bonita_client import BonitaClient
from .validators import validate_iniciar_payload

def _json(req: HttpRequest) -> Dict[str, Any]:
    try: return json.loads(req.body.decode("utf-8")) if req.body else {}
    except Exception: return {}

def nuevo_proyecto_page(req: HttpRequest):
    return render(req, "bonita/nuevo.html")

@csrf_exempt
def iniciar_proyecto_api(req: HttpRequest):
    if req.method != "POST":
        return JsonResponse({"error":"POST only"}, status=405)

    data = _json(req)
    errs = validate_iniciar_payload(data)
    if errs:
        return JsonResponse({"ok": False, "errors": errs}, status=400)

    try:
        cli = BonitaClient(); cli.login()
        proc_id = cli.get_process_definition_id("ProjectPlanning", "1.0")
        if not proc_id:
            return JsonResponse({"error":"No se encontró ProjectPlanning 1.0"}, status=500)

        inst = cli.instantiate_process(proc_id, {"nombre": data.get("nombre")})
        case_id = (inst or {}).get("caseId") or (inst or {}).get("id") or "UNKNOWN"

        task = cli.wait_ready_task_in_case(str(case_id), task_name="Definir plan de trabajo y economico")
        avanzado = False
        if task:
            assignee = settings.BONITA_ASSIGNEE
            user_id = cli.get_user_id_by_username(assignee)
            if not user_id:
                return JsonResponse({"error":"Usuario Bonita no encontrado", "detail": assignee}, status=500)

            cli.assign_task(task["id"], user_id)
            payload = {
                "nombre": str(data.get("nombre") or ""),
                "planTrabajo": json.dumps(data.get("planTrabajo") or {}, ensure_ascii=False),
                "planEconomico": json.dumps(data.get("planEconomico") or {}, ensure_ascii=False),
            }
            cli.execute_task(task["id"], payload)
            avanzado = True

        return JsonResponse({"ok": True, "caseId": case_id, "avanzado": avanzado}, status=201)

    except Exception as e:
        return JsonResponse({"error":"Error integrando con Bonita", "detail": str(e)}, status=500)
