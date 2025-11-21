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
from .models import ProyectoMonitoreo, SesionBonita  # <--- AGREGADO SesionBonita


# --------------------------- Helpers ---------------------------

def _marcar_observaciones_vencidas_si_aplica(proyecto_id: int, jwt_token: str) -> None:
    """
    Verifica y marca como vencidas las observaciones pendientes 
    que hayan superado los 5 días.
    """
    try:
        api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
        url = f"{api_base}/api/admin/observaciones/vencidas/"

        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }

        requests.post(url, headers=headers, timeout=5)
    except Exception:
        pass


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


def dashboard_page(req: HttpRequest):
    """
    Página del tablero gerencial para el Consejo Directivo.
    Muestra métricas consolidadas del sistema.
    """
    return render(req, "bonita/dashboard.html")


def consejo_page(req: HttpRequest):
    api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
    ctx = {
        "case": req.GET.get("case", ""),
        "API_BASE_URL": api_base
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
    2. Verifica si el usuario ya tiene un caso activo en Bonita.
    3. Si tiene caso activo y el caso existe en Bonita, lo retoma.
    4. Si no tiene caso o el caso está cerrado, crea uno nuevo.
    5. Devuelve el caseId.
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

        # Determinar proceso según el flag "consejo"
        if is_consejo:
            proc_name = getattr(settings, "BONITA_PROCESS_NAME_CONSEJO", "Consejo Directivo")
            proc_version = getattr(settings, "BONITA_PROCESS_VERSION_CONSEJO", "1.0")
        else:
            proc_name = getattr(settings, "BONITA_PROCESS_NAME", "ProjectPlanning")
            proc_version = getattr(settings, "BONITA_PROCESS_VERSION", "1.0")

        # 1. Verificar si el usuario ya tiene una sesión activa PARA ESE PROCESO
        sesion = SesionBonita.objects.filter(
            api_username=api_user,
            proceso=proc_name
        ).first()

        case_id = None
        caso_existente = False

        if sesion:
            # Verificar si el caso existe y está activo en Bonita
            try:
                case_info = cli.get_case(sesion.case_id)
                if case_info and case_info.get("state") != "completed":
                    # El caso existe y está activo, lo retomamos
                    case_id = sesion.case_id
                    caso_existente = True
                else:
                    # El caso está completado o no sirve, eliminamos la sesión
                    sesion.delete()
            except Exception:
                # El caso no existe en Bonita, eliminamos la sesión
                sesion.delete()

        # 2. Si no hay caso activo, crear uno nuevo
        if not case_id:
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

            # Guardar la sesión en la base de datos
            # 🔴 IMPORTANTE: lookup SOLO por api_username, porque es unique=True.
            # Así garantizamos UNA fila por usuario y vamos pisando proceso/case según lo último.
            SesionBonita.objects.update_or_create(
                api_username=api_user,
                defaults={
                    "case_id": case_id,
                    "proceso": proc_name,
                },
            )

        return JsonResponse({
            "ok": True,
            "caseId": case_id,
            "casoExistente": caso_existente
        }, status=200)

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Fallo integración Bonita", "detail": str(e)},
            status=500,
        )


@csrf_exempt
def next_step_api(req: HttpRequest):
    """
    Dado un caseId, busca la tarea pendiente (ready) en Bonita
    y decide a qué pantalla debe ir el usuario.
    
    Si no hay tarea ready, intenta determinar el estado del caso
    a partir de las variables para redirigir correctamente.

    Si no se puede determinar un rol válido (rol = 'desconocido'),
    devuelve 403 y NO redirige a ninguna página.
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

        proyecto_id = None
        pedido_id = None
        rol_usuario = None

        # Leer proyectoId
        try:
            var_proyecto = cli.get_case_variable(case_id, "proyectoId")
            if var_proyecto and "value" in var_proyecto:
                val = var_proyecto["value"]
                if val is not None and str(val).strip() and str(val).lower() != "null":
                    proyecto_id = str(val).strip()
        except Exception:
            pass

        # Leer pedidoId
        try:
            var_pedido = cli.get_case_variable(case_id, "pedidoId")
            if var_pedido and "value" in var_pedido:
                val = var_pedido["value"]
                if val is not None and str(val).strip() and str(val).lower() != "null":
                    pedido_id = str(val).strip()
        except Exception:
            pass

        # Leer rol
        try:
            var_rol = cli.get_case_variable(case_id, "rol")
            if var_rol and "value" in var_rol:
                val = var_rol["value"]
                if val:
                    rol_usuario = str(val).strip()
        except Exception:
            pass

        # Aca probamos un poco más de tiempo para que aparezca la primera tarea
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name=None,
            timeout_sec=8,  # antes 3
        )

        name = ""
        rol = "desconocido"
        url = f"/bonita/home/?case={case_id}"

        if task:
            name = (task.get("name") or task.get("displayName") or "").strip()

            if name == "Definir plan de trabajo y economico":
                rol = "ong_originante"
                url = f"/bonita/nuevo/?case={case_id}"

            elif name == "Revisar proyectos":
                rol = "red_ongs"
                url = f"/bonita/revisar/?case={case_id}"

            elif name == "Registrar pedido":
                rol = "ong_originante"
                if proyecto_id:
                    url = f"/bonita/pedido/?case={case_id}&proyecto={proyecto_id}"
                else:
                    url = f"/bonita/pedido/?case={case_id}"

            elif name == "Revisar pedidos":
                rol = "red_ongs"
                if proyecto_id:
                    url = f"/bonita/ver-pedidos/?case={case_id}&proyecto={proyecto_id}"
                else:
                    url = f"/bonita/ver-pedidos/?case={case_id}"

            elif name == "Registrar compromiso":
                rol = "red_ongs"
                if proyecto_id and pedido_id:
                    url = f"/bonita/compromiso/?case={case_id}&proyecto={proyecto_id}&pedido={pedido_id}&rol=red_ongs"
                elif proyecto_id:
                    url = f"/bonita/compromiso/?case={case_id}&proyecto={proyecto_id}&rol=red_ongs"
                else:
                    url = f"/bonita/compromiso/?case={case_id}&rol=red_ongs"

            elif name == "Evaluar propuestas":
                rol = "ong_originante"
                if proyecto_id:
                    url = f"/bonita/evaluar/?case={case_id}&proyecto={proyecto_id}"
                else:
                    url = f"/bonita/evaluar/?case={case_id}"

            elif name == "Monitorear ejecución / transparencia":
                rol = "ong_originante"
                if proyecto_id:
                    url = f"/bonita/monitoreo/?case={case_id}&proyecto={proyecto_id}"
                else:
                    url = f"/bonita/monitoreo/?case={case_id}"

            elif name == "Revisar proyecto y cargar observaciones":
                rol = "consejo_directivo"
                url = f"/bonita/consejo/?case={case_id}"

            elif name == "Evaluar Respuestas":
                rol = "consejo_directivo"
                url = f"/bonita/consejo/evaluar/?case={case_id}"

            elif name == "Resolver observaciones":
                rol = "ong_originante"
                if proyecto_id:
                    url = f"/bonita/monitoreo/?case={case_id}&proyecto={proyecto_id}"
                else:
                    url = f"/bonita/monitoreo/?case={case_id}"
        else:
            name = "Sin tarea ready - inferido por variables"

            if proyecto_id:
                # Si ya hay proyecto, misma lógica que tenías
                if rol_usuario:
                    rol_lower = rol_usuario.lower()
                    if "originante" in rol_lower:
                        rol = "ong_originante"
                        url = f"/bonita/monitoreo/?case={case_id}&proyecto={proyecto_id}"
                        name = "Monitoreo (inferido - ONG Originante)"
                    elif "red" in rol_lower or "ongs" in rol_lower:
                        rol = "red_ongs"
                        url = f"/bonita/revisar/?case={case_id}"
                        name = "Revisar proyectos (inferido - Red ONGs)"
                    elif "consejo" in rol_lower:
                        rol = "consejo_directivo"
                        url = f"/bonita/consejo/?case={case_id}"
                        name = "Consejo (inferido - Consejo Directivo)"
                else:
                    rol = "ong_originante"
                    url = f"/bonita/monitoreo/?case={case_id}&proyecto={proyecto_id}"
                    name = "Monitoreo (inferido por proyecto)"
            else:
                # Caso conflictivo: proceso recién creado, sin proyectoId
                # Solo lo mandamos a algo concreto si Bonita YA marcó un rol claro
                if rol_usuario:
                    rol_lower = rol_usuario.lower()
                    if "originante" in rol_lower:
                        rol = "ong_originante"
                        url = f"/bonita/nuevo/?case={case_id}&rol=ong_originante"
                        name = "Definir plan de trabajo y económico (inferido)"
                    elif "red" in rol_lower or "ongs" in rol_lower:
                        rol = "red_ongs"
                        url = f"/bonita/revisar/?case={case_id}&rol=red_ongs"
                        name = "Revisar proyectos (inferido - Red ONGs)"
                    elif "consejo" in rol_lower:
                        rol = "consejo_directivo"
                        url = f"/bonita/consejo/?case={case_id}"
                        name = "Consejo (inferido - Consejo Directivo)"
                    else:
                        # Rol raro → devolvemos 403, no mandamos al home
                        return JsonResponse(
                            {
                                "ok": False,
                                "caseId": case_id,
                                "tarea": name,
                                "rol": "desconocido",
                                "proyectoId": proyecto_id,
                                "pedidoId": pedido_id,
                                "error": "Credenciales incorrectas",
                            },
                            status=403,
                        )
                else:
                    # Sin rol y sin proyecto: caso recién creado, no se sabe nada todavía
                    # Devolvemos 403 para que el frontend NO redirija a ningún lado.
                    return JsonResponse(
                        {
                            "ok": False,
                            "caseId": case_id,
                            "tarea": name,
                            "rol": "desconocido",
                            "proyectoId": proyecto_id,
                            "pedidoId": pedido_id,
                            "error": "No se pudo determinar tu rol todavía. Volvé a intentar el login.",
                        },
                        status=403,
                    )

        # Guardrail final: si por cualquier razón seguimos con rol desconocido,
        # NO devolvemos URL, devolvemos 403.
        if rol == "desconocido":
            return JsonResponse(
                {
                    "ok": False,
                    "caseId": case_id,
                    "tarea": name,
                    "rol": rol,
                    "proyectoId": proyecto_id,
                    "pedidoId": pedido_id,
                    "error": "No se pudo determinar tu rol. Reintentá el login.",
                },
                status=403,
            )

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "tarea": name,
                "rol": rol,
                "url": url,
                "proyectoId": proyecto_id,
                "pedidoId": pedido_id,
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
def debug_case_variables_api(req: HttpRequest):
    """
    Endpoint de debug para ver todas las variables de un caso.
    """
    case_id = (req.GET.get("case") or _json(req).get("caseId") or "").strip()
    if not case_id:
        return JsonResponse({"error": "Falta caseId"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        # Listar todas las variables del caso
        r = cli.s.get(
            f"{cli.api}/bpm/caseVariable",
            params=[("p", "0"), ("c", "100"), ("f", f"case_id={case_id}")],
            headers=cli._h(),
            timeout=cli._timeout,
        )
        r.raise_for_status()
        variables = r.json() if r.text else []

        # También obtener info del caso
        case_info = cli.get_case(case_id)

        return JsonResponse({
            "ok": True,
            "caseId": case_id,
            "caseInfo": case_info,
            "variables": variables,
            "totalVariables": len(variables)
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


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
            "pedidoTipo": pedido_tipo,
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

    Ahora además setea seguirColaborando=True, porque al elegir un proyecto
    la Red de ONGs decide seguir colaborando.

    Si la tarea 'Revisar proyectos' YA NO está ready (porque el flujo ya avanzó),
    se considera OK igual.
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

        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Revisar proyectos",
            timeout_sec=15,
        )

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

        cli.assign_task(task["id"], user_id)
        contract_payload = {
            "proyectoSeleccionadoId": proyecto_id_int,
            "seguirColaborando": True,  # NUEVO: sigue colaborando
        }
        cli.execute_task(task["id"], contract_payload)

        # Guardar explícitamente el proyectoId en la variable del caso
        try:
            cli.update_case_variable(case_id, "proyectoId", str(proyecto_id_int))
        except Exception:
            pass

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
        "compromisoDetalle": "...",
        "seguirColaborando": true/false
      }
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    comp_tipo = str(data.get("compromisoTipo") or "").strip()
    comp_detalle = str(data.get("compromisoDetalle") or "").strip()
    pedido_raw = data.get("pedidoId")
    seguir_raw = data.get("seguirColaborando", True)

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

    def _to_bool(v) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "t", "yes", "y", "si", "sí")
        if isinstance(v, (int, float)):
            return bool(v)
        return False

    seguir_colaborando = _to_bool(seguir_raw)

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

        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "compromisoTipo": comp_tipo,
            "compromisoDetalle": comp_detalle,
            "pedidoId": pedido_id,
            "seguirColaborando": seguir_colaborando,  # NUEVO
        }
        cli.execute_task(task["id"], payload_contrato)

        # (lo demás igual que antes)
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


# --------------------------- Helpers para observaciones ---------------------------

def calcular_limite_manual(proyecto: dict) -> dict:
    """
    Calcula el límite de observaciones basándose en el total de observaciones del proyecto.
    Esto es un fallback cuando no se puede consultar el endpoint de la API Django.
    
    IMPORTANTE: Este método cuenta TODAS las observaciones originales del proyecto,
    sin importar su estado. Las observaciones rechazadas no se cuentan como adicionales
    porque son la misma observación que vuelve a la ONG.
    
    El serializer ProyectoConObservacionesOut devuelve:
    - observaciones_pendientes
    - observaciones_rechazadas  
    - observaciones_respondidas
    - observaciones_vencidas
    - total_observaciones
    
    NOTA: Las observaciones aprobadas ya están incluidas en alguno de los contadores o en total.
    """
    # El campo total_observaciones es el más confiable si existe
    total_obs = proyecto.get("total_observaciones", 0)

    # Si no existe, sumar los contadores individuales
    if total_obs == 0:
        total_obs = (
                (proyecto.get("observaciones_pendientes") or 0) +
                (proyecto.get("observaciones_rechazadas") or 0) +
                (proyecto.get("observaciones_respondidas") or 0) +
                (proyecto.get("observaciones_vencidas") or 0)
        )

    # Si tiene 2 o más observaciones, límite alcanzado
    puede_observar = total_obs < 2

    return {
        "puede_observar": puede_observar,
        "observaciones_realizadas": total_obs,
        "mensaje": f"Se han realizado {total_obs} de 2 observaciones permitidas este mes" if total_obs < 2
        else f"Ya se realizaron {total_obs} de 2 observaciones permitidas este mes",
        "fecha_reset": None  # No podemos calcular esto sin acceso a la BD
    }


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

        # Enriquecer cada proyecto con información del límite mensual
        # Obtener token JWT del caso para consultar límites
        var_access = cli.get_case_variable(case_id, "access")
        jwt_token = None
        if var_access and "value" in var_access:
            jwt_token = var_access["value"]

        if jwt_token and isinstance(proyectos, list):
            api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
            for proyecto in proyectos:
                try:
                    proyecto_id = proyecto.get("id")
                    if proyecto_id:
                        res_limite = requests.get(
                            f"{api_base}/api/proyectos/{proyecto_id}/observaciones/limite/",
                            headers={
                                "Authorization": f"Bearer {jwt_token}",
                                "Content-Type": "application/json"
                            },
                            timeout=5
                        )
                        if res_limite.status_code == 200:
                            limite_info = res_limite.json()
                            proyecto["limite_observaciones"] = limite_info
                        else:
                            # Endpoint no implementado o error - usar cálculo manual (fallback)
                            if res_limite.status_code == 404:
                                print(
                                    f"Info: Endpoint de límite no implementado para proyecto {proyecto_id}, usando cálculo manual")
                            else:
                                print(
                                    f"Advertencia: Error obteniendo límite para proyecto {proyecto_id}: Status {res_limite.status_code}")
                            proyecto["limite_observaciones"] = calcular_limite_manual(proyecto)
                except Exception as e:
                    print(f"Excepción obteniendo límite para proyecto {proyecto.get('id')}: {e}")
                    # Si falla la consulta, calcular manualmente basado en el total de observaciones
                    proyecto["limite_observaciones"] = calcular_limite_manual(proyecto)
        elif isinstance(proyectos, list):
            # Si no hay token, calcular manualmente para todos
            for proyecto in proyectos:
                proyecto["limite_observaciones"] = calcular_limite_manual(proyecto)

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
    continuar_revisando = data.get("continuarRevisando", False)

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

        # Obtener el token JWT del caso para verificar el límite
        var_access = cli.get_case_variable(case_id, "access")
        jwt_token = None
        if var_access and "value" in var_access:
            jwt_token = var_access["value"]

        # Verificar límite mensual antes de ejecutar la tarea
        if jwt_token:
            api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
            try:
                res_limite = requests.get(
                    f"{api_base}/api/proyectos/{proyecto_id}/observaciones/limite/",
                    headers={
                        "Authorization": f"Bearer {jwt_token}",
                        "Content-Type": "application/json"
                    },
                    timeout=10
                )

                if res_limite.status_code == 200:
                    limite_info = res_limite.json()
                    if not limite_info.get("puede_observar", True):
                        # Límite alcanzado
                        return JsonResponse(
                            {
                                "ok": False,
                                "error": "Límite de observaciones mensuales alcanzado",
                                "detail": limite_info.get("mensaje",
                                                          "Ya se alcanzó el límite de 2 observaciones este mes"),
                                "observaciones_realizadas": limite_info.get("observaciones_realizadas", 2),
                                "fecha_reset": limite_info.get("fecha_reset")
                            },
                            status=429,
                        )
            except Exception as e:
                # Si falla la verificación, continuamos (el conector hará la validación)
                print(f"Advertencia: No se pudo verificar límite de observaciones: {e}")

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
            "continuarRevisando": continuar_revisando,
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

        # Verificar si el conector devolvió un error 429 (límite alcanzado)
        if status_code == 429:
            error_msg = "Límite de observaciones mensuales alcanzado"
            error_detail = "Ya se alcanzó el límite de 2 observaciones este mes"
            obs_realizadas = 2
            fecha_reset = None

            if body_observacion and isinstance(body_observacion, dict):
                error_detail = body_observacion.get("detail", error_detail)
                obs_realizadas = body_observacion.get("observaciones_realizadas", 2)
                fecha_reset = body_observacion.get("fecha_reset")

            return JsonResponse(
                {
                    "ok": False,
                    "error": error_msg,
                    "detail": error_detail,
                    "observaciones_realizadas": obs_realizadas,
                    "fecha_reset": fecha_reset
                },
                status=429,
            )

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
def cerrar_sesion_consejo_api(req: HttpRequest):
    """
    Cierra la sesión del Consejo Directivo finalizando el proceso.
    
    Este endpoint busca la tarea "Revisar proyecto y cargar observaciones" 
    y la ejecuta con continuarRevisando=false para terminar el proceso.
    
    Si no hay tarea pendiente, devuelve ok=True (el proceso ya terminó).
    
    Espera en POST:
      - caseId: ID del caso en Bonita
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

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)
        if not user_id:
            return JsonResponse(
                {"ok": False, "error": "Usuario Bonita no encontrado", "detail": assignee_username},
                status=500,
            )

        # Buscar tarea "Revisar proyecto y cargar observaciones"
        # Si no existe, el proceso ya terminó o no hay tarea pendiente
        task = cli.wait_ready_task_in_case(
            case_id,
            task_name="Revisar proyecto y cargar observaciones",
            timeout_sec=5,
        )

        if not task:
            # No hay tarea pendiente, considerar que ya terminó
            return JsonResponse(
                {
                    "ok": True,
                    "caseId": case_id,
                    "mensaje": "No hay tareas pendientes, sesión ya finalizada."
                },
                status=200,
            )

        # Ejecutar la tarea con continuarRevisando=false y datos mínimos
        # para que el proceso vaya al Fin
        cli.assign_task(task["id"], user_id)
        payload_contrato = {
            "proyectoId": 0,  # Valor dummy, no importa porque no se usará
            "observaciones": "Sesión cerrada por el usuario",  # Texto dummy
            "continuarRevisando": False,
        }
        cli.execute_task(task["id"], payload_contrato)

        return JsonResponse(
            {
                "ok": True,
                "caseId": case_id,
                "mensaje": "Sesión cerrada correctamente"
            },
            status=200,
        )

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": "Error cerrando sesión", "detail": str(e)},
            status=500,
        )


