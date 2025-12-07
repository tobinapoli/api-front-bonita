# bonita/validators.py
from __future__ import annotations
from datetime import datetime, date
from typing import Any, Dict, List, Optional

# Monedas y rubros válidos
VALID_CURRENCIES = {"ARS", "USD", "EUR", "BRL", "UYU", "CLP"}
VALID_RUBROS = {
    "Desarrollo",
    "Testing",
    "Gestión",
    "Infraestructura",
    "Diseño",
    "Documentación",
    "Capacitación",
    "Soporte",
    "Otro"
}

MAX_ETAPAS = 5


def _is_str(v): 
    return isinstance(v, str) and v.strip() != ""


def _is_num(v): 
    return isinstance(v, (int, float))


def _parse_date(d: Optional[str]):
    """
    Devuelve (error_msg, datetime|None).
    Acepta 'YYYY-MM-DD' y 'MM/DD/YYYY'.
    """
    if not (isinstance(d, str) and d.strip()):
        return None, None
    txt = d.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return None, datetime.strptime(txt, fmt)
        except ValueError:
            continue
    return "Formato de fecha inválido (use YYYY-MM-DD).", None


def _get_today():
    """Retorna la fecha de hoy (date)."""
    return date.today()


def validate_iniciar_payload(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Valida el payload de /api/bonita/iniciar/.

    Reglas principales:
    - 'nombre' obligatorio.
    - planTrabajo.etapas:
        * lista, al menos 1 etapa, máximo MAX_ETAPAS.
        * cada etapa con nombre, fechaInicioPrevista y fechaFinPrevista.
        * fechas >= hoy.
        * fechaFin >= fechaInicio.
        * etapa[i].inicio >= etapa[i-1].fin (orden secuencial).
    - Desde las fechas de etapas se calcula:
        data["metadata"] = {
            "fechaInicioPrevista": min(fechaInicioEtapas),
            "fechaFinPrevista": max(fechaFinEtapas),
        }
    - planEconomico:
        * monedaBase válida.
        * al menos un rubro, rubro dentro de lista válida, monto numérico >= 0.
    """
    errs: List[Dict[str, str]] = []

    def err(path, msg):
        errs.append({"path": path, "msg": msg})

    today = _get_today()

    # ---------- nombre ----------
    if not _is_str(data.get("nombre", "")):
        err("nombre", "Requerido (string no vacío).")

    # ---------- planTrabajo ----------
    pt = data.get("planTrabajo")
    if not isinstance(pt, dict):
        err("planTrabajo", "Debe ser objeto con 'etapas'.")
        etapas = []
    else:
        etapas = pt.get("etapas")

    if not isinstance(etapas, list) or len(etapas) == 0:
        err("planTrabajo.etapas", "Debe ser una lista con al menos 1 etapa.")
        etapas = []
    elif len(etapas) > MAX_ETAPAS:
        err("planTrabajo.etapas", f"No puede haber más de {MAX_ETAPAS} etapas.")

    start_dates: List[Optional[date]] = []
    end_dates: List[Optional[date]] = []
    prev_end: Optional[date] = None

    for i, e in enumerate(etapas):
        if not isinstance(e, dict):
            err(f"planTrabajo.etapas[{i}]", "Cada etapa debe ser objeto.")
            start_dates.append(None)
            end_dates.append(None)
            continue

        # nombre de etapa
        if not _is_str(e.get("nombre", "")):
            err(f"planTrabajo.etapas[{i}].nombre", "Requerido (string).")

        # fechas
        ini_raw = e.get("fechaInicioPrevista")
        fin_raw = e.get("fechaFinPrevista")

        # inicio
        if not _is_str(ini_raw):
            err(f"planTrabajo.etapas[{i}].fechaInicioPrevista", "Requerido (YYYY-MM-DD).")
            e_ini_date = None
        else:
            e_i_fmt, e_ini_dt = _parse_date(ini_raw)
            if e_i_fmt:
                err(f"planTrabajo.etapas[{i}].fechaInicioPrevista", e_i_fmt)
                e_ini_date = None
            else:
                e_ini_date = e_ini_dt.date()
                if e_ini_date < today:
                    err(f"planTrabajo.etapas[{i}].fechaInicioPrevista", "No puede ser anterior a hoy.")
                # orden secuencial: esta etapa no puede empezar antes de que termine la anterior
                if prev_end and e_ini_date < prev_end:
                    err(
                        f"planTrabajo.etapas[{i}].fechaInicioPrevista",
                        "Debe ser mayor o igual a la fecha de fin de la etapa anterior."
                    )

        # fin
        if not _is_str(fin_raw):
            err(f"planTrabajo.etapas[{i}].fechaFinPrevista", "Requerido (YYYY-MM-DD).")
            e_fin_date = None
        else:
            e_f_fmt, e_fin_dt = _parse_date(fin_raw)
            if e_f_fmt:
                err(f"planTrabajo.etapas[{i}].fechaFinPrevista", e_f_fmt)
                e_fin_date = None
            else:
                e_fin_date = e_fin_dt.date()
                if e_fin_date < today:
                    err(f"planTrabajo.etapas[{i}].fechaFinPrevista", "No puede ser anterior a hoy.")

        # relación inicio/fin de la misma etapa
        if e_ini_date and e_fin_date and e_fin_date < e_ini_date:
            err(
                f"planTrabajo.etapas[{i}].fechaFinPrevista",
                "Debe ser >= fechaInicioPrevista."
            )

        start_dates.append(e_ini_date)
        end_dates.append(e_fin_date)

        # para la siguiente etapa
        if e_fin_date:
            prev_end = e_fin_date

        # campos opcionales de etapa, pero si vienen deben ser string
        for k in ("responsablePropuesto", "criteriosAceptacion", "descripcion"):
            if k in e and not isinstance(e[k], str):
                err(f"planTrabajo.etapas[{i}].{k}", "Debe ser string.")

    # Si todo lo de etapas está bien, calculamos metadata desde las etapas
    if not errs and len(etapas) > 0:
        valid_starts = [d for d in start_dates if d is not None]
        valid_ends = [d for d in end_dates if d is not None]
        if valid_starts and valid_ends:
            meta_ini = min(valid_starts)
            meta_fin = max(valid_ends)
            data["metadata"] = {
                "fechaInicioPrevista": meta_ini.isoformat(),
                "fechaFinPrevista": meta_fin.isoformat(),
            }

    # ---------- planEconomico ----------
    pe = data.get("planEconomico")
    if not isinstance(pe, dict):
        err("planEconomico", "Debe ser objeto con 'monedaBase' y 'presupuestoPorRubro'.")
    else:
        # moneda
        moneda_raw = pe.get("monedaBase", "")
        if not _is_str(moneda_raw):
            err("planEconomico.monedaBase", "Requerido (string).")
        else:
            moneda = moneda_raw.strip().upper()
            if moneda not in VALID_CURRENCIES:
                err(
                    "planEconomico.monedaBase",
                    f"Debe ser una de: {', '.join(sorted(VALID_CURRENCIES))}"
                )

        # rubros
        rubros = pe.get("presupuestoPorRubro", [])
        if not isinstance(rubros, list):
            err("planEconomico.presupuestoPorRubro", "Debe ser lista.")
        else:
            if len(rubros) == 0:
                err("planEconomico.presupuestoPorRubro", "Debe tener al menos 1 rubro.")
            for i, r in enumerate(rubros):
                if not isinstance(r, dict):
                    err(f"planEconomico.presupuestoPorRubro[{i}]", "Cada rubro debe ser objeto.")
                    continue

                # nombre de rubro
                rubro_nombre = (r.get("rubro") or "").strip()
                if not _is_str(r.get("rubro", "")):
                    err(f"planEconomico.presupuestoPorRubro[{i}].rubro", "Requerido (string).")
                elif rubro_nombre not in VALID_RUBROS:
                    err(
                        f"planEconomico.presupuestoPorRubro[{i}].rubro",
                        f"Debe ser uno de: {', '.join(sorted(VALID_RUBROS))}"
                    )

                # monto
                monto = r.get("monto", None)
                if monto is None or not _is_num(monto):
                    err(f"planEconomico.presupuestoPorRubro[{i}].monto", "Requerido (número).")
                elif monto < 0:
                    err(f"planEconomico.presupuestoPorRubro[{i}].monto", "No puede ser negativo.")

    return errs
