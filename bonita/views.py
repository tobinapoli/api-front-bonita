from __future__ import annotations
import json
import time
from typing import Any, Dict

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
import requests

from .bonita_client import BonitaClient
from .validators import validate_iniciar_payload
from .models import ProyectoMonitoreo   # <--- ESTO FALTABA

# --------------------------- Páginas HTML ---------------------------

def index_page(req: HttpRequest):
    return render(req, "bonita/index.html")


def home_page(req: HttpRequest):
    return render(req, "bonita/home.html")


def login_page(req: HttpRequest):
    return render(req, "bonita/login.html")


def nuevo_proyecto_page(req: HttpRequest):
    ctx = {
        "case": req.GET.get("case", ""),
        "rol": req.GET.get("rol", "")
    }
    return render(req, "bonita/nuevo.html", ctx)




def revisar_proyectos_page(req: HttpRequest):
    ctx = {
        "case": req.GET.get("case", ""),
        "rol": req.GET.get("rol", "")
    }
    return render(req, "bonita/revisar.html", ctx)

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


def compromiso_page(req: HttpRequest):
    ctx = {
        "case": req.GET.get("case", ""),
        "proyecto": req.GET.get("proyecto", ""),
        "pedido": req.GET.get("pedido", ""),
        "rol": req.GET.get("rol", "")
    }
    return render(req, "bonita/compromiso.html", ctx)

def consejo_page(req: HttpRequest):
    ctx = {
        "case": req.GET.get("case", "")
    }
    return render(req, "bonita/consejo.html", ctx)

def evaluar_propuestas_page(req: HttpRequest):
    ctx = {
        "case": req.GET.get("case", ""),
        "proyecto": req.GET.get("proyecto", ""),
        "rol": req.GET.get("rol", ""),
    }
    return render(req, "bonita/evaluar_propuestas.html", ctx)


