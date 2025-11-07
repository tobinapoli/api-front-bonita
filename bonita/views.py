# bonita/views.py (añadir imports si faltan)
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
from django.http import JsonResponse, HttpRequest
import json
from .bonita_client import BonitaClient
from django.conf import settings
from .validators import validate_iniciar_payload

def login_page(req: HttpRequest):
    return render(req, "bonita/login.html")

@csrf_exempt
def login_api(req: HttpRequest):
    if req.method != "POST":
        return JsonResponse({"error":"POST only"}, status=405)
    try:
        body = json.loads(req.body.decode("utf-8")) if req.body else {}
        api_user = str(body.get("user") or "")
        api_pass = str(body.get("pass") or "")
        if not api_user or not api_pass:
            return JsonResponse({"ok": False, "error": "Faltan credenciales"}, status=400)

        cli = BonitaClient(); cli.login()
        proc_id = cli.get_process_definition_id("ProjectPlanning", "1.0")
        if not proc_id:
            return JsonResponse({"ok": False, "error": "Proceso ProjectPlanning 1.0 no encontrado"}, status=500)

        # 1) Instanciamos el caso
        inst = cli.instantiate_process(proc_id, {})
        case_id = (inst or {}).get("caseId") or (inst or {}).get("id")
        if not case_id:
            return JsonResponse({"ok": False, "error": "No se obtuvo caseId"}, status=500)

        # 2) Esperamos la tarea Login y la ejecutamos con el contract
        task = cli.wait_ready_task_in_case(str(case_id), task_name="Login")
        if not task:
            return JsonResponse({"ok": False, "error": "Tarea Login no está ready"}, status=500)

        # Asignamos (por simplicidad, a walter.bates)
        user_id = cli.get_user_id_by_username(settings.BONITA_ASSIGNEE)
        if not user_id:
            return JsonResponse({"ok": False, "error": "Assignee Bonita no encontrado"}, status=500)
        cli.assign_task(task["id"], user_id)

        # Ejecutamos contract de la tarea Login (apiUser/apiPass)
        cli.execute_task(task["id"], {"apiUser": api_user, "apiPass": api_pass})

        # 3) Devolvemos el caseId para que el front pase a /bonita/nuevo/?case=...
        return JsonResponse({"ok": True, "caseId": case_id}, status=200)
    except Exception as e:
        return JsonResponse({"ok": False, "error": "Fallo integración Bonita", "detail": str(e)}, status=500)

def nuevo_proyecto_page(req: HttpRequest):
    return render(req, "bonita/nuevo.html")

# ... arriba igual ...

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

        # 🔹 NUEVO: ejecutar automáticamente la tarea "Login"
        login_task = cli.wait_ready_task_in_case(str(case_id), task_name="Login", timeout_sec=20)
        if not login_task:
            return JsonResponse({"error":"La tarea 'Login' no quedó lista (posible fallo del conector)."}, status=500)

        assignee = settings.BONITA_ASSIGNEE
        user_id = cli.get_user_id_by_username(assignee)
        if not user_id:
            return JsonResponse({"error":"Usuario Bonita no encontrado", "detail": assignee}, status=500)

        cli.assign_task(login_task["id"], user_id)
        # si la tarea Login no tiene contrato, mandamos {}. (El conector usa variables/parametros del proceso)
        cli.execute_task(login_task["id"], {})

        # 🔹 Como antes: ahora sí esperamos la siguiente tarea
        task = cli.wait_ready_task_in_case(str(case_id), task_name="Definir plan de trabajo y economico", timeout_sec=20)
        avanzado = False
        if task:
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
