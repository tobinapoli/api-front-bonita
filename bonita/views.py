from __future__ import annotations
import json
import time
from typing import Any, Dict

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .bonita_client import BonitaClient
from .validators import validate_iniciar_payload


# --------------------------- Páginas HTML ---------------------------

def index_page(req: HttpRequest):
    return render(req, "bonita/index.html")


def home_page(req: HttpRequest):
    return render(req, "bonita/home.html")


def login_page(req: HttpRequest):
    return render(req, "bonita/login.html")


def nuevo_proyecto_page(req: HttpRequest):
    return render(req, "bonita/nuevo.html")


def revisar_proyectos_page(req: HttpRequest):
    return render(req, "bonita/revisar.html")


def pedido_page(req: HttpRequest):
    """
    Página para registrar un pedido asociado a un proyecto ya creado.
    Espera en la URL:
      - ?case=<caseId de Bonita>
      - ?proyecto=<id del proyecto en la API cloud>
    """
    return render(req, "bonita/pedido.html")


def revisar_pedidos_proyecto_page(req: HttpRequest):
    """
    Página para que la Red de ONGs vea los pedidos de un proyecto concreto.

    Espera en la URL:
      - ?case=<caseId de Bonita>
      - ?proyecto=<id del proyecto en la API cloud> (solo para mostrar)
    """
    return render(req, "bonita/ver_pedidos.html")


# --------------------------- Helpers ---------------------------

def _json(req: HttpRequest) -> Dict[str, Any]:
    try:
        return json.loads(req.body.decode("utf-8")) if req.body else {}
    except Exception:
        return {}


# --------------------------- API: LOGIN ---------------------------

@csrf_exempt
def login_api(req: HttpRequest):
    """
    1. Recibe usuario y contraseña del frontend.
    2. Inicia sesión en Bonita y crea una instancia del proceso ProjectPlanning.
    3. Devuelve el caseId sin esperar a que aparezcan las tareas (flujo no bloqueante).
    """
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

        # Buscar proceso ProjectPlanning
        proc_id = cli.get_process_definition_id(
            getattr(settings, "BONITA_PROCESS_NAME", "ProjectPlanning"),
            getattr(settings, "BONITA_PROCESS_VERSION", "1.0"),
        )
        if not proc_id:
            return JsonResponse(
                {"ok": False, "error": "Proceso ProjectPlanning 1.0 no encontrado"},
                status=500,
            )

        # Instanciar proceso con usuario y password como payload
        inst = cli.instantiate_process(proc_id, {"apiUser": api_user, "apiPass": api_pass})
        case_id = str((inst or {}).get("caseId") or (inst or {}).get("id") or "")
        if not case_id:
            return JsonResponse({"ok": False, "error": "No se obtuvo caseId"}, status=500)

        # Devolvemos directamente el caseId
        return JsonResponse({"ok": True, "caseId": case_id}, status=200)

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Fallo integración Bonita", "detail": str(e)},
            status=500,
        )


# --------------------------- API: Iniciar proyecto ---------------------------