def monitoreo_proyecto_page(req: HttpRequest):
    ctx = {
        "case": req.GET.get("case", ""),
        "proyecto": req.GET.get("proyecto", ""),
    }
    return render(req, "bonita/monitoreo.html", ctx)

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
    2. Según el flag 'consejo' decide si instancia ProjectPlanning o Consejo Directivo.
    3. Devuelve el caseId sin esperar a que aparezcan las tareas (flujo no bloqueante).
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    body = _json(req)
    api_user = str(body.get("user") or body.get("username") or "").strip()
    api_pass = str(body.get("pass") or body.get("password") or "").strip()
    is_consejo = bool(body.get("consejo"))

    if not api_user or not api_pass:
        return JsonResponse({"ok": False, "error": "Faltan credenciales"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        # Elegir proceso según el flag "consejo"
        if is_consejo:
            proc_name = getattr(settings, "BONITA_PROCESS_NAME_CONSEJO", "Consejo Directivo")
            proc_version = getattr(settings, "BONITA_PROCESS_VERSION_CONSEJO", "1.0")
        else:
            proc_name = getattr(settings, "BONITA_PROCESS_NAME", "ProjectPlanning")
            proc_version = getattr(settings, "BONITA_PROCESS_VERSION", "1.0")

        # Buscar proceso correspondiente
        proc_id = cli.get_process_definition_id(proc_name, proc_version)
        if not proc_id:
            return JsonResponse(
                {"ok": False, "error": f"Proceso {proc_name} {proc_version} no encontrado"},
                status=500,
            )

        # Instanciar proceso con usuario y password como payload
        inst = cli.instantiate_process(proc_id, {"apiUser": api_user, "apiPass": api_pass})
        case_id = str((inst or {}).get("caseId") or (inst or {}).get("id") or "")
        if not case_id:
            return JsonResponse({"ok": False, "error": "No se obtuvo caseId"}, status=500)

        return JsonResponse({"ok": True, "caseId": case_id}, status=200)

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Fallo integración Bonita", "detail": str(e)},
            status=500,
        )


@csrf_exempt
def next_step_api(req: HttpRequest):
    """
    Dado un caseId recién creado, espera la primera tarea 'ready'
    y decide a qué pantalla debe ir el usuario.

    - Si la tarea es 'Definir plan de trabajo y economico' => ONG originante => /bonita/nuevo/
    - Si la tarea es 'Revisar proyectos' => Red de ONGs => /bonita/revisar/
    - Si la tarea es 'Revisar proyecto y cargar observaciones' => Consejo Directivo => /bonita/consejo/
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        # Esperamos alguna tarea ready del caso (la primera que aparezca)
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name=None,      # sin filtrar por nombre
            timeout_sec=15,
        )
        if not task:
            return JsonResponse(
                {"ok": False, "error": "No apareció ninguna tarea ready para este caso."},
                status=409,
            )

        name = (task.get("name") or task.get("displayName") or "").strip()

        # Default
        rol = "desconocido"
        url = f"/bonita/home/?case={case_id}"

        if name == "Definir plan de trabajo y economico":
            rol = "ong_originante"
            url = f"/bonita/nuevo/?case={case_id}"
        elif name == "Revisar proyectos":
            rol = "red_ongs"
            url = f"/bonita/revisar/?case={case_id}"
        elif name == "Revisar proyecto y cargar observaciones":
            rol = "consejo_directivo"
            url = f"/bonita/consejo/?case={case_id}"
        elif name == "Evaluar Respuestas":  
            rol = "consejo_directivo"
            url = f"/bonita/consejo/evaluar/?case={case_id}"

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "tarea": name,
                "rol": rol,
                "url": url,
            },
            status=200,
        )

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Fallo al decidir siguiente paso", "detail": str(e)},
            status=500,
        )
def consejo_evaluar_page(req: HttpRequest):
    ctx = {
        "case": req.GET.get("case", "")
    }
    return render(req, "bonita/consejo_evaluar.html", ctx)
@csrf_exempt
def obtener_datos_evaluacion_api(req: HttpRequest):
    """
    Lee la variable 'respuestasJson' del caso en Bonita.
    Esta variable ya fue llenada por la tarea automática 'Buscar Respuestas'.
    """
    case_id = (req.GET.get("case") or _json(req).get("caseId") or "").strip()
    if not case_id:
        return JsonResponse({"error": "Falta caseId"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()
        
        # Leemos la variable donde el conector GET guardó la respuesta
        var = cli.get_case_variable(case_id, "respuestasJson")
        
        lista = []
        if var and "value" in var and var["value"]:
            try:
                lista = json.loads(var["value"])
            except:
                pass

        return JsonResponse({"ok": True, "respuestas": lista})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
def enviar_evaluacion_consejo_api(req: HttpRequest):
    """
    Completa la tarea 'Evaluar Respuestas' en Bonita.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
        
    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    obs_id = data.get("observacionId")
    aprobada = bool(data.get("aprobada"))

    if not case_id or not obs_id:
        return JsonResponse({"ok": False, "error": "Faltan datos"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()
        
        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)

        # Buscar tarea
        task = cli.wait_ready_task_in_case(case_id, "Evaluar Respuestas", timeout_sec=10)
        if not task:
             return JsonResponse({"ok": False, "error": "La tarea 'Evaluar Respuestas' no está lista."}, status=409)

        # Ejecutar tarea
        cli.assign_task(task["id"], user_id)
        contract = {
            "observacionId": int(obs_id),
            "aprobada": aprobada
        }
        cli.execute_task(task["id"], contract)

        return JsonResponse({"ok": True})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
# --------------------------- API: Iniciar proyecto ---------------------------

@csrf_exempt
def iniciar_proyecto_api(req: HttpRequest):
    """
    Empuja el contrato de 'Definir plan de trabajo y económico'.
    Si no existe un caseId válido, instancia el proceso.
    Luego espera a que Bonita complete el conector que crea el proyecto
    y devuelve el ID de proyecto al frontend.

    Además, guarda un snapshot del proyecto en la BD local (ProyectoMonitoreo)
    usando el proyectoId devuelto por la API cloud.
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
            timeout_sec=15,
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

        # ---------- Guardar snapshot en la BD local ----------
        try:
            if proyecto_id not in (None, "", []):
                try:
                    pid_int = int(proyecto_id)
                except (TypeError, ValueError):
                    pid_int = None

                if pid_int is not None:
                    ProyectoMonitoreo.objects.update_or_create(
                        proyecto_id=pid_int,
                        defaults={
                            "nombre": str(data.get("nombre") or ""),
                            "descripcion": str(data.get("descripcion") or ""),
                            "plan_trabajo": data.get("planTrabajo") or {},
                        },
                    )
        except Exception:
            # No romper el flujo si falla sólo el snapshot
            pass

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

    El conector de salida crea el pedido en la API JWT.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id        = str(data.get("caseId") or "").strip()
    pedido_tipo    = str(data.get("pedidoTipo") or "").strip()
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

        # Buscamos la tarea 'Registrar pedido' en ese caso
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Registrar pedido",
            timeout_sec=5,
        )

        # Si la tarea YA NO está ready, asumimos que ya se ejecutó antes
        # (por ejemplo, pestaña vieja). No lo tratamos como error duro.
        if not task:
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "note": "La tarea 'Registrar pedido' no estaba ready; se asume ya ejecutada."
                },
                status=200,
            )

        # Asignar y ejecutar la tarea con el contrato de Bonita
        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "pedidoTipo":    pedido_tipo,
            "pedidoDetalle": pedido_detalle,
        }
        cli.execute_task(task["id"], payload_contrato)

        # Leer variables que dejó el conector de salida
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

    Si la tarea 'Revisar proyectos' YA NO está ready (porque el flujo ya avanzó),
    se considera OK igual y NO se devuelve error, para que el front pueda navegar
    sin romperse aunque el usuario esté en pestañas viejas.
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
            timeout_sec=15,
        )

        # Si la tarea YA NO está ready, asumimos que ya se ejecutó antes.
        # No lo tratamos como error para permitir navegación desde pestañas viejas.
        if not task:
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "proyectoId": proyecto_id_int,
                    "note": "La tarea 'Revisar proyectos' no estaba ready; se asume ya ejecutada.",
                },
                status=200,
            )

        # Si la tarea existe, la ejecutamos normalmente
        cli.assign_task(task["id"], user_id)
        contract_payload = {"proyectoSeleccionadoId": proyecto_id_int}
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

        # Buscar tarea 'Revisar pedidos'.
        # Si no aparece, asumimos que ya fue ejecutada (pestaña vieja / doble click).
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Revisar pedidos",
            timeout_sec=3,
        )
        if not task:
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "verOtroProyecto": ver_otro,
                    "note": "La tarea 'Revisar pedidos' no estaba ready; se asume ya ejecutada."
                },
                status=200,
            )

        # Asignar y ejecutar con el contrato verOtroProyecto
        cli.assign_task(task["id"], user_id)
        cli.execute_task(task["id"], {"verOtroProyecto": ver_otro})

        return JsonResponse(
            {"ok": True, "caseId": case_id, "verOtroProyecto": ver_otro},
            status=200,
        )

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )


# bonita/views.py (agregar al final junto al resto de APIs)

@csrf_exempt
def registrar_compromiso_api(req: HttpRequest):
    """
    Completa la tarea 'Registrar compromiso' en Bonita.

    Espera un JSON:
      {
        "caseId": "...",
        "pedidoId": 6,
        "compromisoTipo": "...",
        "compromisoDetalle": "..."
      }

    El conector de salida de esa tarea se encarga de hablar con la API JWT
    para crear el compromiso asociado al pedido correspondiente.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id     = str(data.get("caseId") or "").strip()
    comp_tipo   = str(data.get("compromisoTipo") or "").strip()
    comp_detalle = str(data.get("compromisoDetalle") or "").strip()
    pedido_raw  = data.get("pedidoId")

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)
    if not comp_tipo:
        return JsonResponse({"ok": False, "error": "Falta compromisoTipo"}, status=400)
    if not comp_detalle:
        return JsonResponse({"ok": False, "error": "Falta compromisoDetalle"}, status=400)
    if pedido_raw in (None, "", []):
        return JsonResponse({"ok": False, "error": "Falta pedidoId"}, status=400)

    try:
        pedido_id = int(pedido_raw)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "pedidoId debe ser entero"}, status=400)

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

        # Buscar tarea "Registrar compromiso"
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Registrar compromiso",
            timeout_sec=15,
        )
        if not task:
            return JsonResponse(
                {"ok": False, "error": "No apareció la tarea 'Registrar compromiso'."},
                status=409,
            )

        # Asignar y ejecutar con el contrato
        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "compromisoTipo": comp_tipo,
            "compromisoDetalle": comp_detalle,
            "pedidoId": pedido_id,
        }
        cli.execute_task(task["id"], payload_contrato)

        # Opcional: leer lo que dejó el conector de salida
        compromiso_id = None
        status_code_comp = None
        body_comp_json = None
        body_comp_raw = None

        var_id = cli.get_case_variable(case_id, "compromisoId")
        if var_id and "value" in var_id:
            v = var_id["value"]
            try:
                compromiso_id = int(v)
            except Exception:
                compromiso_id = v

        var_status = cli.get_case_variable(case_id, "status_code_compromiso")
        if var_status and "value" in var_status:
            v = var_status["value"]
            try:
                status_code_comp = int(v)
            except Exception:
                status_code_comp = v

        var_body = cli.get_case_variable(case_id, "body_compromiso")
        if var_body and "value" in var_body and (var_body["value"] or "").strip():
            body_comp_raw = var_body["value"]
            try:
                body_comp_json = json.loads(body_comp_raw)
            except Exception:
                body_comp_json = None

        resp: Dict[str, Any] = {
            "ok": True,
            "caseId": case_id,
            "pedidoId": pedido_id,
            "compromisoId": compromiso_id,
            "statusCode": status_code_comp,
        }
        if body_comp_json is not None:
            resp["compromiso"] = body_comp_json
        elif body_comp_raw is not None:
            resp["compromisoRaw"] = body_comp_raw

        return JsonResponse(resp, status=201)

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )


# --------------------------- API: Consejo Directivo ---------------------------

@csrf_exempt
def obtener_proyectos_en_ejecucion_api(req: HttpRequest):
    """
    Devuelve la lista de proyectos en ejecución para que el Consejo Directivo 
    pueda revisarlos.
    
    IMPORTANTE: Este endpoint espera que el conector ON_ENTER de la tarea
    "Revisar proyecto y cargar observaciones" en Bonita haya consultado la
    API JWT (endpoint GET /api/consejo/proyectos/) y guardado el resultado
    en la variable de proceso 'proyectosJson'.
    
    Espera en GET o POST:
      - caseId o case
    """
    case_id = (req.GET.get("case") or _json(req).get("caseId") or "").strip()
    if not case_id:
        return JsonResponse({"error": "Falta caseId/case"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        # Leer la variable 'proyectosJson' que debería contener los proyectos
        # en ejecución obtenidos por el conector de entrada de Bonita
        var = cli.get_case_variable(case_id, "proyectosJson")
        
        if not var or "value" not in var or not (var["value"] or "").strip():
            return JsonResponse(
                {
                    "ok": True, 
                    "caseId": case_id, 
                    "proyectos": [], 
                    "mensaje": "No hay proyectos en ejecución para revisar"
                },
                status=200,
            )

        try:
            proyectos = json.loads(var["value"])
            # Si proyectos es una lista, devolver tal cual
            # Si es un objeto con propiedad "proyectos", extraerla
            if isinstance(proyectos, dict) and "proyectos" in proyectos:
                proyectos = proyectos["proyectos"]
        except Exception as e:
            return JsonResponse(
                {"error": "Error parseando proyectos", "detail": str(e)},
                status=500,
            )

        return JsonResponse(
            {
                "ok": True, 
                "caseId": case_id, 
                "proyectos": proyectos,
                "count": len(proyectos) if isinstance(proyectos, list) else 0
            }, 
            status=200
        )
        
    except Exception as e:
        return JsonResponse(
            {"error": "Error consultando Bonita", "detail": str(e)},
            status=500,
        )


@csrf_exempt
def enviar_observaciones_consejo_api(req: HttpRequest):
    """
    Completa la tarea 'Revisar proyecto y cargar observaciones' del proceso 
    Consejo Directivo.
    
    Espera un JSON:
      {
        "caseId": "...",
        "proyectoId": 123,
        "observaciones": "texto de las observaciones..."
      }
    
    IMPORTANTE: El conector de salida de esta tarea en Bonita debe:
    1. Leer las variables del contrato (proyectoId, observaciones)
    2. Hacer POST a la API JWT: /api/proyectos/{proyectoId}/observaciones/crear/
       con el body: {"texto": observaciones}
    3. Guardar la respuesta en variables como 'observacionId' y 'status_code_observacion'
    
    El conector debe usar el token JWT almacenado en la variable 'access' del proceso.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    proyecto_id_raw = data.get("proyectoId")
    observaciones = str(data.get("observaciones") or "").strip()

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)
    if proyecto_id_raw in (None, "", []):
        return JsonResponse({"ok": False, "error": "Falta proyectoId"}, status=400)
    if not observaciones:
        return JsonResponse({"ok": False, "error": "Falta texto de observaciones"}, status=400)

    try:
        proyecto_id = int(proyecto_id_raw)
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

        # Buscar tarea "Revisar proyecto y cargar observaciones"
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Revisar proyecto y cargar observaciones",
            timeout_sec=15,
        )
        
        if not task:
            # La tarea ya fue ejecutada (navegación desde pestaña vieja)
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "proyectoId": proyecto_id,
                    "note": "La tarea 'Revisar proyecto y cargar observaciones' no estaba ready; se asume ya ejecutada."
                },
                status=200,
            )

        # Asignar y ejecutar con el contrato
        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "proyectoId": proyecto_id,
            "observaciones": observaciones,
        }
        cli.execute_task(task["id"], payload_contrato)

        # Esperar a que el conector de salida complete
        # y leer las variables que dejó
        observacion_id = None
        status_code = None
        body_observacion = None

        # Dar tiempo al conector para ejecutarse
        time.sleep(1)

        var_id = cli.get_case_variable(case_id, "observacionId")
        if var_id and "value" in var_id:
            v = var_id["value"]
            try:
                observacion_id = int(v)
            except Exception:
                observacion_id = v

        var_status = cli.get_case_variable(case_id, "status_code_observacion")
        if var_status and "value" in var_status:
            v = var_status["value"]
            try:
                status_code = int(v)
            except Exception:
                status_code = v

        var_body = cli.get_case_variable(case_id, "body_observacion")
        if var_body and "value" in var_body and (var_body["value"] or "").strip():
            try:
                body_observacion = json.loads(var_body["value"])
            except Exception:
                body_observacion = var_body["value"]

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "proyectoId": proyecto_id,
                "observacionId": observacion_id,
                "statusCode": status_code,
                "body": body_observacion,
                "mensaje": "Observaciones enviadas correctamente"
            },
            status=201,
        )

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )


@csrf_exempt
def ver_observaciones_proyecto_api(req: HttpRequest, proyecto_id: int):
    """
    Obtiene las observaciones de un proyecto específico.
    
    Este endpoint hace una consulta directa a la API JWT para obtener
    las observaciones de un proyecto.
    
    Espera en GET:
      - case o caseId: ID del caso en Bonita
      - proyectoId: ID del proyecto (en la URL path)
    
    Retorna las observaciones con su estado (pendiente, respondida, vencida)
    y días restantes.
    """
    import requests
    
    case_id = (req.GET.get("case") or req.GET.get("caseId") or "").strip()
    proyecto_id_raw = proyecto_id
    
    if not case_id:
        return JsonResponse({"error": "Falta caseId/case"}, status=400)
    if not proyecto_id_raw:
        return JsonResponse({"error": "Falta proyectoId en URL"}, status=400)
    
    try:
        proyecto_id = int(proyecto_id_raw)
    except (TypeError, ValueError):
        return JsonResponse({"error": "proyectoId debe ser entero"}, status=400)
    
    try:
        cli = BonitaClient()
        cli.login()
        
        # Obtener el token JWT de la variable del caso
        var_token = cli.get_case_variable(case_id, "access")
        if not var_token or "value" not in var_token or not (var_token["value"] or "").strip():
            return JsonResponse(
                {"error": "No se encontró token de autenticación en el caso"},
                status=401,
            )
        
        jwt_token = var_token["value"].strip()
        
        # Construir URL de la API
        api_base_url = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
        url = f"{api_base_url}/api/proyectos/{proyecto_id}/observaciones/"
        
        # Hacer request a la API JWT
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            observaciones = response.json()
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "proyectoId": proyecto_id,
                    "observaciones": observaciones,
                },
                status=200,
            )
        else:
            return JsonResponse(
                {
                    "ok": False,
                    "error": f"Error al obtener observaciones: {response.status_code}",
                    "detail": response.text,
                },
                status=response.status_code,
            )
            
    except requests.RequestException as e:
        return JsonResponse(
            {"ok": False, "error": "Error de conexión con API", "detail": str(e)},
            status=500,
        )
    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error consultando observaciones", "detail": str(e)},
            status=500,
        )

# --------------------------- API: Revisar compromisos (Evaluar propuestas) ---


@csrf_exempt
def revisar_compromisos_api(req: HttpRequest):
    """
    Devuelve lo que dejó el conector ON_ENTER de la tarea 'Evaluar propuestas'
    en la variable de proceso 'compromisosJson'.

    El conector en Bonita debe:
      - Hacer GET a:  /api/pedidos/<pedidoId>/compromisos/
      - Guardar el cuerpo en la variable de caso 'compromisosJson'
      - Guardar el status HTTP en 'code_compromisos'
    """
    case_id = (req.GET.get("case") or _json(req).get("caseId") or "").strip()
    if not case_id:
        return JsonResponse({"error": "Falta caseId/case"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        # Lista de compromisos
        var = cli.get_case_variable(case_id, "compromisosJson")
        if not var or "value" not in var or not (var["value"] or "").strip():
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "compromisos": [],
                    "mensaje": "No hay compromisos para este pedido",
                },
                status=200,
            )

        try:
            compromisos = json.loads(var["value"])
        except Exception:
            compromisos = []

        # Código HTTP que dejó el conector (opcional)
        status_code = None
        v_code = cli.get_case_variable(case_id, "code_compromisos")
        if v_code and "value" in v_code and (v_code["value"] or "").strip():
            try:
                status_code = int(v_code["value"])
            except Exception:
                status_code = v_code["value"]

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "compromisos": compromisos,
                "statusCode": status_code,
            },
            status=200,
        )

    except Exception as e:
        return JsonResponse(
            {"error": "Error consultando Bonita", "detail": str(e)},
            status=500,
        )


def _append_compromiso_aceptado(cli: BonitaClient, case_id: str, compromiso_id: int | None):
    """
    Agrega el compromiso aceptado al arreglo JSON 'compromisosAceptadosJson'
    de la instancia de proceso en Bonita y sincroniza ese arreglo
    con la tabla ProyectoMonitoreo en la BD local.

    Guarda un diccionario con:
      - id
      - detalle
      - fecha
      - estado  => tomando el valor final desde body_compromiso_cumplido
                   (o 'cumplido' por defecto).
    """
    if not compromiso_id:
        return

    # ----- Leer historial actual desde Bonita -----
    try:
        var = cli.get_case_variable(case_id, "compromisosAceptadosJson")
        raw = (var.get("value") or "").strip() if var else ""
    except Exception:
        raw = ""

    lista: list[Any] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                lista = parsed
        except Exception:
            lista = []

    # Evitar duplicados (tanto si son ints como dicts)
    for item in lista:
        if isinstance(item, dict) and item.get("id") == compromiso_id:
            return
        if isinstance(item, int) and item == compromiso_id:
            return

    # ----- Sacar detalle/fecha/estado base desde compromisosJson -----
    nuevo: dict[str, Any] | None = None
    try:
        var_comp = cli.get_case_variable(case_id, "compromisosJson")
        raw_comp = (var_comp.get("value") or "").strip() if var_comp else ""
        if raw_comp:
            comps = json.loads(raw_comp)
            if isinstance(comps, list):
                for c in comps:
                    try:
                        cid = int(c.get("id"))
                    except Exception:
                        continue
                    if cid == compromiso_id:
                        nuevo = {
                            "id": compromiso_id,
                            "detalle": c.get("detalle", ""),
                            "fecha": c.get("fecha", ""),
                            # estado base (luego lo pisamos con el final)
                            "estado": c.get("estado", ""),
                        }
                        break
    except Exception:
        nuevo = None

    if nuevo is None:
        nuevo = {
            "id": compromiso_id,
            "detalle": "",
            "fecha": "",
            "estado": "",
        }

    # ----- Tomar el estado FINAL desde body_compromiso_cumplido -----
    final_state = "cumplido"  # por defecto, porque ya está aceptado
    try:
        var_cc = cli.get_case_variable(case_id, "body_compromiso_cumplido")
        raw_cc = (var_cc.get("value") or "").strip() if var_cc else ""
        if raw_cc:
            obj = json.loads(raw_cc)
            if isinstance(obj, dict):
                cid_resp = obj.get("compromisoId")
                try:
                    cid_resp = int(cid_resp)
                except Exception:
                    pass
                if cid_resp == compromiso_id and obj.get("estado"):
                    final_state = str(obj["estado"])
    except Exception:
        # si falla, nos quedamos con "cumplido"
        pass

    nuevo["estado"] = final_state
    lista.append(nuevo)

    # ----- Guardar de nuevo en la variable de caso en Bonita -----
    try:
        cli.update_case_variable(
            case_id,
            "compromisosAceptadosJson",
            json.dumps(lista, ensure_ascii=False),
        )
    except Exception:
        # No rompemos el flujo si falla el tracking
        pass

    # ----- Sincronizar también en ProyectoMonitoreo -----
    try:
        var_pid = cli.get_case_variable(case_id, "proyectoId")
        pid_raw = (var_pid.get("value") or "").strip() if var_pid else ""
        if pid_raw:
            try:
                proj_id = int(pid_raw)
            except ValueError:
                proj_id = None

            if proj_id is not None:
                snap, _ = ProyectoMonitoreo.objects.get_or_create(
                    proyecto_id=proj_id,
                    defaults={"nombre": "", "descripcion": "", "plan_trabajo": {}},
                )
                snap.compromisos_aceptados = lista
                snap.save(update_fields=["compromisos_aceptados", "actualizado_en"])
    except Exception:
        # Tampoco rompemos el flujo si falla sólo la sincronización local
        pass





# --------------------------- API: Ejecutar 'Evaluar propuestas' --------------


@csrf_exempt
def evaluar_propuestas_api(req: HttpRequest):
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    proyecto_raw = data.get("proyectoId")
    comp_raw = data.get("compromisoIdSeleccionado")
    volver_raw = data.get("volverAEvaluar")
    finalizar_raw = data.get("finalizarPlan")  # viene del botón "Confirmar y pasar a ejecución"

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)

    # proyectoId es opcional
    try:
        proyecto_id = int(proyecto_raw) if proyecto_raw not in (None, "", []) else None
    except (TypeError, ValueError):
        proyecto_id = None

    # compromiso seleccionado (puede venir vacío si es "volver a evaluar")
    comp_str: str
    comp_id: int | None = None
    if comp_raw in (None, "", []):
        comp_str = ""
    else:
        try:
            comp_id = int(comp_raw)
            comp_str = str(comp_id)
        except (TypeError, ValueError):
            comp_str = str(comp_raw)

    def _to_bool(v) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "t", "yes", "y", "si", "sí")
        if isinstance(v, (int, float)):
            return bool(v)
        return False

    volver = _to_bool(volver_raw)
    finalizar = _to_bool(finalizar_raw)

    # Si no quiere volver a evaluar, tiene que haber un compromiso elegido
    if not volver and not comp_str:
        return JsonResponse(
            {
                "ok": False,
                "error": "Debés seleccionar un compromiso o marcar 'volver a evaluar'.",
            },
            status=400,
        )

    try:
        cli = BonitaClient()
        cli.login()

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Usuario Bonita no encontrado",
                    "detail": assignee_username,
                },
                status=500,
            )

        # 1) Ejecutar la tarea "Evaluar propuestas"
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Evaluar propuestas",
            timeout_sec=10,
        )

        if not task:
            # Si ya no está ready, asumimos que se ejecutó antes (pestaña vieja, etc.)
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "proyectoId": proyecto_id,
                    "compromisoIdSeleccionado": comp_id,
                    "volverAEvaluar": volver,
                    "finalizarPlan": finalizar,
                    "note": "La tarea 'Evaluar propuestas' no estaba ready; se asume ya ejecutada.",
                },
                status=200,
            )

        contract_payload = {
            "compromisoIdSeleccionado": comp_str,
            "volverAEvaluar": volver,
        }

        cli.assign_task(task["id"], user_id)
        cli.execute_task(task["id"], contract_payload)

        # 2) Si NO es "volver a evaluar" y hay compromiso elegido,
        # auto-ejecutamos "Acumular compromiso en el plan" y lo guardamos en el histórico
        if not volver and comp_str:
            task2 = cli.wait_ready_task_in_case(
                case_id,
                task_name="Acumular compromiso en el plan",
                timeout_sec=10,
            )
            if task2:
                cli.assign_task(task2["id"], user_id)
                cli.execute_task(task2["id"], {"finalizarPlan": finalizar})

            # registrar el compromiso como aceptado en el array
            _append_compromiso_aceptado(cli, case_id, comp_id)

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "proyectoId": proyecto_id,
                "compromisoIdSeleccionado": comp_id,
                "volverAEvaluar": volver,
                "finalizarPlan": finalizar,
            },
            status=200,
        )

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error integrando con Bonita", "detail": str(e)},
            status=500,
        )


@csrf_exempt
def resumen_proyecto_api(req: HttpRequest):
    """
    Devuelve un resumen del proyecto para el monitoreo.

    Estrategia híbrida:
      1) Si viene proyectoId y hay snapshot en ProyectoMonitoreo, se usa ESO.
      2) Si no hay snapshot (o no hay proyectoId), se leen las variables de caso
         de Bonita (proyectoNombre, descripcion, planTrabajo, compromisosAceptadosJson).

    Además, consulta la API backend para ver si hay alguna observación
    pendiente/rechazada sobre el proyecto.
    """
    case_id = (req.GET.get("case") or _json(req).get("caseId") or "").strip()
    proyecto_raw = req.GET.get("proyecto") or _json(req).get("proyectoId")

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId/case"}, status=400)

    # proyectoId es opcional
    try:
        proyecto_id = int(proyecto_raw) if proyecto_raw not in (None, "", []) else None
    except (TypeError, ValueError):
        proyecto_id = None

    try:
        cli = BonitaClient()
        cli.login()

        nombre = ""
        desc = ""
        etapas: list[dict[str, Any]] = []
        compromisos_detalle: list[dict[str, Any]] = []

        # 1) Intentar leer snapshot local si hay proyecto_id
        snap = None
        if proyecto_id is not None:
            try:
                snap = ProyectoMonitoreo.objects.get(proyecto_id=proyecto_id)
            except ProyectoMonitoreo.DoesNotExist:
                snap = None

        if snap is not None:
            nombre = snap.nombre or ""
            desc = snap.descripcion or ""

            plan = snap.plan_trabajo or {}
            if isinstance(plan, dict):
                etapas = plan.get("etapas") or []
                if not isinstance(etapas, list):
                    etapas = []
            else:
                etapas = []

            compromisos = snap.compromisos_aceptados or []
            if isinstance(compromisos, list):
                for x in compromisos:
                    if isinstance(x, dict):
                        cid = x.get("id")
                        try:
                            cid_int = int(cid)
                        except Exception:
                            cid_int = cid
                        compromisos_detalle.append(
                            {
                                "id": cid_int,
                                "detalle": x.get("detalle", ""),
                                "fecha": x.get("fecha", ""),
                                "estado": x.get("estado", ""),
                            }
                        )
                    else:
                        try:
                            cid_int = int(x)
                        except Exception:
                            cid_int = x
                        compromisos_detalle.append(
                            {
                                "id": cid_int,
                                "detalle": "",
                                "fecha": "",
                                "estado": "",
                            }
                        )

        # 2) Si NO hay snapshot, usar variables de Bonita (modo viejo)
        if snap is None:
            v_nombre = cli.get_case_variable(case_id, "proyectoNombre")
            if v_nombre and "value" in v_nombre:
                nombre = (v_nombre["value"] or "").strip()

            v_desc = cli.get_case_variable(case_id, "descripcion")
            if v_desc and "value" in v_desc:
                desc = (v_desc["value"] or "").strip()

            v_plan = cli.get_case_variable(case_id, "planTrabajo")
            if v_plan and "value" in v_plan and (v_plan["value"] or "").strip():
                try:
                    plan = json.loads(v_plan["value"])
                    if isinstance(plan, dict) and "etapas" in plan:
                        etapas = plan["etapas"]
                except Exception:
                    etapas = []

            v_hist = cli.get_case_variable(case_id, "compromisosAceptadosJson")
            if v_hist and "value" in v_hist:
                raw_hist = (v_hist["value"] or "").strip()
                if raw_hist:
                    try:
                        parsed = json.loads(raw_hist)
                    except Exception:
                        parsed = None

                    if isinstance(parsed, list):
                        if parsed and all(isinstance(x, dict) for x in parsed):
                            compromisos_detalle = [
                                {
                                    "id": int(x.get("id")) if str(x.get("id")).isdigit() else x.get("id"),
                                    "detalle": x.get("detalle", ""),
                                    "fecha": x.get("fecha", ""),
                                    "estado": x.get("estado", ""),
                                }
                                for x in parsed
                            ]
                        elif parsed and all(isinstance(x, (int, str)) for x in parsed):
                            for cid in parsed:
                                try:
                                    cid_int = int(cid)
                                except Exception:
                                    cid_int = cid
                                compromisos_detalle.append(
                                    {
                                        "id": cid_int,
                                        "detalle": "",
                                        "fecha": "",
                                        "estado": "",
                                    }
                                )

        # 3) Obtener token JWT de Bonita para consultar observaciones en tu backend
        jwt_token = ""
        var_access = cli.get_case_variable(case_id, "access")
        if var_access and "value" in var_access:
            jwt_token = (var_access["value"] or "").strip()

        # 4) Consultar API Backend para ver observaciones pendientes/rechazadas
        observacion_pendiente = None
        historial_observaciones = []
        if proyecto_id and jwt_token:
            try:
                api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
                url_obs = f"{api_base}/api/proyectos/{proyecto_id}/observaciones/"

                headers = {
                    "Authorization": f"Bearer {jwt_token}",
                    "Content-Type": "application/json",
                }

                resp_obs = requests.get(url_obs, headers=headers, timeout=5)
                if resp_obs.status_code == 200:
                    lista_obs = resp_obs.json()
                    
                    # Guardar el historial completo de observaciones
                    historial_observaciones = lista_obs
                    
                    # Buscar observaciones pendientes/rechazadas para bloqueo
                    pendientes = [
                        o for o in lista_obs
                        if o.get("estado") in ["pendiente", "rechazada"]
                    ]
                    if pendientes:
                        ultima = sorted(pendientes, key=lambda x: x.get("id", 0))[-1]
                        observacion_pendiente = {
                            "id": ultima.get("id"),
                            "texto": ultima.get("texto"),
                            "estado": ultima.get("estado"),
                            "fecha_vencimiento": ultima.get("fecha_vencimiento"),
                        }
            except Exception as e:
                # Loguear si querés, pero no romper la respuesta
                print(f"Error consultando observaciones al backend: {e}")

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "proyectoId": proyecto_id,
                "nombreProyecto": nombre,
                "descripcion": desc,
                "etapas": etapas,
                "compromisosAceptados": compromisos_detalle,
                "observacionPendiente": observacion_pendiente,
                "historialObservaciones": historial_observaciones,
            },
            status=200,
        )

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error consultando datos de monitoreo / Bonita", "detail": str(e)},
            status=500,
        )



@csrf_exempt
def responder_observacion_bonita_api(req: HttpRequest):
    """
    Ejecuta la tarea 'Monitorear ejecución' con accion='RESPONDER'.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    obs_id = data.get("observacionId")
    respuesta = str(data.get("respuesta") or "").strip()

    if not case_id or not obs_id or not respuesta:
        return JsonResponse({"ok": False, "error": "Datos incompletos"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()
        
        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)

        # Buscar tarea "Monitorear ejecución"
        task = cli.wait_ready_task_in_case(case_id, "Monitorear ejecución / transparencia", timeout_sec=5)
        
        if not task:
             return JsonResponse({"ok": False, "error": "La tarea de monitoreo no está lista."}, status=409)

        # Ejecutar con acción RESPONDER
        cli.assign_task(task["id"], user_id)
        contract = {
            "accion": "RESPONDER",
            "observacionId": int(obs_id),
            "respuesta": respuesta
        }
        cli.execute_task(task["id"], contract)

        return JsonResponse({"ok": True, "caseId": case_id})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)