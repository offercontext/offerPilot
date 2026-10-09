"""Human-only loopback setup. Secrets live briefly in RAM, then only in OS vault.

No secret is persisted in SQLite, returned in a response, or included in errors.
All IMAP and OS-vault calls are outside SQLite transactions. Non-secret intents
retain a cleanup reference if the process dies between vault and DB writes.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import Field, SecretStr, StrictBool, StrictStr, ValidationError, field_validator
from sqlalchemy import select

from .models import JobMailConnection, JobMailCredentialOperation
from .process_lock import LocalMailProcessLock
from .review import JobMailError, begin_immediate, canonical, timestamp, utcnow, workspace_id
from .schemas import StrictInput
from .sync import JobMailSyncService
from .transport import DisabledTransport, OSVault, QQIMAPTransport, TransportUnavailable

COOKIE = "offerpilot_mail_setup"
TTL_SECONDS = 300
MAX_SETUP_BYTES = 8192


class CredentialVault(Protocol):
    backend_name: str
    def get(self, reference: str) -> str: ...
    def set(self, reference: str, secret: str) -> None: ...
    def delete(self, reference: str) -> None: ...


class StartInput(StrictInput):
    explicit_setup_consent: StrictBool
    purpose: Literal["connect", "disconnect"] = "connect"


class TokenInput(StrictInput):
    setup_token: StrictStr = Field(min_length=32, max_length=128)


class TestInput(TokenInput):
    email: StrictStr = Field(min_length=6, max_length=320, pattern=r"^[^\s@]+@qq\.com$")
    authorization_code: SecretStr = Field(min_length=1, max_length=128)
    explicit_test_consent: StrictBool

    @field_validator("authorization_code")
    @classmethod
    def reject_control_characters(cls, value: SecretStr) -> SecretStr:
        if any(ord(char) < 32 or ord(char) == 127 for char in value.get_secret_value()):
            raise ValueError("invalid authorization code")
        return value


class SaveInput(TokenInput):
    folder_ids: list[StrictStr] = Field(min_length=1, max_length=20)
    backfill_days: Literal[0, 7] = 0
    explicit_save_consent: StrictBool


class DeleteInput(TokenInput):
    explicit_delete_consent: StrictBool


@dataclass(repr=False)
class SetupSession:
    token: str
    cookie_digest: str
    scope_id: str
    connection_snapshot: tuple[str, int, str] | None
    purpose: str
    expires_at: datetime
    phase: str = "started"
    address: str = ""
    secret: SecretStr | None = field(default=None, repr=False)
    folders: list[dict[str, Any]] = field(default_factory=list)
    transport: QQIMAPTransport | None = field(default=None, repr=False)
    timer: threading.Timer | None = field(default=None, repr=False)


class TemporaryVault:
    backend_name = "temporary-memory-only"
    def __init__(self, session: SetupSession):
        self.session = session

    def get(self, _reference: str) -> str:
        if self.session.secret is None or self.session.expires_at <= utcnow():
            raise TransportUnavailable("secure_setup_expired")
        return self.session.secret.get_secret_value()


class JobMailSecureSetup:
    def __init__(self, sync: JobMailSyncService, *, vault_factory: Callable[[], CredentialVault] = OSVault,
                 transport_factory: Callable[..., QQIMAPTransport] = QQIMAPTransport,
                 ttl_seconds: int = TTL_SECONDS, enabled: bool = False):
        self.sync = sync
        self.sessions = sync.sessions
        self.vault_factory = vault_factory
        self.transport_factory = transport_factory
        self.ttl_seconds = ttl_seconds
        bind = self.sessions.kw.get("bind")
        database = str(bind.url.database or "") if bind is not None else ""
        self._process_lock = LocalMailProcessLock.acquire(database) if enabled else None
        self.enabled = enabled and self._process_lock is not None
        self.disabled_reason = "local_setup_in_use" if enabled and not self.enabled else "local_setup_not_enabled"
        self.local_only = enabled
        self._lock = threading.RLock()
        self._credential_lock = threading.RLock()
        self._sessions: dict[str, SetupSession] = {}
        self._closed = False
        self._shutdown_fenced = False
        self._active_operations = 0
        sync.secure_setup = self
        with self.sessions() as session:
            connection = self.sync._connection(session)
            self.local_only = self.local_only or bool(connection and connection.provider == "qq")
        if self.enabled:
            self.recover()

    def _vault(self) -> CredentialVault:
        try:
            return self.vault_factory()
        except Exception:
            raise JobMailError("secure_store_unavailable", "本机原生安全凭据库不可用；请使用手动导入", 409) from None

    def requires_local_access(self) -> bool:
        if not self.local_only:
            # Another process can save a real connection after this read-only
            # app started. Once real mail exists, keep its historical data local.
            with self.sessions() as session:
                connection = self.sync._connection(session)
                self.local_only = bool(connection and connection.provider == "qq")
        return self.local_only

    @staticmethod
    def _snapshot(connection: JobMailConnection | None) -> tuple[str, int, str] | None:
        return (connection.id, connection.scope_version, connection.status) if connection else None

    def recover(self) -> None:
        # Local state reconciliation only; no vault write/delete, login or LIST.
        if not self.enabled:
            return
        with self.sessions() as session:
            begin_immediate(session)
            scope = workspace_id(session)
            for operation in session.scalars(select(JobMailCredentialOperation).where(
                    JobMailCredentialOperation.scope_id == scope,
                    JobMailCredentialOperation.state.in_(["storing", "deleting"]))):
                operation.state = "delete_pending"
                operation.updated_at = utcnow()
            session.commit()
        self.rebuild_transport()

    def rebuild_transport(self) -> None:
        if not self.enabled:
            if isinstance(self.sync.transport, QQIMAPTransport):
                self.sync.transport.cancel()
                self.sync.transport = DisabledTransport()
            return
        with self.sessions() as session:
            connection = self.sync._connection(session)
            if not connection or connection.provider != "qq" or connection.status != "connected" or not connection.credential_ref:
                return
            address, reference = connection.email, connection.credential_ref
            expected = self._snapshot(connection)
        try:
            vault = self._vault()
            transport = self.transport_factory(address, reference, enabled=True, vault=vault)
            with self._lock, self.sessions() as session:
                if not self.enabled or self._closed or self._snapshot(self.sync._connection(session)) != expected:
                    transport.cancel()
                    return
                self.sync.transport = transport
        except Exception:
            self.sync.transport = DisabledTransport()

    def catalog(self) -> list[str]:
        with self.sessions() as session:
            connection = self.sync._connection(session)
            if not connection or not connection.credential_ref:
                return []
            operation = session.scalar(select(JobMailCredentialOperation).where(
                JobMailCredentialOperation.scope_id == workspace_id(session),
                JobMailCredentialOperation.credential_ref == connection.credential_ref,
                JobMailCredentialOperation.state == "committed"))
            catalog = json.loads(operation.catalog_json) if operation else []
            return [row["name"] for row in catalog if row.get("selectable")]

    def status(self, *, local: bool) -> dict[str, Any]:
        backend = None
        available = False
        reason = "local_browser_required" if not local else "secure_store_unavailable"
        if not self.enabled:
            reason = self.disabled_reason
        if local and self.enabled:
            try:
                vault = self._vault()
                backend = vault.backend_name
                available = True
                reason = "ready"
            except JobMailError:
                pass
        with self.sessions() as session:
            connection = self.sync._connection(session)
            configured = bool(connection and connection.provider == "qq" and connection.credential_ref)
            pending = session.scalar(select(JobMailCredentialOperation.id).where(
                JobMailCredentialOperation.scope_id == workspace_id(session),
                JobMailCredentialOperation.state.in_(["storing", "deleting", "delete_pending"]))) is not None
            connected = bool(connection and connection.status == "connected")
        if pending and self.enabled:
            reason = "credential_delete_pending"
        elif connected and available:
            reason = "already_connected"
        return {"available": available, "reason": reason, "backend": backend, "local_only": True,
                "credential_input_allowed": available and not connected and not pending,
                "configured": configured, "deletion_pending": pending}

    def start(self, *, cookie: str, purpose: str, consent: bool) -> dict[str, Any]:
        if not self.enabled:
            raise JobMailError("local_setup_not_enabled", "请用仅绑定本机的受支持启动选项开启安全配置；不可经反向代理", 409)
        if consent is not True:
            raise JobMailError("secure_consent_required", "请明确同意开始本机安全配置", 422)
        if purpose == "connect":
            self._vault()
        with self.sessions() as session:
            scope = workspace_id(session)
            connection = self.sync._connection(session)
            pending = session.scalar(select(JobMailCredentialOperation.id).where(
                JobMailCredentialOperation.scope_id == scope,
                JobMailCredentialOperation.state.in_(["storing", "deleting", "delete_pending"])))
            if purpose == "connect" and (pending or connection and (connection.status == "connected" or connection.credential_ref)):
                raise JobMailError("secure_setup_conflict", "请先断开已有连接并完成凭据清理", 409)
            snapshot = self._snapshot(connection)
        with self._lock:
            if self._closed:
                raise JobMailError("secure_setup_expired", "安全配置已关闭", 410)
            # One session per app/workspace bounds live secrets and isolates tabs.
            for token in list(self._sessions):
                self._drop(token)
            token = secrets.token_urlsafe(32)
            entry = SetupSession(token, hashlib.sha256(cookie.encode()).hexdigest(), scope, snapshot,
                                 purpose, utcnow() + timedelta(seconds=self.ttl_seconds))
            timer = threading.Timer(self.ttl_seconds, self.cancel_expired, args=(token,))
            timer.daemon = True
            entry.timer = timer
            self._sessions[token] = entry
            timer.start()
            return {"setup_token": token, "expires_at": timestamp(entry.expires_at)}

    def _entry(self, token: str, cookie: str, *, phase: str | None = None) -> SetupSession:
        with self._lock:
            entry = self._sessions.get(token)
            if (entry is None or entry.expires_at <= utcnow() or self._closed
                    or not secrets.compare_digest(entry.cookie_digest, hashlib.sha256(cookie.encode()).hexdigest())):
                raise JobMailError("secure_setup_expired", "安全配置已过期或不属于当前浏览器，请重新开始", 410)
            if phase is not None and entry.phase != phase:
                raise JobMailError("secure_setup_conflict", "该配置步骤已经使用，不能自动重放", 409)
            return entry

    def _fence(self, entry: SetupSession) -> None:
        with self._lock:
            if (self._sessions.get(entry.token) is not entry or entry.expires_at <= utcnow()
                    or self.sync._stopped.is_set()):
                raise JobMailError("secure_setup_expired", "安全配置已取消或过期", 410)
        with self.sessions() as session:
            if (workspace_id(session) != entry.scope_id
                    or self._snapshot(self.sync._connection(session)) != entry.connection_snapshot):
                raise JobMailError("secure_setup_conflict", "工作区或连接已变化，请重新开始", 409)

    def _drop(self, token: str) -> None:
        entry = self._sessions.pop(token, None)
        if entry is not None:
            entry.phase = "closed"
            entry.secret = None
            if entry.timer:
                entry.timer.cancel()
            if entry.transport:
                entry.transport.cancel()

    def cancel_expired(self, token: str) -> None:
        with self._lock:
            self._drop(token)

    def cancel(self, token: str, cookie: str) -> dict[str, bool]:
        self._entry(token, cookie)
        with self._lock:
            self._drop(token)
        return {"cancelled": True}

    def test(self, payload: TestInput, cookie: str) -> dict[str, Any]:
        if payload.explicit_test_consent is not True:
            raise JobMailError("secure_consent_required", "测试登录和列出目录需要明确确认", 422)
        entry = self._entry(payload.setup_token, cookie, phase="started")
        self._fence(entry)
        if entry.purpose != "connect":
            raise JobMailError("secure_setup_conflict", "此配置会话不能连接邮箱", 409)
        try:
            with self._lock:
                entry = self._entry(payload.setup_token, cookie, phase="started")
                entry.phase = "testing"
                entry.address = payload.email
                entry.secret = payload.authorization_code
                payload.authorization_code = SecretStr("")
                entry.transport = self.transport_factory(entry.address, "temporary", enabled=True, vault=TemporaryVault(entry))
            self._fence(entry)
            if entry.transport is None:
                raise JobMailError("secure_setup_expired", "安全配置已取消", 410)
            folders = entry.transport.discover()
            self._fence(entry)
            with self._lock:
                entry = self._entry(payload.setup_token, cookie, phase="testing")
                entry.folders = folders
                entry.phase = "tested"
            return {"setup_token": entry.token, "email_masked": entry.address[:2] + "***@qq.com",
                    "expires_at": timestamp(entry.expires_at), "folders": folders}
        except Exception:
            with self._lock:
                self._drop(entry.token)
            raise JobMailError("secure_test_failed", "连接测试未完成；临时授权码已清除，请检查后重新开始", 409) from None

    def save(self, payload: SaveInput, cookie: str) -> dict[str, Any]:
        if payload.explicit_save_consent is not True:
            raise JobMailError("secure_consent_required", "保存到本机安全库及所选范围需要独立确认", 422)
        entry = self._entry(payload.setup_token, cookie, phase="tested")
        self._fence(entry)
        selected = list(dict.fromkeys(payload.folder_ids))
        mapping = {folder["id"]: folder for folder in entry.folders if folder["selectable"]}
        if not selected or any(folder_id not in mapping for folder_id in selected):
            raise JobMailError("secure_input_invalid", "只能选择本次测试发现的可用目录", 422)
        with self._lock:
            entry = self._entry(payload.setup_token, cookie, phase="tested")
            entry.phase = "saving"
            self._active_operations += 1
        reference = entry.scope_id + "." + uuid4().hex
        operation_id = str(uuid4())
        intent_written = False
        committed = False
        vault: CredentialVault | None = None
        try:
            vault = self._vault()
            transport = entry.transport
            if transport is None or entry.secret is None:
                raise JobMailError("secure_setup_expired", "配置已过期", 410)
            folders = [mapping[key]["name"] for key in selected]
            # Only read-only SELECT metadata; saving never fetches a body.
            cursors = {}
            for folder in folders:
                self._fence(entry)
                validity, highest = transport.baseline(folder)
                cursors[folder] = {"uidvalidity": validity, "uid": 0 if payload.backfill_days else highest}
            self._fence(entry)
            with self._lock, self.sessions() as session:
                if self._sessions.get(entry.token) is not entry or entry.expires_at <= utcnow():
                    raise JobMailError("secure_setup_expired", "安全配置已取消或过期", 410)
                begin_immediate(session)
                if (workspace_id(session) != entry.scope_id
                        or self._snapshot(self.sync._connection(session)) != entry.connection_snapshot):
                    raise JobMailError("secure_setup_conflict", "连接已变化", 409)
                if session.scalar(select(JobMailCredentialOperation.id).where(
                        JobMailCredentialOperation.scope_id == entry.scope_id,
                        JobMailCredentialOperation.state.in_(["storing", "deleting", "delete_pending"]))):
                    raise JobMailError("secure_setup_conflict", "存在尚未清理的安全凭据操作", 409)
                session.add(JobMailCredentialOperation(id=operation_id, scope_id=entry.scope_id,
                    credential_ref=reference, state="storing", catalog_json=canonical(entry.folders)))
                session.commit()
                intent_written = True
            ready_transport = self.transport_factory(entry.address, reference, enabled=True, vault=vault)
            if entry.secret is None:
                raise JobMailError("secure_setup_expired", "安全配置已过期", 410)
            secret = entry.secret.get_secret_value()
            # A disconnect fences immediately, but cleanup must wait for any
            # in-flight write to finish. A cancelled writer must never write
            # after another request has declared the reference deleted.
            with self._credential_lock:
                self._fence(entry)
                vault.set(reference, secret)
                if not secrets.compare_digest(vault.get(reference).encode(), secret.encode()):
                    raise TransportUnavailable("credential_verification_failed")
            self._fence(entry)
            with self._lock, self.sessions() as session:
                if self._sessions.get(entry.token) is not entry or entry.expires_at <= utcnow():
                    raise JobMailError("secure_setup_expired", "安全配置已取消或过期", 410)
                begin_immediate(session)
                if (workspace_id(session) != entry.scope_id
                        or self._snapshot(self.sync._connection(session)) != entry.connection_snapshot):
                    raise JobMailError("secure_setup_conflict", "连接已变化", 409)
                connection = self.sync._connection(session)
                if connection is None:
                    connection = JobMailConnection(id=str(uuid4()), scope_id=entry.scope_id, scope_version=1)
                    session.add(connection)
                else:
                    connection.scope_version += 1
                connection.email = entry.address
                connection.provider = "qq"
                connection.status = "connected"
                connection.credential_ref = reference
                connection.folders_json = canonical(folders)
                connection.cursor_json = canonical(cursors)
                connection.sync_mode = "manual"
                connection.interval_minutes = 15
                connection.ai_enabled = False
                connection.start_at = utcnow() - timedelta(days=payload.backfill_days)
                connection.next_run_at = None
                connection.not_before_at = None
                operation = session.get(JobMailCredentialOperation, operation_id)
                if operation is None or operation.state != "storing":
                    raise JobMailError("secure_setup_conflict", "安全操作已变化", 409)
                operation.state = "committed"
                operation.updated_at = utcnow()
                session.commit()
                committed = True
                self.local_only = True
                self.sync.transport = ready_transport
            return self.sync.status()
        except Exception:
            if intent_written and not committed:
                # A COMMIT can succeed even if its acknowledgement/callback
                # fails. Never compensate by deleting a durably committed
                # credential. If readback itself is unavailable, retain the
                # intent/reference for explicit recovery rather than guessing.
                try:
                    with self.sessions() as session:
                        operation = session.get(JobMailCredentialOperation, operation_id)
                        connection = self.sync._connection(session)
                        committed = bool(operation and operation.state == "committed"
                                         and connection and connection.credential_ref == reference)
                except Exception:
                    raise JobMailError("secure_save_result_unknown", "保存结果暂时无法核实，请查看当前连接状态，不要重复保存", 409) from None
            if committed:
                self.rebuild_transport()
                raise JobMailError("secure_save_result_unknown", "保存结果返回异常，请查看当前连接状态，不要重复保存", 409) from None
            if intent_written and vault is not None:
                self._cleanup_reference(operation_id, reference, vault)
            raise JobMailError("secure_credential_write_failed", "保存未完成；请查看凭据清理状态后再操作", 409) from None
        finally:
            with self._lock:
                self._drop(entry.token)
                self._active_operations -= 1
                self._release_closed_owner()

    def _cleanup_reference(self, operation_id: str, reference: str, vault: CredentialVault | None) -> bool:
        cleaned = False
        try:
            if vault is None:
                raise TransportUnavailable("secure_store_unavailable")
            with self._credential_lock:
                vault.delete(reference)
            cleaned = True
        except Exception:
            pass
        with self.sessions() as session:
            begin_immediate(session)
            operation = session.get(JobMailCredentialOperation, operation_id)
            if operation:
                operation.state = "deleted" if cleaned else "delete_pending"
                operation.updated_at = utcnow()
            session.commit()
        return cleaned

    def disconnect(self, payload: DeleteInput, cookie: str) -> dict[str, Any]:
        if payload.explicit_delete_consent is not True:
            raise JobMailError("secure_consent_required", "断开并删除本机授权码需要明确确认", 422)
        entry = self._entry(payload.setup_token, cookie, phase="started")
        self._fence(entry)
        if entry.purpose != "disconnect":
            raise JobMailError("secure_setup_conflict", "此会话不能删除凭据", 409)
        with self._lock:
            entry = self._entry(payload.setup_token, cookie, phase="started")
            entry.phase = "deleting"
            self._active_operations += 1
        try:
            with self._lock, self.sessions() as session:
                self._entry(payload.setup_token, cookie, phase="deleting")
                begin_immediate(session)
                connection = self.sync._connection(session)
                if workspace_id(session) != entry.scope_id or self._snapshot(connection) != entry.connection_snapshot:
                    raise JobMailError("secure_setup_conflict", "连接已变化", 409)
                if connection:
                    self.sync._cancel_runs(session, connection.id)
                    connection.scope_version += 1
                    connection.status = "credential_delete_pending"
                    connection.sync_mode = "manual"
                    connection.next_run_at = None
                operations = list(session.scalars(select(JobMailCredentialOperation).where(
                    JobMailCredentialOperation.scope_id == entry.scope_id,
                    JobMailCredentialOperation.state.in_(["committed", "storing", "deleting", "delete_pending"]))))
                cleanup = [(operation.id, operation.credential_ref) for operation in operations]
                if connection and connection.credential_ref and not any(reference == connection.credential_ref for _, reference in cleanup):
                    missing = JobMailCredentialOperation(id=str(uuid4()), scope_id=entry.scope_id,
                        credential_ref=connection.credential_ref, state="deleting")
                    session.add(missing)
                    cleanup.append((missing.id, missing.credential_ref))
                for operation in operations:
                    operation.state = "deleting"
                    operation.updated_at = utcnow()
                fenced_snapshot = self._snapshot(connection)
                session.commit()
            transport = self.sync.transport
            if isinstance(transport, QQIMAPTransport):
                transport.cancel()
            self.sync.transport = DisabledTransport()
            try:
                vault: CredentialVault | None = self._vault()
            except JobMailError:
                vault = None
            cleaned = all([self._cleanup_reference(operation_id, reference, vault)
                           for operation_id, reference in cleanup])
            with self.sessions() as session:
                begin_immediate(session)
                connection = self.sync._connection(session)
                if workspace_id(session) != entry.scope_id or self._snapshot(connection) != fenced_snapshot:
                    raise JobMailError("secure_setup_conflict", "连接已变化，凭据清理结果已保留，请核对当前状态", 409)
                if connection:
                    if cleaned:
                        connection.credential_ref = None
                        connection.status = "disconnected"
                    else:
                        connection.status = "credential_delete_pending"
                session.commit()
            if not cleaned:
                raise JobMailError("credential_delete_pending", "已停止同步，但本机安全库删除未完成；请重试清理", 409)
            return self.sync.status()
        finally:
            with self._lock:
                self._drop(entry.token)
                self._active_operations -= 1
                self._release_closed_owner()

    def _release_closed_owner(self) -> None:
        if (self._closed and self._shutdown_fenced and self._active_operations == 0
                and self._process_lock is not None):
            self._process_lock.close()
            self._process_lock = None

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self.enabled = False
            self.sync._stopped.set()
            for token in list(self._sessions):
                self._drop(token)
            if isinstance(self.sync.transport, QQIMAPTransport):
                self.sync.transport.cancel()
        # Complete the durable run fence before yielding ownership to another
        # process. A still-blocked credential operation retains the OS lock.
        self.sync.shutdown()
        with self._lock:
            self._shutdown_fenced = True
            self._release_closed_owner()


def local_browser(request: Request, *, require_origin: bool) -> bool:
    try:
        hosts = request.headers.getlist("host")
        if len(hosts) != 1 or any(char in hosts[0] for char in "@/\\ \r\n\t"):
            return False
        client = request.client
        if client is None or not ipaddress.ip_address(client.host).is_loopback:
            return False
        host = request.url.hostname or ""
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            return False
        if request.url.scheme not in {"http", "https"}:
            return False
        if require_origin:
            origins = request.headers.getlist("origin")
            if len(origins) != 1 or origins[0] != f"{request.url.scheme}://{request.url.netloc}":
                return False
        return not any(key == "forwarded" or key.startswith("x-forwarded-") for key in request.headers)
    except ValueError:
        return False


async def protected_payload(request: Request) -> dict[str, Any]:
    # Perform admission before consuming *any* secret-bearing request bytes.
    if not local_browser(request, require_origin=True):
        raise JobMailError("local_browser_required", "只能由本机同源安全配置页面操作", 403)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise JobMailError("secure_input_invalid", "安全配置只接受JSON表单", 415)
    if request.query_params:
        raise JobMailError("secure_input_invalid", "安全配置不能通过URL传递参数", 422)
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if not 0 <= int(length) <= MAX_SETUP_BYTES:
                raise ValueError("invalid length")
        except ValueError:
            raise JobMailError("secure_input_invalid", "安全表单超过大小限制", 413) from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_SETUP_BYTES:
            raise JobMailError("secure_input_invalid", "安全表单超过大小限制", 413)
        body.extend(chunk)
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("invalid object")
        return value
    except Exception:
        raise JobMailError("secure_input_invalid", "安全表单无效", 422) from None
    finally:
        body.clear()


def register_secure_setup_routes(app: FastAPI, setup: JobMailSecureSetup) -> None:
    def response(value: dict[str, Any], status: int = 200) -> JSONResponse:
        return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.get("/api/job-mail/secure-setup/status")
    def status(request: Request) -> JSONResponse:
        return response(setup.status(local=local_browser(request, require_origin=False)))

    @app.post("/api/job-mail/secure-setup/{step}")
    async def perform(step: str, request: Request) -> JSONResponse:
        try:
            if not setup.enabled:
                raise JobMailError("local_setup_not_enabled", "请用本机安全配置启动选项开启此入口", 409)
            value = await protected_payload(request)
            cookie = request.cookies.get(COOKIE, "")
            if step == "start":
                start = StartInput.model_validate(value)
                cookie = secrets.token_urlsafe(32)
                result = response(await run_in_threadpool(setup.start, cookie=cookie, purpose=start.purpose, consent=start.explicit_setup_consent))
                result.set_cookie(COOKIE, cookie, max_age=setup.ttl_seconds, httponly=True,
                                  secure=request.url.scheme == "https", samesite="strict", path="/api/job-mail")
                return result
            if step == "test":
                command = TestInput.model_validate(value)
                value.clear()
                return response(await run_in_threadpool(setup.test, command, cookie))
            if step == "save":
                result = response(await run_in_threadpool(setup.save, SaveInput.model_validate(value), cookie))
                result.delete_cookie(COOKIE, path="/api/job-mail")
                return result
            if step == "cancel":
                token = TokenInput.model_validate(value)
                result = response(await run_in_threadpool(setup.cancel, token.setup_token, cookie))
                result.delete_cookie(COOKIE, path="/api/job-mail")
                return result
            raise JobMailError("secure_input_invalid", "安全操作不存在", 404)
        except ValidationError:
            return response({"error": "安全表单字段无效", "error_code": "secure_input_invalid"}, 422)
        except JobMailError as exc:
            return response({"error": str(exc), "error_code": exc.code}, exc.status)