@csrf_exempt
def iniciar_proyecto_api(req: HttpRequest):
    """
    Empuja el contrato de 'Definir plan de trabajo y económico'.
    Si no existe un caseId válido, instancia el proceso.
    Luego espera a que Bonita complete el conector que crea el proyecto
    y devuelve el ID de proyecto al frontend.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
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
            return JsonResponse(
                {"error": "Usuario Bonita no encontrado", "detail": assignee_username},
                status=500,
            )

        # Si no vino caseId, crear nueva instancia
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

        # Buscar tarea "Definir plan..."
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Definir plan de trabajo y economico",
            timeout_sec=45,
        )
        if not task:
            return JsonResponse(
                {"ok": False, "error": "No apareció la tarea 'Definir plan de trabajo y economico'."},
                status=409,
            )

        # Asignar y ejecutar tarea
        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "nombre": str(data.get("nombre") or ""),
            "descripcion": str(data.get("descripcion") or ""),
            "planTrabajo": json.dumps(data.get("planTrabajo") or {}, ensure_ascii=False),
            "planEconomico": json.dumps(data.get("planEconomico") or {}, ensure_ascii=False),
        }
        cli.execute_task(task["id"], payload_contrato)

        # ---------- Esperar a que el conector cree el proyecto ----------
        proyecto_id = None
        raw_body_proyecto = None

        deadline = time.time() + 10  # hasta 10 segundos
        last_raw_pid = None

        while time.time() < deadline and proyecto_id is None:
            # 1) Intento directo: variable proyectoId
            var_pid = cli.get_case_variable(case_id, "proyectoId")
            if var_pid and "value" in var_pid:
                v = (var_pid["value"] or "").strip()
                last_raw_pid = v
                if v and v.lower() != "null":
                    try:
                        proyecto_id = int(v)
                    except ValueError:
                        proyecto_id = v
                    break

            time.sleep(0.4)

        # 2) Si sigue en None, probar leyendo body_proyecto y parseando JSON
        var_proy = cli.get_case_variable(case_id, "body_proyecto")
        if var_proy and "value" in var_proy:
            raw_body_proyecto = (var_proy["value"] or "").strip()
            if proyecto_id is None and raw_body_proyecto and raw_body_proyecto.lower() != "null":
                try:
                    obj = json.loads(raw_body_proyecto)
                except Exception:
                    obj = None

                if isinstance(obj, str):
                    try:
                        obj2 = json.loads(obj)
                        obj = obj2
                    except Exception:
                        pass

                if isinstance(obj, dict):
                    proyecto_id = (
                        obj.get("id")
                        or obj.get("proyectoId")
                        or obj.get("id_proyecto")
                    )

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "avanzado": True,
                "proyectoId": proyecto_id,
                "rawBodyProyecto": raw_body_proyecto,
            },
            status=201,
        )

    except Exception as e:
        return JsonResponse(
            {"error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )


# --------------------------- API: Registrar pedido ---------------------------

@csrf_exempt
def registrar_pedido_api(req: HttpRequest):
    """
    Completa la tarea 'Registrar pedido' en Bonita.

    Espera un JSON:
      {
        "caseId": "...",
        "pedidoTipo": "...",
        "pedidoDetalle": "..."
      }

    El conector de salida de esa tarea se encarga de hablar con la API JWT
    para crear el pedido en el proyecto correspondiente.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    pedido_tipo = str(data.get("pedidoTipo") or "").strip()
    pedido_detalle = str(data.get("pedidoDetalle") or "").strip()

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)
    if not pedido_tipo:
        return JsonResponse({"ok": False, "error": "Falta pedidoTipo"}, status=400)
    if not pedido_detalle:
        return JsonResponse({"ok": False, "error": "Falta pedidoDetalle"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse(
                {"ok": False, "error": "Usuario Bonita no encontrado", "detail": assignee_username},
                status=500,
            )

        # Buscar tarea "Registrar pedido"
        task = cli.wait_ready_task_in_case(case_id, task_name="Registrar pedido", timeout_sec=45)
        if not task:
            return JsonResponse(
                {"ok": False, "error": "No apareció la tarea 'Registrar pedido'."},
                status=409,
            )

        # Asignar y ejecutar tarea con el contrato
        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "pedidoTipo": pedido_tipo,
            "pedidoDetalle": pedido_detalle,
        }
        cli.execute_task(task["id"], payload_contrato)

        # Leer variables que deja el conector REST de salida
        pedido_id = None
        status_code_pedido = None
        body_pedido_json = None
        body_pedido_raw = None

        var_id = cli.get_case_variable(case_id, "pedidoId")
        if var_id and "value" in var_id:
            v = var_id["value"]
            try:
                pedido_id = int(v)
            except Exception:
                pedido_id = v

        var_status = cli.get_case_variable(case_id, "status_code_pedido")
        if var_status and "value" in var_status:
            v = var_status["value"]
            try:
                status_code_pedido = int(v)
            except Exception:
                status_code_pedido = v

        var_body = cli.get_case_variable(case_id, "body_pedido")
        if var_body and "value" in var_body and (var_body["value"] or "").strip():
            body_pedido_raw = var_body["value"]
            try:
                body_pedido_json = json.loads(body_pedido_raw)
            except Exception:
                body_pedido_json = None

        resp: Dict[str, Any] = {
            "ok": True,
            "caseId": case_id,
            "pedidoId": pedido_id,
            "statusCode": status_code_pedido,
        }
        if body_pedido_json is not None:
            resp["pedido"] = body_pedido_json
        elif body_pedido_raw is not None:
            resp["pedidoRaw"] = body_pedido_raw

        return JsonResponse(resp, status=201)

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )


@csrf_exempt
def elegir_proyecto_api(req: HttpRequest):
    """
    Completa la tarea 'Revisar proyectos' seteando proyectoSeleccionadoId.

    Espera JSON:
      {
        "caseId": "...",
        "proyectoId": 11
      }
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    proyecto_id = data.get("proyectoId")

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)
    if proyecto_id in (None, "", []):
        return JsonResponse({"ok": False, "error": "Falta proyectoId"}, status=400)

    # intentar castear a int si viene como string
    try:
        proyecto_id_int = int(proyecto_id)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "proyectoId debe ser entero"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse(
                {"ok": False, "error": "Usuario Bonita no encontrado", "detail": assignee_username},
                status=500,
            )

        # Buscar la tarea 'Revisar proyectos' en ese caso
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Revisar proyectos",
            timeout_sec=45,
        )
        if not task:
            return JsonResponse(
                {"ok": False, "error": "No apareció la tarea 'Revisar proyectos'."},
                status=409,
            )

        # Asignar y ejecutar con el contrato
        cli.assign_task(task["id"], user_id)
        contract_payload = {
            "proyectoSeleccionadoId": proyecto_id_int,
        }
        cli.execute_task(task["id"], contract_payload)

        # A partir de acá, el flujo en Bonita pasa a 'Revisar pedidos'
        return JsonResponse(
            {"ok": True, "caseId": case_id, "proyectoId": proyecto_id_int},
            status=200,
        )

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )


# --------------------------- API: Revisar proyectos ---------------------------

@csrf_exempt
def revisar_proyectos_api(req: HttpRequest):
    """
    Devuelve lo que dejó el conector ON_ENTER de la tarea 'Revisar proyecto'
    en la variable de proceso 'proyectosJson'.
    """
    case_id = (req.GET.get("case") or _json(req).get("caseId") or "").strip()
    if not case_id:
        return JsonResponse({"error": "Falta caseId/case"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        var = cli.get_case_variable(case_id, "proyectosJson")
        if not var or "value" not in var or not (var["value"] or "").strip():
            return JsonResponse(
                {"ok": True, "caseId": case_id, "proyectos": [], "mensaje": "No hay proyectos"},
                status=200,
            )

        try:
            proyectos = json.loads(var["value"])
        except Exception:
            proyectos = []

        return JsonResponse({"ok": True, "caseId": case_id, "proyectos": proyectos}, status=200)
    except Exception as e:
        return JsonResponse(
            {"error": "Error consultando Bonita", "detail": str(e)},
            status=500,
        )


# --------------------------- API: Revisar pedidos de un proyecto -------------


@csrf_exempt
def revisar_pedidos_proyecto_api(req: HttpRequest):
    """
    Devuelve lo que dejó el conector ON_ENTER de la tarea 'Revisar pedidos'
    en la variable de proceso 'pedidosJson'.
    """
    case_id = (req.GET.get("case") or _json(req).get("caseId") or "").strip()
    if not case_id:
        return JsonResponse({"error": "Falta caseId/case"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        var = cli.get_case_variable(case_id, "pedidosJson")
        if not var or "value" not in var or not (var["value"] or "").strip():
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "pedidos": [],
                    "mensaje": "No hay pedidos",
                },
                status=200,
            )

        try:
            pedidos = json.loads(var["value"])
        except Exception:
            pedidos = []

        return JsonResponse(
            {"ok": True, "caseId": case_id, "pedidos": pedidos},
            status=200,
        )
    except Exception as e:
        return JsonResponse(
            {"error": "Error consultando Bonita", "detail": str(e)},
            status=500,
        )

@csrf_exempt
def finalizar_revision_pedidos_api(req: HttpRequest):
    """
    Completa la tarea 'Revisar pedidos' en Bonita seteando verOtroProyecto.
    Espera JSON:
      {
        "caseId": "...",
        "verOtroProyecto": true/false
      }
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    ver_otro = bool(data.get("verOtroProyecto"))

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse(
                {"ok": False, "error": "Usuario Bonita no encontrado", "detail": assignee_username},
                status=500,
            )

        # Buscar tarea 'Revisar pedidos'
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Revisar pedidos",
            timeout_sec=45,
        )
        if not task:
            return JsonResponse(
                {"ok": False, "error": "No apareció la tarea 'Revisar pedidos' para este caso."},
                status=409,
            )

        # Asignar y ejecutar con el contrato verOtroProyecto
        cli.assign_task(task["id"], user_id)
        cli.execute_task(task["id"], {"verOtroProyecto": ver_otro})

        return JsonResponse({"ok": True, "caseId": case_id, "verOtroProyecto": ver_otro}, status=200)

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )
