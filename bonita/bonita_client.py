import json
from typing import Any, Dict, Optional

import requests
from django.conf import settings


class BonitaClient:
    def __init__(self, timeout: float = 15.0) -> None:
        self.s = requests.Session()
        self.base = settings.BONITA_BASE_URL.rstrip("/")
        self.api = f"{self.base}/API"
        self._csrf: Optional[str] = None
        self._timeout = timeout

        # Headers por defecto para todas las requests a Bonita
        self.s.headers.update({
            "Accept": "application/json",
            "User-Agent": "pp-front/bonita-client",
        })

    def _h(self) -> Dict[str, str]:
        """
        Headers adicionales para incluir el token CSRF de Bonita
        cuando ya hicimos login.
        """
        return {"X-Bonita-API-Token": self._csrf} if self._csrf else {}

    def _json(self, r: requests.Response):
        """
        Devuelve el cuerpo parseado como JSON si aplica,
        o None si no hay contenido JSON.
        """
        if r.status_code == 204:
            return None

        ct = (r.headers.get("Content-Type") or "")
        if ct.startswith("application/json") and r.text.strip():
            try:
                return r.json()
            except ValueError:
                return None

        return None

    # --- Sesión ---

    def login(self) -> None:
        """
        Inicia sesión en Bonita y guarda el token CSRF en cookies.
        """
        r = self.s.post(
            f"{self.base}/loginservice",
            data={
                "username": settings.BONITA_USER,
                "password": settings.BONITA_PASSWORD,
                "redirect": "false",
            },
            timeout=self._timeout,
        )
        r.raise_for_status()
        self._csrf = self.s.cookies.get("X-Bonita-API-Token")

    # --- Procesos / tareas ---

    def get_process_definition_id(self, name: str, version: str) -> Optional[str]:
        """
        Devuelve el ID de definición de proceso dado un nombre y versión,
        o None si no se encuentra.
        """
        r = self.s.get(
            f"{self.api}/bpm/process",
            params=[
                ("p", "0"),
                ("c", "5"),
                ("f", f"name={name}"),
                ("f", f"version={version}"),
            ],
            headers=self._h(),
            timeout=self._timeout,
        )
        r.raise_for_status()
        data = self._json(r) or []
        return data[0]["id"] if data else None

    def instantiate_process(self, proc_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Instancia un proceso en Bonita con el contrato dado (payload).
        """
        r = self.s.post(
            f"{self.api}/bpm/process/{proc_id}/instantiation",
            json=payload,
            headers=self._h(),
            timeout=self._timeout,
        )
        r.raise_for_status()
        return self._json(r)

    def wait_ready_task_in_case(
        self,
        case_id: str,
        task_name: Optional[str] = None,
        timeout_sec: float = 12.0,
        interval_sec: float = 0.4,
    ) -> Optional[Dict[str, Any]]:
        """
        Espera hasta que haya una tarea humana en estado 'ready'
        para el case_id dado. Si task_name no es None, filtra por nombre.
        Devuelve el primer objeto tarea encontrado o None si vence el timeout.
        """
        import time

        deadline = time.time() + timeout_sec
        params: list[tuple[str, str]] = [
            ("f", f"caseId={case_id}"),
            ("f", "state=ready"),
            ("p", "0"),
            ("c", "10"),
        ]
        if task_name:
            params.append(("f", f"name={task_name}"))

        while time.time() < deadline:
            r = self.s.get(
                f"{self.api}/bpm/humanTask",
                params=params,
                headers=self._h(),
                timeout=self._timeout,
            )
            r.raise_for_status()
            tasks = self._json(r) or []
            if tasks:
                return tasks[0]
            time.sleep(interval_sec)

        return None

    def get_user_id_by_username(self, username: str) -> Optional[str]:
        """
        Devuelve el ID del usuario de Bonita a partir del userName.
        """
        r = self.s.get(
            f"{self.api}/identity/user",
            params=[("f", f"userName={username}")],
            headers=self._h(),
            timeout=self._timeout,
        )
        r.raise_for_status()
        data = self._json(r) or []
        return data[0]["id"] if data else None

    def assign_task(self, task_id: str, user_id: str) -> None:
        """
        Asigna una tarea humana a un usuario (assigned_id).
        """
        r = self.s.put(
            f"{self.api}/bpm/humanTask/{task_id}",
            json={"assigned_id": user_id},
            headers=self._h(),
            timeout=self._timeout,
        )
        r.raise_for_status()

    def execute_task(self, task_id: str, contract: Dict[str, Any]):
        """
        Ejecuta una userTask enviando el contrato (campos del formulario).
        """
        r = self.s.post(
            f"{self.api}/bpm/userTask/{task_id}/execution",
            json=contract,
            headers=self._h(),
            timeout=self._timeout,
        )
        r.raise_for_status()
        return self._json(r)

    # --- Casos ---

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene la información de un caso por su ID.
        Devuelve None si el caso no existe.
        """
        r = self.s.get(
            f"{self.api}/bpm/case/{case_id}",
            headers=self._h(),
            timeout=self._timeout,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._json(r)

    # --- Variables del caso ---

    def get_case_variable(self, case_id: str, var_name: str) -> Optional[Dict[str, Any]]:
        """
        Devuelve el objeto variable de caso (incluye tipo y valor) o None si no existe.
        """
        r = self.s.get(
            f"{self.api}/bpm/caseVariable/{case_id}/{var_name}",
            headers=self._h(),
            timeout=self._timeout,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._json(r)

    def update_case_variable(self, case_id: str, var_name: str, value: Any) -> None:
        """
        Actualiza una variable de caso existente usando el tipo real
        que ya tiene en Bonita.

        Lee primero la variable para conocer el 'type' y luego hace PUT
        con ese mismo tipo y el nuevo valor.
        """
        current = self.get_case_variable(case_id, var_name)
        if not current:
            raise ValueError(f"Variable de caso '{var_name}' no encontrada en case {case_id}")

        var_type = current.get("type") or "java.lang.String"

        payload = {
            "type": var_type,
            "value": value,
        }

        r = self.s.put(
            f"{self.api}/bpm/caseVariable/{case_id}/{var_name}",
            json=payload,
            headers=self._h(),
            timeout=self._timeout,
        )
        r.raise_for_status()
