"""Synthetic scheduler evidence only; test controls are never production routes.

Deadline/throttle fixtures are seeded rather than waiting five real minutes.
The unmodified runtime's ten-second loop must invoke tick and start automatic work.
"""
from datetime import datetime, timedelta, timezone
import os

from fastapi import HTTPException
from sqlalchemy import select
import uvicorn

from job_mail_app import app, transport  # inherits the fail-closed socket guard
from offerpilot.job_mail.models import JobMailConnection, JobMailSyncRun
from offerpilot.job_mail.transport import TransportUnavailable

service = app.state.job_mail_service
original_read = transport.read
state = {"fail": False, "read_attempts": 0}


def controlled_read(*args, **kwargs):
    state["read_attempts"] += 1
    if state["fail"]:
        raise TransportUnavailable("synthetic_read_failure")
    return original_read(*args, **kwargs)


transport.read = controlled_read


@app.post("/api/job-mail/fixture-scheduler/{action}")
def control(action: str):
    if action not in {"due", "fail", "recover", "cooldown"}:
        raise HTTPException(400, "Unknown synthetic fixture action")
    if action in {"fail", "recover"}:
        state["fail"] = action == "fail"
    else:
        with service.sessions() as session:
            connection = session.scalar(select(JobMailConnection))
            if connection is None:
                raise HTTPException(409, "Synthetic connection required")
            if session.scalar(select(JobMailSyncRun).where(JobMailSyncRun.status == "running")):
                raise HTTPException(409, "Wait for the synthetic worker")
            past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            connection.not_before_at = past
            if action == "due":
                connection.next_run_at = past
            session.commit()
    return {"synthetic_only": True, "read_attempts": state["read_attempts"]}


@app.get("/api/job-mail/fixture-scheduler")
def counters():
    return {"synthetic_only": True, "read_attempts": state["read_attempts"]}


# Both fixture controls must precede the product's SPA fallback.
for _ in range(2):
    route = app.router.routes.pop()
    assert getattr(route, "path", "").startswith("/api/job-mail/fixture-scheduler")
    app.router.routes.insert(0, route)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("OFFERPILOT_MAIL_FIXTURE_PORT", "38091")),
                access_log=False)
