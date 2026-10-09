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
from offerpilot.job_mail.transport import FakeIMAPTransport

root = Path(os.environ["OFFERPILOT_MAIL_FIXTURE_DATA"])
root.mkdir(parents=True, exist_ok=True)
body = "公司：合成星河科技\n岗位：后端工程师\n面试安排：2026-10-15T15:00:00+08:00\n时长：45分钟\n地点：线上合成会议室"
fixture = ParsedMail(1, "1", "INBOX", "<synthetic-acceptance@example.invalid>",
    datetime.now(timezone.utc).isoformat(), "hr@example.invalid", "【合成样本】面试邀请",
    body, "synthetic-ui-invite")
transport = FakeIMAPTransport([fixture])
transport.generations["Recruitment"] = "1"
transport.generations["Private-not-selected"] = "1"
app = create_app(data_dir=root, static_dir=Path("web/dist"), job_mail_transport=transport)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("OFFERPILOT_MAIL_FIXTURE_PORT", "38091")),
                access_log=False)
