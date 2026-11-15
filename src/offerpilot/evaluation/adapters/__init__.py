from __future__ import annotations

import time
from typing import Any

import httpx


class DriverError(RuntimeError):
    pass


class ProductFailure(DriverError):
    pass


class HTTPDriver:
    def __init__(self, address: str, token: str, deadline: float,
                 observations: list[dict[str, Any]]) -> None:
        self.client = httpx.Client(base_url=address, headers={"Authorization": f"Bearer {token}"},
                                   trust_env=False)
        self.deadline, self.observations = deadline, observations
        self.script_replies = 0

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("trial watchdog")
        if method == "POST":
            self.script_replies += 1
            if self.script_replies > 4:
                raise TimeoutError("script reply watchdog")
        response = self.client.request(method, path, json=body, timeout=min(30, remaining))
        try:
            payload = response.json()
        except ValueError as exc:
            self.observations.append({"method": method, "path": path, "body": body,
                                      "status": response.status_code, "response": response.text[:524288]})
            if response.status_code not in {200, 202}:
                raise ProductFailure(f"HTTP {response.status_code} on {path}") from exc
            raise DriverError("unhandled non-JSON response") from exc
        self.observations.append({"method": method, "path": path, "body": body,
                                  "status": response.status_code, "response": payload})
        if response.status_code not in {200, 202}:
            raise ProductFailure(f"HTTP {response.status_code} on {path}")
        if not isinstance(payload, dict):
            raise DriverError("unhandled non-object response")
        return payload

    def wait_turn(self, turn: str, states: set[str]) -> dict[str, Any]:
        while time.monotonic() < self.deadline:
            status = self.request("GET", f"/api/pilot/runtime/v1/turns/{turn}")
            if status.get("state") in states | {"failed", "cancelled", "interrupted"}:
                return status
            time.sleep(0.05)
        raise TimeoutError("runtime did not converge")

    def close(self) -> None:
        self.client.close()