@csrf_exempt
def dashboard_datos_api(req: HttpRequest):
    """
    Endpoint para obtener métricas del dashboard gerencial.

    Consulta:
    1. API Django (/api/dashboard/metricas/) para todos los datos consolidados
    2. API REST de Bonita para casos activos
    3. Base de datos local para sesiones
    """
    if req.method != "GET":
        return JsonResponse({"error": "GET only"}, status=405)

    try:
        from bonita.models import SesionBonita
        from decimal import Decimal
        from datetime import datetime

        api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")

        # ========================================
        # 1. CONSULTAR API DJANGO (ENDPOINT CONSOLIDADO)
        # ========================================

        proyectos = []
        pedidos = []
        compromisos = []
        observaciones = []

        try:
            res_metricas = requests.get(f"{api_base}/api/dashboard/metricas/", timeout=10)
            if res_metricas.ok:
                data = res_metricas.json()
                proyectos = data.get('proyectos', [])
                pedidos = data.get('pedidos', [])
                compromisos = data.get('compromisos', [])
                observaciones = data.get('observaciones', [])
            else:
                print(f"Error consultando API Django: {res_metricas.status_code} - {res_metricas.text}")
        except Exception as e:
            print(f"Error consultando API Django: {e}")

        # ========================================
        # 2. CALCULAR MÉTRICAS DE PROYECTOS
        # ========================================

        total_proyectos = len(proyectos)
        proyectos_planificacion = sum(1 for p in proyectos if p.get('estado') == 'planificacion')
        proyectos_ejecucion = sum(1 for p in proyectos if p.get('estado') == 'ejecucion')
        proyectos_finalizados = sum(1 for p in proyectos if p.get('estado') == 'finalizado')

        # ========================================
        # 3. CALCULAR MÉTRICAS DE PEDIDOS
        # ========================================

        total_pedidos = len(pedidos)
        pedidos_abiertos = sum(1 for p in pedidos if p.get('estado') == 'abierto')

        # ========================================
        # 4. CALCULAR MÉTRICAS DE COMPROMISOS
        # ========================================

        total_compromisos = len(compromisos)
        compromisos_cumplidos = sum(1 for c in compromisos if c.get('estado') == 'cumplido')

        # Calcular montos
        monto_total = Decimal('0')
        for c in compromisos:
            monto = c.get('monto')
            if monto:
                try:
                    monto_total += Decimal(str(monto))
                except:
                    pass

        monto_promedio = monto_total / total_compromisos if total_compromisos > 0 else Decimal('0')

        # ========================================
        # 5. CALCULAR MÉTRICAS DE OBSERVACIONES
        # ========================================

        total_observaciones = len(observaciones)
        observaciones_pendientes = sum(1 for o in observaciones if o.get('estado') == 'pendiente')
        observaciones_respondidas = sum(1 for o in observaciones if o.get('estado') == 'respondida')
        observaciones_aprobadas = sum(1 for o in observaciones if o.get('estado') == 'aprobada')
        observaciones_rechazadas = sum(1 for o in observaciones if o.get('estado') == 'rechazada')
        observaciones_vencidas = sum(1 for o in observaciones if o.get('estado') == 'vencida')

        # ========================================
        # 6. CONSULTAR API REST DE BONITA
        # ========================================

        casos_activos = 0
        casos_ong = 0
        casos_consejo = 0

        try:
            # Autenticar en Bonita
            bonita_base = settings.BONITA_BASE_URL
            login_url = f"{bonita_base}/loginservice"

            bonita_session = requests.Session()
            login_data = {
                "username": settings.BONITA_USER,
                "password": settings.BONITA_PASSWORD,
                "redirect": "false"
            }

            login_resp = bonita_session.post(login_url, data=login_data, timeout=5)

            if login_resp.ok:
                # Consultar casos activos
                cases_url = f"{bonita_base}/API/bpm/case"
                params = {"f": "state=started", "p": 0, "c": 100}
                cases_resp = bonita_session.get(cases_url, params=params, timeout=5)

                if cases_resp.ok:
                    cases = cases_resp.json()
                    casos_activos = len(cases)

                    # Crear cache de nombres de procesos
                    process_names_cache = {}

                    # Contar casos por proceso
                    for case in cases:
                        process_def_id = case.get('processDefinitionId', '')
                        if not process_def_id:
                            continue

                        # Obtener nombre del proceso desde cache o consultarlo
                        if process_def_id not in process_names_cache:
                            try:
                                process_url = f"{bonita_base}/API/bpm/process/{process_def_id}"
                                process_resp = bonita_session.get(process_url, timeout=3)
                                if process_resp.ok:
                                    process_data = process_resp.json()
                                    process_names_cache[process_def_id] = process_data.get('name', '')
                                else:
                                    process_names_cache[process_def_id] = ''
                            except Exception:
                                process_names_cache[process_def_id] = ''

                        process_name = process_names_cache[process_def_id]
                        if 'ProjectPlanning' in process_name:
                            casos_ong += 1
                        elif 'Consejo' in process_name:
                            casos_consejo += 1

        except Exception as e:
            print(f"Error consultando Bonita: {e}")

        # Sesiones locales
        sesiones_activas = SesionBonita.objects.count()
        sesiones_consejo_local = SesionBonita.objects.filter(proceso="Consejo Directivo").count()
        sesiones_ongs_local = SesionBonita.objects.filter(proceso="ProjectPlanning").count()

        # ========================================
        # 7. TOP PROYECTOS CON MÁS OBSERVACIONES
        # ========================================

        proyectos_obs_count = {}
        for obs in observaciones:
            proyecto_id = obs.get('proyecto_id')
            if proyecto_id:
                proyectos_obs_count[proyecto_id] = proyectos_obs_count.get(proyecto_id, 0) + 1

        # Crear diccionario de proyectos para búsqueda rápida
        proyectos_dict = {p['id']: p for p in proyectos}

        top_proyectos_obs = []
        for proyecto_id, count in sorted(proyectos_obs_count.items(), key=lambda x: x[1], reverse=True)[:5]:
            proyecto = proyectos_dict.get(proyecto_id)
            if proyecto:
                top_proyectos_obs.append({
                    "id": proyecto_id,
                    "nombre": proyecto.get('nombre', 'Sin nombre'),
                    "estado": proyecto.get('estado', 'desconocido'),
                    "total_observaciones": count
                })

        # ========================================
        # 8. TOP PROYECTOS CON MÁS COMPROMISOS
        # ========================================

        proyectos_comp_count = {}
        proyectos_pedidos_count = {}

        # Crear diccionario de pedidos para búsqueda rápida
        pedidos_dict = {p['id']: p for p in pedidos}

        # Contar pedidos por proyecto
        for pedido in pedidos:
            proyecto_id = pedido.get('proyecto')
            if proyecto_id:
                proyectos_pedidos_count[proyecto_id] = proyectos_pedidos_count.get(proyecto_id, 0) + 1

        # Contar compromisos por proyecto (a través de pedidos)
        for compromiso in compromisos:
            pedido_id = compromiso.get('pedidoId')
            if pedido_id:
                pedido = pedidos_dict.get(pedido_id)
                if pedido:
                    proyecto_id = pedido.get('proyecto')
                    if proyecto_id:
                        proyectos_comp_count[proyecto_id] = proyectos_comp_count.get(proyecto_id, 0) + 1

        top_proyectos_comp = []
        for proyecto_id, count in sorted(proyectos_comp_count.items(), key=lambda x: x[1], reverse=True)[:5]:
            proyecto = proyectos_dict.get(proyecto_id)
            if proyecto:
                top_proyectos_comp.append({
                    "id": proyecto_id,
                    "nombre": proyecto.get('nombre', 'Sin nombre'),
                    "total_pedidos": proyectos_pedidos_count.get(proyecto_id, 0),
                    "total_compromisos": count
                })

        # ========================================
        # 9. OBSERVACIONES RECIENTES (últimas 10)
        # ========================================

        observaciones_recientes = []
        # Ordenar por fecha de creación (más recientes primero)
        observaciones_ordenadas = sorted(
            observaciones,
            key=lambda x: x.get('fecha_creacion', ''),
            reverse=True
        )[:10]

        for obs in observaciones_ordenadas:
            proyecto_id = obs.get('proyecto_id')
            proyecto = proyectos_dict.get(proyecto_id)

            # Calcular días restantes
            dias_restantes = None
            fecha_vencimiento = obs.get('fecha_vencimiento')
            if fecha_vencimiento:
                try:
                    fecha_venc = datetime.fromisoformat(fecha_vencimiento.replace('Z', '+00:00'))
                    dias_restantes = (fecha_venc - datetime.now(fecha_venc.tzinfo)).days
                except:
                    pass

            observaciones_recientes.append({
                "id": obs.get('id'),
                "proyecto_nombre": proyecto.get('nombre', 'Desconocido') if proyecto else 'Desconocido',
                "estado": obs.get('estado', 'desconocido'),
                "fecha_creacion": obs.get('fecha_creacion'),
                "dias_restantes": dias_restantes
            })

        # ========================================
        # 10. CONSTRUIR RESPUESTA
        # ========================================

        metricas = {
            # Proyectos
            "total_proyectos": total_proyectos,
            "proyectos_planificacion": proyectos_planificacion,
            "proyectos_ejecucion": proyectos_ejecucion,
            "proyectos_finalizados": proyectos_finalizados,

            # Pedidos
            "total_pedidos": total_pedidos,
            "pedidos_abiertos": pedidos_abiertos,

            # Compromisos
            "total_compromisos": total_compromisos,
            "compromisos_cumplidos": compromisos_cumplidos,
            "monto_total_compromisos": float(monto_total),
            "monto_promedio_compromiso": float(monto_promedio),

            # Observaciones
            "total_observaciones": total_observaciones,
            "observaciones_pendientes": observaciones_pendientes,
            "observaciones_respondidas": observaciones_respondidas,
            "observaciones_aprobadas": observaciones_aprobadas,
            "observaciones_rechazadas": observaciones_rechazadas,
            "observaciones_vencidas": observaciones_vencidas,

            # Bonita
            "casos_activos_bonita": casos_activos,
            "casos_ong_bonita": casos_ong,
            "casos_consejo_bonita": casos_consejo,

            # Sesiones locales
            "sesiones_activas": sesiones_activas,
            "sesiones_consejo": sesiones_consejo_local,
            "sesiones_ongs": sesiones_ongs_local,
        }

        return JsonResponse({
            "ok": True,
            "data": {
                "metricas": metricas,
                "top_proyectos_observaciones": top_proyectos_obs,
                "top_proyectos_compromisos": top_proyectos_comp,
                "observaciones_recientes": observaciones_recientes
            }
        }, status=200)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse(
            {"ok": False, "error": "Error obteniendo datos del dashboard", "detail": str(e)},
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

        # Marcar observaciones vencidas antes de consultar
        _marcar_observaciones_vencidas_si_aplica(proyecto_id, jwt_token)

        # Construir URL de la API
        api_base_url = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
        url = f"{api_base_url}/api/proyectos/{proyecto_id}/observaciones/"

        # Hacer request a la API JWT
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 401:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Token expirado",
                    "detail": "La sesión ha expirado. Por favor, inicie sesión nuevamente.",
                    "needsLogin": True
                },
                status=401,
            )
        elif response.status_code == 200:
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
                # Primero marcar las vencidas si aplica
                _marcar_observaciones_vencidas_si_aplica(proyecto_id, jwt_token)

                api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
                url_obs = f"{api_base}/api/proyectos/{proyecto_id}/observaciones/"

                headers = {
                    "Authorization": f"Bearer {jwt_token}",
                    "Content-Type": "application/json",
                }

                resp_obs = requests.get(url_obs, headers=headers, timeout=5)
                if resp_obs.status_code == 401:
                    # Token expirado - informar al usuario que debe hacer login nuevamente
                    return JsonResponse({
                        "ok": False,
                        "error": "Token expirado",
                        "detail": "La sesión ha expirado. Por favor, inicie sesión nuevamente.",
                        "needsLogin": True
                    }, status=401)
                if resp_obs.status_code == 200:
                    lista_obs = resp_obs.json()

                    # Guardar el historial completo de observaciones
                    historial_observaciones = lista_obs

                    # Buscar observaciones pendientes/rechazadas/vencidas para mostrar
                    pendientes = [
                        o for o in lista_obs
                        if o.get("estado") in ["pendiente", "rechazada", "vencida"]
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
    Primero verifica en la API si la observación aún está pendiente y no venció.
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

        # Obtener token para verificar estado de la observación
        var_access = cli.get_case_variable(case_id, "access")
        if var_access and "value" in var_access:
            jwt_token = (var_access["value"] or "").strip()

            # Verificar el estado actual de la observación
            try:
                var_proyecto = cli.get_case_variable(case_id, "proyectoId")
                if var_proyecto and "value" in var_proyecto:
                    proyecto_id = var_proyecto["value"]

                    # Marcar vencidas primero
                    _marcar_observaciones_vencidas_si_aplica(proyecto_id, jwt_token)

                    # Consultar el estado actual
                    api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
                    url_obs = f"{api_base}/api/proyectos/{proyecto_id}/observaciones/"

                    headers = {
                        "Authorization": f"Bearer {jwt_token}",
                        "Content-Type": "application/json",
                    }

                    resp = requests.get(url_obs, headers=headers, timeout=3)
                    if resp.status_code == 200:
                        observaciones = resp.json()
                        obs_actual = next((o for o in observaciones if o.get("id") == int(obs_id)), None)

                        if obs_actual and obs_actual.get("estado") == "vencida":
                            return JsonResponse({
                                "ok": False,
                                "error": "Esta observación ya venció. No se puede responder."
                            }, status=400)
            except Exception:
                pass  # Si falla la verificación, continuar igual

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


@csrf_exempt
def finalizar_proyecto_api(req: HttpRequest):
    """
    Finaliza un proyecto cambiando su estado a 'finalizado' en la API backend
    y ejecuta la tarea 'Monitorear ejecución' con accion='FINALIZAR'.
    
    Solo permite finalizar si no hay observaciones pendientes, rechazadas o respondidas.
    """
    if req.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = _json(req)
    case_id = str(data.get("caseId") or "").strip()
    proyecto_id_raw = data.get("proyectoId")

    if not case_id:
        return JsonResponse({"ok": False, "error": "Falta caseId"}, status=400)
    if proyecto_id_raw in (None, "", []):
        return JsonResponse({"ok": False, "error": "Falta proyectoId"}, status=400)

    try:
        proyecto_id = int(proyecto_id_raw)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "proyectoId debe ser entero"}, status=400)

    try:
        cli = BonitaClient()
        cli.login()

        # Obtener token JWT para verificar observaciones
        var_access = cli.get_case_variable(case_id, "access")
        if not var_access or "value" not in var_access:
            return JsonResponse({"ok": False, "error": "No se encontró token de autenticación"}, status=401)

        jwt_token = (var_access["value"] or "").strip()

        # Verificar que no haya observaciones problemáticas
        api_base = getattr(settings, "API_BASE_URL", "http://127.0.0.1:8000")
        url_obs = f"{api_base}/api/proyectos/{proyecto_id}/observaciones/"

        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }

        try:
            resp_obs = requests.get(url_obs, headers=headers, timeout=5)
            if resp_obs.status_code == 200:
                lista_obs = resp_obs.json()
                observaciones_problematicas = [
                    o for o in lista_obs
                    if o.get("estado") in ["pendiente", "rechazada", "respondida"]
                ]

                if observaciones_problematicas:
                    return JsonResponse({
                        "ok": False,
                        "error": "No se puede finalizar el proyecto. Hay observaciones pendientes de resolución.",
                        "observacionesPendientes": len(observaciones_problematicas)
                    }, status=400)
        except requests.RequestException as e:
            print(f"Error consultando observaciones: {e}")
            # Continuar de todas formas si no podemos verificar

        # Cambiar estado del proyecto a 'finalizado' en la API
        url_cambiar_estado = f"{api_base}/api/proyectos/{proyecto_id}/estado/"
        payload_estado = {"estado": "finalizado"}

        try:
            resp_estado = requests.post(url_cambiar_estado, headers=headers, json=payload_estado, timeout=5)

            if resp_estado.status_code not in [200, 201]:
                return JsonResponse({
                    "ok": False,
                    "error": "Error al cambiar el estado del proyecto en la API",
                    "statusCode": resp_estado.status_code,
                    "detail": resp_estado.text
                }, status=500)
        except requests.RequestException as e:
            return JsonResponse({
                "ok": False,
                "error": "Error de red al cambiar estado del proyecto",
                "detail": str(e)
            }, status=500)

        # Ejecutar tarea en Bonita con accion='FINALIZAR'
        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)

        if not user_id:
            return JsonResponse({
                "ok": False,
                "error": "Usuario Bonita no encontrado",
                "detail": assignee_username
            }, status=500)

        # Buscar tarea "Monitorear ejecución / transparencia"
        task = cli.wait_ready_task_in_case(case_id, "Monitorear ejecución / transparencia", timeout_sec=5)

        if not task:
            # Si no hay tarea, puede ser que ya terminó o no está en ese estado
            return JsonResponse({
                "ok": False,
                "error": "La tarea de monitoreo no está lista. El proceso puede haber finalizado ya o estar en otro estado.",
                "caseId": case_id
            }, status=409)

        # Ejecutar la tarea con el contrato: accion = "FINALIZAR"
        # Incluir los otros campos del contrato con valores por defecto
        cli.assign_task(task["id"], user_id)
        contract = {
            "accion": "FINALIZAR",
            "observacionId": 0,  # Valor dummy, no se usa para finalizar
            "respuesta": ""  # Texto vacío, no se usa para finalizar
        }

        try:
            cli.execute_task(task["id"], contract)
        except Exception as e:
            return JsonResponse({
                "ok": False,
                "error": "Error ejecutando tarea en Bonita",
                "detail": str(e),
                "taskId": task["id"],
                "contract": contract
            }, status=500)

        return JsonResponse({
            "ok": True,
            "caseId": case_id,
            "proyectoId": proyecto_id,
            "mensaje": "Proyecto finalizado correctamente"
        })

    except Exception as e:
        import traceback
        print(f"Error en finalizar_proyecto_api: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            "ok": False,
            "error": "Error finalizando proyecto",
            "detail": str(e),
            "type": type(e).__name__
        }, status=500)


