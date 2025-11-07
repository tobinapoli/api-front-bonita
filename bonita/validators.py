# bonita/validators.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

def _is_str(v): return isinstance(v, str) and v.strip() != ""
def _is_num(v): return isinstance(v, (int, float))

def _parse_date(d: Optional[str]):
    if not (isinstance(d, str) and d.strip()):
        return None, None
    txt = d.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return None, datetime.strptime(txt, fmt)
        except ValueError:
            continue
    return "Formato de fecha inválido (use YYYY-MM-DD).", None

def validate_iniciar_payload(data: Dict[str, Any]) -> List[Dict[str, str]]:
    errs: List[Dict[str, str]] = []

    def err(path, msg): errs.append({"path": path, "msg": msg})

    # nombre
    if not _is_str(data.get("nombre", "")):
        err("nombre", "Requerido (string no vacío).")

    # metadata
    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):
        err("metadata", "Debe ser objeto.")
        meta = {}

    meta_ini_raw = meta.get("fechaInicioPrevista")
    meta_fin_raw = meta.get("fechaFinPrevista")

    if not _is_str(meta_ini_raw):
        err("metadata.fechaInicioPrevista", "Requerido (YYYY-MM-DD).")
        meta_ini = None
    else:
        e_meta_ini_fmt, meta_ini = _parse_date(meta_ini_raw)
        if e_meta_ini_fmt: err("metadata.fechaInicioPrevista", e_meta_ini_fmt)

    if not _is_str(meta_fin_raw):
        err("metadata.fechaFinPrevista", "Requerido (YYYY-MM-DD).")
        meta_fin = None
    else:
        e_meta_fin_fmt, meta_fin = _parse_date(meta_fin_raw)
        if e_meta_fin_fmt: err("metadata.fechaFinPrevista", e_meta_fin_fmt)

    if (meta_ini and meta_fin) and meta_fin < meta_ini:
        err("metadata.fechaFinPrevista", "Debe ser >= fechaInicioPrevista (proyecto).")

    # planTrabajo
    pt = data.get("planTrabajo")
    if not isinstance(pt, dict):
        err("planTrabajo", "Debe ser objeto con 'etapas'.")
    else:
        etapas = pt.get("etapas")
        if not isinstance(etapas, list) or len(etapas) == 0:
            err("planTrabajo.etapas", "Debe ser una lista con al menos 1 etapa.")
        else:
            for i, e in enumerate(etapas):
                if not isinstance(e, dict):
                    err(f"planTrabajo.etapas[{i}]", "Cada etapa debe ser objeto.")
                    continue
                if not _is_str(e.get("nombre", "")):
                    err(f"planTrabajo.etapas[{i}].nombre", "Requerido (string).")

                ini_raw = e.get("fechaInicioPrevista")
                fin_raw = e.get("fechaFinPrevista")

                if not _is_str(ini_raw):
                    err(f"planTrabajo.etapas[{i}].fechaInicioPrevista", "Requerido (YYYY-MM-DD).")
                    e_ini = None
                else:
                    e_i_fmt, e_ini = _parse_date(ini_raw)
                    if e_i_fmt: err(f"planTrabajo.etapas[{i}].fechaInicioPrevista", e_i_fmt)

                if not _is_str(fin_raw):
                    err(f"planTrabajo.etapas[{i}].fechaFinPrevista", "Requerido (YYYY-MM-DD).")
                    e_fin = None
                else:
                    e_f_fmt, e_fin = _parse_date(fin_raw)
                    if e_f_fmt: err(f"planTrabajo.etapas[{i}].fechaFinPrevista", e_f_fmt)

                if e_ini and e_fin and e_fin < e_ini:
                    err(f"planTrabajo.etapas[{i}].fechaFinPrevista", "Debe ser >= fechaInicioPrevista.")
                if meta_ini and e_ini and e_ini < meta_ini:
                    err(f"planTrabajo.etapas[{i}].fechaInicioPrevista", "No puede ser anterior al inicio del proyecto.")
                if meta_fin and e_fin and e_fin > meta_fin:
                    err(f"planTrabajo.etapas[{i}].fechaFinPrevista", "No puede superar el fin del proyecto.")

                for k in ("responsablePropuesto", "criteriosAceptacion", "descripcion"):
                    if k in e and not isinstance(e[k], str):
                        err(f"planTrabajo.etapas[{i}].{k}", "Debe ser string.")

    # planEconomico
    pe = data.get("planEconomico")
    if not isinstance(pe, dict):
        err("planEconomico", "Debe ser objeto con 'monedaBase' y 'presupuestoPorRubro'.")
    else:
        if "monedaBase" in pe and not _is_str(pe["monedaBase"]):
            err("planEconomico.monedaBase", "Debe ser string (ej. 'USD', 'ARS').")
        rubros = pe.get("presupuestoPorRubro", [])
        if not isinstance(rubros, list):
            err("planEconomico.presupuestoPorRubro", "Debe ser lista.")
        else:
            for i, r in enumerate(rubros):
                if not isinstance(r, dict):
                    err(f"planEconomico.presupuestoPorRubro[{i}]", "Cada rubro debe ser objeto.")
                    continue
                if not _is_str(r.get("rubro", "")):
                    err(f"planEconomico.presupuestoPorRubro[{i}].rubro", "Requerido (string).")
                monto = r.get("monto", None)
                if monto is None or not _is_num(monto) or monto < 0:
                    err(f"planEconomico.presupuestoPorRubro[{i}].monto", "Requerido (número ≥ 0).")

    return errs
