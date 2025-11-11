import requests, json
from typing import Any, Dict, Optional
from django.conf import settings

class BonitaClient:
    def __init__(self, timeout: float = 15.0) -> None:
        self.s = requests.Session()
        self.base = settings.BONITA_BASE_URL.rstrip("/")
        self.api = f"{self.base}/API"
        self._csrf: Optional[str] = None
        self.s.headers.update({"Accept": "application/json", "User-Agent": "pp-front/bonita-client"})
        self._timeout = timeout

    def _h(self): 
        return {"X-Bonita-API-Token": self._csrf} if self._csrf else {}

    def _json(self, r: requests.Response):
        if r.status_code == 204: 
            return None
        ct = (r.headers.get("Content-Type") or "")
        if ct.startswith("application/json") and r.text.strip():
            return r.json()
        return None

    # --- Sesión ---
    def login(self) -> None:
        r = self.s.post(
            f"{self.base}/loginservice",
            data={"username": settings.BONITA_USER, "password": settings.BONITA_PASSWORD, "redirect": "false"},
            timeout=self._timeout,
        )
        r.raise_for_status()
        self._csrf = self.s.cookies.get("X-Bonita-API-Token")

    # --- Procesos / tareas ---
    def get_process_definition_id(self, name: str, version: str) -> Optional[str]:
        r = self.s.get(
            f"{self.api}/bpm/process",
            params=[("p","0"),("c","5"),("f",f"name={name}"),("f",f"version={version}")],
            headers=self._h(), timeout=self._timeout
        )
        r.raise_for_status()
        data = self._json(r) or []
        return data[0]["id"] if data else None

    def instantiate_process(self, proc_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        r = self.s.post(
            f"{self.api}/bpm/process/{proc_id}/instantiation",
            json=payload, headers=self._h(), timeout=self._timeout
        )
        r.raise_for_status()
        return self._json(r)

    def wait_ready_task_in_case(self, case_id: str, task_name: Optional[str] = None,
                                timeout_sec=12, interval_sec=0.4):
        import time
        deadline = time.time() + timeout_sec
        params = [("f",f"caseId={case_id}"),("f","state=ready"),("p","0"),("c","10")]
        if task_name:
            params.append(("f", f"name={task_name}"))
        while time.time() < deadline:
            r = self.s.get(f"{self.api}/bpm/humanTask", params=params, headers=self._h(), timeout=self._timeout)
            r.raise_for_status()
            tasks = self._json(r) or []
            if tasks:
                return tasks[0]
            time.sleep(interval_sec)
        return None

    def get_user_id_by_username(self, username: str) -> Optional[str]:
        r = self.s.get(
            f"{self.api}/identity/user",
            params=[("f", f"userName={username}")],
            headers=self._h(), timeout=self._timeout
        )
        r.raise_for_status()
        data = self._json(r) or []
        return data[0]["id"] if data else None

    def assign_task(self, task_id: str, user_id: str) -> None:
        r = self.s.put(
            f"{self.api}/bpm/humanTask/{task_id}",
            json={"assigned_id": user_id}, headers=self._h(), timeout=self._timeout
        )
        r.raise_for_status()

    def execute_task(self, task_id: str, contract: Dict[str, Any]):
        r = self.s.post(
            f"{self.api}/bpm/userTask/{task_id}/execution",
            json=contract, headers=self._h(), timeout=self._timeout
        )
        r.raise_for_status()
        return self._json(r)

    # --- Variables del caso ---
    def get_case_variable(self, case_id: str, var_name: str) -> Optional[Dict[str, Any]]:
        """Devuelve el objeto variable de caso (incluye tipo y valor)."""
        r = self.s.get(
            f"{self.api}/bpm/caseVariable/{case_id}/{var_name}",
            headers=self._h(), timeout=self._timeout
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._json(r)