@csrf_exempt
def red_ongs_salir_api(req: HttpRequest):
    """
    Finaliza la colaboración de la Red de ONGs.
    Ejecuta la tarea activa (cualquiera de las 3 del ciclo)
    enviando seguirColaborando = false.
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

        assignee_username = getattr(settings, "BONITA_ASSIGNEE", "walter.bates")
        user_id = cli.get_user_id_by_username(assignee_username)

        # Buscar cualquier tarea del ciclo de Red de ONGs
        posibles = [
            "Revisar proyectos",
            "Revisar pedidos",
            "Registrar compromiso"
        ]

        tarea = None
        for nombre in posibles:
            tarea = cli.wait_ready_task_in_case(case_id, nombre, timeout_sec=2)
            if tarea:
                break

        if not tarea:
            return JsonResponse({
                "ok": True,
                "caseId": case_id,
                "note": "No hay tareas de la Red de ONGs activas. Se asume finalizado."
            })

        # Ejecutar la tarea encontrada
        cli.assign_task(tarea["id"], user_id)

        # Armamos el contrato según la tarea
        contract = {
            "seguirColaborando": False
        }

        # Algunas tareas tienen campos obligatorios adicionales
        nombre = tarea["name"]

        if nombre == "Revisar proyectos":
            contract["proyectoSeleccionadoId"] = 0

        if nombre == "Revisar pedidos":
            contract["verOtroProyecto"] = False

        if nombre == "Registrar compromiso":
            contract.setdefault("compromisoTipo", "")
            contract.setdefault("compromisoDetalle", "")
            contract.setdefault("pedidoId", 0)

        cli.execute_task(tarea["id"], contract)

        return JsonResponse({
            "ok": True,
            "caseId": case_id,
            "mensaje": f"Tarea '{nombre}' ejecutada → colaboración finalizada."
        })

    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": "Error integrando con Bonita",
            "detail": str(e)
        }, status=500)
