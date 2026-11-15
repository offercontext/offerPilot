# ruff: noqa: E402
"""Isolated synthetic-only UI acceptance server. Never use a personal data directory.

Run: OFFERPILOT_MAIL_FIXTURE_DATA=/tmp/mail-fixture PYTHONPATH=src python tests/fixtures/job_mail_app.py
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from pathlib import Path

_original_connect = socket.socket.connect


def _local_only(self: socket.socket, address: object) -> object:
    if isinstance(address, tuple) and str(address[0]) not in {"127.0.0.1", "::1", "localhost"}:
        raise OSError("Synthetic acceptance fixture blocks external network")
    return _original_connect(self, address)  # type: ignore[arg-type]


socket.socket.connect = _local_only  # type: ignore[method-assign]
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import uvicorn

from offerpilot.api import create_app
from offerpilot.job_mail.extraction import ParsedMail
from offerpilot.job_mail.transport import FakeIMAPTransport, QQIMAPTransport


class SyntheticVault:
    """RAM-only test double. Never instantiates or writes an OS credential store."""
    backend_name = "Synthetic acceptance vault (RAM only)"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, reference: str) -> str:
        return self.values[reference]

    def set(self, reference: str, secret: str) -> None:
        self.values[reference] = secret

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


class SyntheticQQTransport(QQIMAPTransport):
    """Exercise real setup orchestration without opening an IMAP socket."""
    body_read_attempts = 0

    def discover(self) -> list[dict[str, object]]:
        self._check_cancelled()
        assert self._vault.get(self._reference) == "synthetic-browser-fixture-only"
        return [
            {"id": "fixture-inbox", "name": "INBOX", "selectable": True,
             "excluded_by_default": False, "special_use": []},
            {"id": "fixture-recruitment", "name": "求职通知", "selectable": True,
             "excluded_by_default": False, "special_use": []},
            {"id": "fixture-trash", "name": "垃圾箱", "selectable": True,
             "excluded_by_default": True, "special_use": ["\\Trash"]},
        ]

    def baseline(self, folder: str) -> tuple[str, int]:
        self._check_cancelled()
        assert folder in {"INBOX", "求职通知", "垃圾箱"}
        return "fixture-qq", 0

    def read(self, *args: object, **kwargs: object) -> object:
        type(self).body_read_attempts += 1
        raise AssertionError("Setup browser acceptance must never fetch a body")

root = Path(os.environ["OFFERPILOT_MAIL_FIXTURE_DATA"])
root.mkdir(parents=True, exist_ok=True)
body = "公司：合成星河科技\n岗位：后端工程师\n面试安排：2026-10-15T15:00:00+08:00\n时长：45分钟\n地点：线上合成会议室"
fixture = ParsedMail(1, "1", "INBOX", "<synthetic-acceptance@example.invalid>",
    datetime.now(timezone.utc).isoformat(), "hr@example.invalid", "【合成样本】面试邀请",
    body, "synthetic-ui-invite")
transport = FakeIMAPTransport([fixture])
transport.generations["Recruitment"] = "1"
transport.generations["Private-not-selected"] = "1"
app = create_app(data_dir=root, static_dir=Path("web/dist"), job_mail_transport=transport,
                 job_mail_local_setup_enabled=True)
vault = SyntheticVault()
app.state.job_mail_secure_setup.vault_factory = lambda: vault
app.state.job_mail_secure_setup.transport_factory = SyntheticQQTransport


@app.get("/api/job-mail/fixture-safety")
def fixture_safety() -> dict[str, int | bool]:
    # Fixed counters only, never dump vault keys/values, config, or environment.
    return {"synthetic_only": True, "body_read_attempts": SyntheticQQTransport.body_read_attempts,
            "vault_entries": len(vault.values)}


# create_app registers its SPA fallback last. The fixture route must precede
# that catch-all, otherwise the final browser safety assertion receives a 404.
fixture_route = app.router.routes.pop()
assert getattr(fixture_route, "path", None) == "/api/job-mail/fixture-safety"
app.router.routes.insert(0, fixture_route)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("OFFERPILOT_MAIL_FIXTURE_PORT", "38091")),
                access_log=False)
