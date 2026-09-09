from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.config import Config
from offerpilot.pilot_runtime.managed_execution import RuntimeExecutionManager
from .contracts import ProactivePolicyUpdate
from .repository import ProactiveConflict, ProactiveRepository
from .runtime import ProactiveRuntime, configured_draft


def register_proactive_routes(app: FastAPI, sessions: sessionmaker[Session],
                              manager: RuntimeExecutionManager, config_loader: Callable[[], Config]) -> ProactiveRuntime:
    repository = ProactiveRepository(sessions)
    runtime = ProactiveRuntime(repository, manager, configured_draft(config_loader))
    app.state.proactive_runtime = runtime

    @app.get("/api/proactive/settings")
    def settings() -> dict[str, Any]:
        return repository.settings()

    @app.put("/api/proactive/settings")
    def update_settings(command: ProactivePolicyUpdate) -> JSONResponse:
        try:
            result = repository.update_settings(command)
            runtime.stop_cancelled()
            return JSONResponse(result)
        except ProactiveConflict as exc:
            return JSONResponse({"error": "主动任务设置未保存，请刷新并检查选择范围", "error_code": str(exc)}, status_code=409)

    @app.get("/api/proactive/jobs")
    def jobs() -> dict[str, Any]:
        return {"items": repository.list_jobs()}

    @app.post("/api/proactive/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> JSONResponse:
        try:
            repository.cancel(job_id)
            runtime.stop_cancelled()
            return JSONResponse({"state": "cancelled"})
        except ProactiveConflict as exc:
            if str(exc) == "job_already_terminal":
                return JSONResponse(
                    {"error": "任务已结束，结果未修改", "error_code": str(exc)},
                    status_code=409,
                )
            return JSONResponse({"error": "任务不存在", "error_code": str(exc)}, status_code=404)

    return runtime
