"""One bounded shared manual/scheduled sync pipeline, offline by default."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from .extraction import ParsedMail, clean_text, recognize, Candidate
from .model_adapter import Extractor, OfflineRuleExtractor
from .models import JobMailConnection, JobMailSyncRun, JobMailExtractionAttempt
from .review import JobMailError, ingest_candidate, workspace_id, rebind_retained_suggestions
from .transport import DisabledTransport, FakeIMAPTransport, MailTransport, TransportUnavailable

DAILY_LIMIT = 100


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _transaction(session: Session) -> None:
    session.execute(text("BEGIN IMMEDIATE"))


class JobMailSyncService:
    def __init__(self, sessions: sessionmaker[Session], transport: MailTransport | None = None,
                 extractor: Extractor | None = None) -> None:
        self.sessions = sessions
        self.transport = transport or DisabledTransport()
        self.extractor = extractor or OfflineRuleExtractor()
        self.owner = str(uuid4())
        self._stopped = threading.Event()
        self._threads: list[threading.Thread] = []
        self._thread_lock = threading.Lock()

    def _connection(self, session: Session) -> JobMailConnection | None:
        return session.scalar(select(JobMailConnection).where(
            JobMailConnection.scope_id == workspace_id(session)))

    @staticmethod
    def _budget(connection: JobMailConnection) -> dict[str, Any]:
        value = json.loads(connection.budget_json)
        return value if value.get("day") == _now().date().isoformat() else {
            "day": _now().date().isoformat(), "used": 0}

    @staticmethod
    def _run(run: JobMailSyncRun | None) -> dict[str, Any] | None:
        if run is None:
            return None
        expired = run.status == "running" and run.lease_until is not None and run.lease_until <= _now()
        return {"id": run.id, "status": "interrupted" if expired else run.status, "trigger": run.trigger,
                "progress": json.loads(run.progress_json), "error_code": "lease_expired" if expired else run.error_code,
                "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at)}

    def status(self) -> dict[str, Any]:
        with self.sessions() as session:
            connection = self._connection(session)
            summary = None
            run = None
            used = 0
            if connection:
                used = int(self._budget(connection).get("used", 0))
                email = connection.email
                summary = {"id": connection.id, "email_masked":
                           (email[:2] + "***@" + email.split("@")[-1]) if "@" in email else "合成邮箱",
                           "provider": connection.provider, "status": connection.status,
                           "folders": json.loads(connection.folders_json),
                           "scope_version": connection.scope_version, "sync_mode": connection.sync_mode,
                           "interval_minutes": connection.interval_minutes, "ai_enabled": False,
                           "start_at": _iso(connection.start_at),
                           "next_run_at": _iso(connection.next_run_at),
                           "last_attempt_at": _iso(connection.last_attempt_at),
                           "last_success_at": _iso(connection.last_success_at),
                           "not_before_at": _iso(connection.not_before_at)}
                run = session.scalar(select(JobMailSyncRun).where(
                    JobMailSyncRun.connection_id == connection.id).order_by(
                    JobMailSyncRun.started_at.desc(), JobMailSyncRun.id.desc()).limit(1))
            synthetic = isinstance(self.transport, FakeIMAPTransport)
            return {"capabilities": {"real_connection": False, "ai_recognition": False,
                                      "synthetic_connection": synthetic},
                    "connection": summary, "run": self._run(run),
                    "budget": {"limit": DAILY_LIMIT, "used": used,
                               "remaining": max(0, DAILY_LIMIT - used)},
                    "execution_location": "本地/自托管后端",
                    "available_folders": self.transport.folders() if synthetic else [],
                    "recognition_mode": "offline_rules",
                    "retention": {"pending_days": 90, "processed_days": 30},
                    "notice": "当前为离线规则提议；真实QQ连接与外部AI尚未开放。后端停止时不检查邮件。"}

    @staticmethod
    def _folders(value: Any) -> list[str]:
        if not isinstance(value, list) or not 0 < len(value) <= 20 or any(
                not isinstance(item, str) or not item or len(item) > 200 for item in value):
            raise JobMailError("invalid_folders", "请选择1至20个文件夹", 422)
        return list(dict.fromkeys(value))

    def connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("provider") != "synthetic" or not isinstance(self.transport, FakeIMAPTransport):
            raise JobMailError("secure_connection_unavailable", "安全凭据配置及QQ联调尚未开放，请使用手动粘贴", 409)
        folders = self._folders(payload.get("folders"))
        if any(folder not in self.transport.folders() for folder in folders):
            raise JobMailError("invalid_folders", "文件夹不可用", 422)
        backfill = payload.get("backfill_days", 0)
        if backfill not in (0, 7) or isinstance(backfill, bool):
            raise JobMailError("invalid_backfill", "仅支持新邮件或最近7天", 422)
        # Read-only metadata establishes a baseline. Connecting never fetches bodies.
        cursors = {}
        for folder in folders:
            validity, latest = self.transport.baseline(folder)
            cursors[folder] = {"uidvalidity": validity, "uid": 0 if backfill else latest}
        now = _now()
        with self.sessions() as session:
            _transaction(session)
            connection = self._connection(session)
            if connection is None:
                connection = JobMailConnection(id=str(uuid4()), scope_id=workspace_id(session))
                session.add(connection)
            else:
                connection.scope_version += 1
            self._cancel_runs(session, connection.id)
            connection.email = "demo@qq.com"
            connection.provider = "synthetic"
            connection.status = "connected"
            connection.folders_json = json.dumps(folders)
            connection.cursor_json = json.dumps(cursors)
            connection.sync_mode = "manual"
            connection.interval_minutes = 15
            connection.ai_enabled = False
            connection.start_at = now - timedelta(days=backfill)
            connection.next_run_at = None
            connection.not_before_at = None
            connection.failure_count = 0
            session.commit()
        return self.status()

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - {"sync_mode", "interval_minutes", "folders", "ai_enabled"}:
            raise JobMailError("invalid_settings", "不支持此设置", 422)
        if payload.get("ai_enabled") is True:
            raise JobMailError("ai_not_authorized", "外部AI需另行授权数据与服务商", 409)
        mode = payload.get("sync_mode")
        interval = payload.get("interval_minutes")
        if mode is not None and mode not in {"manual", "automatic"}:
            raise JobMailError("invalid_mode", "同步方式无效", 422)
        if interval is not None and (type(interval) is not int or not 5 <= interval <= 1440):
            raise JobMailError("invalid_interval", "同步间隔须为5至1440分钟", 422)
        with self.sessions() as session:
            _transaction(session)
            connection = self._connection(session)
            if connection is None or connection.status != "connected":
                raise JobMailError("not_connected", "请先连接邮箱", 409)
            if "folders" in payload:
                folders = self._folders(payload["folders"])
                if any(f not in self.transport.folders() for f in folders):
                    raise JobMailError("invalid_folders", "文件夹不可用", 422)
                if folders != json.loads(connection.folders_json):
                    cursors = json.loads(connection.cursor_json)
                    for folder in folders:
                        if folder not in cursors:
                            validity, latest = self.transport.baseline(folder)
                            cursors[folder] = {"uidvalidity": validity, "uid": latest}
                    connection.cursor_json = json.dumps({f: cursors[f] for f in folders})
                    connection.folders_json = json.dumps(folders)
                    old_scope = connection.scope_version
                    connection.scope_version += 1
                    rebind_retained_suggestions(session, connection, old_scope)
                    self._cancel_runs(session, connection.id)
            if mode is not None:
                connection.sync_mode = mode
            if interval is not None:
                connection.interval_minutes = interval
            connection.next_run_at = (_now() + timedelta(minutes=connection.interval_minutes)
                                      if connection.sync_mode == "automatic" else None)
            session.commit()
        return self.status()

    def start_sync(self, *, trigger: str = "manual") -> dict[str, Any]:
        now = _now()
        with self.sessions() as session:
            _transaction(session)
            connection = self._connection(session)
            if connection is None or connection.status != "connected":
                raise JobMailError("not_connected", "邮箱未连接", 409)
            active = session.scalar(select(JobMailSyncRun).where(
                JobMailSyncRun.connection_id == connection.id, JobMailSyncRun.status == "running"))
            if active:
                if active.lease_until and active.lease_until > now:
                    return self._run(active) or {}
                if active.owner_token == self.owner:
                    with self._thread_lock:
                        if any(t.is_alive() for t in self._threads):
                            # A timed-out provider may still be physically alive.
                            # Never add another local worker behind its lease.
                            raise JobMailError("worker_still_stopping", "旧同步仍在停止，请稍后核对结果", 409)
                active.status = "interrupted"
                active.error_code = "lease_expired"
                active.finished_at = now
            if trigger == "automatic" and (connection.sync_mode != "automatic"
                    or connection.next_run_at is None or connection.next_run_at > now):
                return {"status": "not_due"}
            if connection.not_before_at and connection.not_before_at > now:
                raise JobMailError("sync_throttled", "同步过于频繁，请等待60秒节流结束", 429)
            with self._thread_lock:
                if any(t.is_alive() for t in self._threads):
                    raise JobMailError("worker_still_stopping", "旧同步仍在停止，请稍后核对结果", 409)
            run = JobMailSyncRun(id=str(uuid4()), connection_id=connection.id,
                scope_version=connection.scope_version, trigger=trigger, status="running",
                owner_token=self.owner, lease_until=now + timedelta(seconds=90), started_at=now,
                progress_json=json.dumps({"scanned": 0, "candidates": 0, "duplicates": 0,
                                          "deferred": 0, "failed_folders": []}))
            session.add(run)
            connection.last_attempt_at = now
            connection.not_before_at = now + timedelta(seconds=60)
            if connection.sync_mode == "automatic":
                connection.next_run_at = now + timedelta(minutes=connection.interval_minutes)
            session.commit()
            result = self._run(run) or {}
            run_id = run.id
        thread = threading.Thread(target=self._execute, args=(run_id,), daemon=True,
                                  name="offerpilot-mail-sync")
        with self._thread_lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()
        return result

    def _fenced(self, session: Session, run_id: str) -> tuple[JobMailSyncRun, JobMailConnection] | None:
        run = session.get(JobMailSyncRun, run_id)
        if run is None or run.status != "running" or run.cancel_requested or self._stopped.is_set():
            return None
        connection = session.get(JobMailConnection, run.connection_id)
        if (connection is None or connection.status != "connected"
                or connection.scope_version != run.scope_version or run.owner_token != self.owner
                or run.lease_until is None or run.lease_until <= _now()):
            return None
        run.lease_until = _now() + timedelta(seconds=90)
        return run, connection

    def _execute(self, run_id: str) -> None:
        try:
            with self.sessions() as session:
                fenced = self._fenced(session, run_id)
                if not fenced:
                    return
                folders = json.loads(fenced[1].folders_json)
            for folder in folders:
                with self.sessions() as session:
                    _transaction(session)
                    fenced = self._fenced(session, run_id)
                    if not fenced:
                        return
                    cursor = json.loads(fenced[1].cursor_json).get(folder, {"uidvalidity": "", "uid": 0})
                    since = fenced[1].start_at
                    session.commit()
                try:
                    batch = self.transport.read(folder, cursor["uidvalidity"], cursor["uid"], limit=50, since=since)
                except TransportUnavailable as exc:
                    self._folder_failed(run_id, folder, str(exc))
                    continue
                deferred = False
                for mail in batch.messages:
                    outcome = self._process_mail(run_id, mail)
                    if outcome == "stop":
                        return
                    if outcome == "budget":
                        deferred = True
                        break
                if not deferred:
                    with self.sessions() as session:
                        _transaction(session)
                        fenced = self._fenced(session, run_id)
                        if not fenced:
                            return
                        cursors = json.loads(fenced[1].cursor_json)
                        cursors[folder] = {"uidvalidity": batch.uidvalidity, "uid": batch.last_uid}
                        fenced[1].cursor_json = json.dumps(cursors)
                        session.commit()
                if batch.has_more:
                    with self.sessions() as session:
                        _transaction(session)
                        fenced = self._fenced(session, run_id)
                        if not fenced:
                            return
                        progress = json.loads(fenced[0].progress_json)
                        progress["deferred"] += 1
                        fenced[0].progress_json = json.dumps(progress)
                        session.commit()
            self._finish(run_id)
        except Exception:
            # Exceptions may contain provider/body content. Store only a fixed code.
            self._finish(run_id, error="sync_failed")

    @staticmethod
    def _advance(connection: JobMailConnection, mail: ParsedMail) -> None:
        cursors = json.loads(connection.cursor_json)
        cursors[mail.folder] = {"uidvalidity": mail.uidvalidity, "uid": mail.uid}
        connection.cursor_json = json.dumps(cursors)

    def _process_mail(self, run_id: str, mail: ParsedMail) -> str:
        # Only a bounded source excerpt is retained while a provider result is
        # unknown. Long/multi-part evidence is explicitly review-only.
        mail = replace(mail, body_text=mail.body_text[:12000],
                       truncated=mail.truncated or len(mail.body_text) > 12000)
        key = hashlib.sha256((mail.fingerprint + "\0" + mail.received_at).encode()).hexdigest()
        with self.sessions() as session:
            _transaction(session)
            fenced = self._fenced(session, run_id)
            if not fenced:
                return "stop"
            run, connection = fenced
            progress = json.loads(run.progress_json)
            received = datetime.fromisoformat(mail.received_at).astimezone(timezone.utc).replace(tzinfo=None)
            candidate = (connection.start_at is None or received >= connection.start_at) and bool(recognize(mail))
            if not candidate:
                progress["scanned"] += 1
                self._advance(connection, mail)
                run.progress_json = json.dumps(progress)
                session.commit()
                return "done"
            source_key = hashlib.sha256((connection.id + key).encode()).hexdigest()
            prior = session.scalar(select(JobMailExtractionAttempt).where(
                JobMailExtractionAttempt.scope_id == connection.scope_id,
                JobMailExtractionAttempt.source_key == source_key))
            if prior is not None:
                # A cancelled/crashed provider result may have been charged.
                # Preserve an explicit manual-review source, never re-invoke.
                if prior.state == "running" and prior.run_id != run_id:
                    self._unknown_attempt(session, prior, connection.scope_version)
                if prior.state != "running":
                    ingest_candidate(session, asdict(mail), [], connection_id=connection.id,
                                     scope_version=connection.scope_version)
                progress["duplicates"] += 1
                progress["scanned"] += 1
                self._advance(connection, mail)
                run.progress_json = json.dumps(progress)
                session.commit()
                return "done"
            budget = self._budget(connection)
            if int(budget["used"]) >= DAILY_LIMIT:
                progress["deferred"] += 1
                run.progress_json = json.dumps(progress)
                session.commit()
                return "budget"
            attempt = JobMailExtractionAttempt(id=str(uuid4()), scope_id=connection.scope_id,
                source_key=source_key, connection_id=connection.id, run_id=run_id,
                state="running", payload_json=json.dumps(asdict(mail)), created_at=_now())
            session.add(attempt)
            budget["used"] += 1
            connection.budget_json = json.dumps(budget)
            session.commit()
            attempt_id = attempt.id
        # Crucially outside any SQLite transaction: cancelling/disconnecting or
        # editing a business record is never blocked by a remote provider.
        try:
            candidates = self.extractor.extract(mail)
            failed = False
        except Exception:
            candidates = [self._unknown_candidate(mail)]
            failed = True
        with self.sessions() as session:
            _transaction(session)
            completed_attempt = session.get(JobMailExtractionAttempt, attempt_id)
            fenced = self._fenced(session, run_id)
            if completed_attempt is None or completed_attempt.state != "running":
                return "stop"
            if not fenced:
                # Keep evidence for a future explicit retry/review, but never
                # publish a late result after revocation or cancellation.
                session.commit()
                return "stop"
            run, connection = fenced
            result = ingest_candidate(session, asdict(mail), [c.to_dict() for c in candidates],
                connection_id=connection.id, scope_version=connection.scope_version)
            completed_attempt.state = "unknown" if failed else "succeeded"
            completed_attempt.payload_json = "{}"
            completed_attempt.finished_at = _now()
            progress = json.loads(run.progress_json)
            progress["scanned"] += 1
            progress["candidates"] += len(candidates) if not result["deduplicated"] else 0
            progress["duplicates"] += int(result["deduplicated"])
            if failed:
                progress["recognition_failed"] = progress.get("recognition_failed", 0) + 1
            self._advance(connection, mail)
            run.progress_json = json.dumps(progress)
            session.commit()
        return "done"

    @staticmethod
    def _unknown_candidate(mail: ParsedMail) -> Candidate:
        return Candidate("manual_only", "custom", "", "unknown", None, None, "", "", "",
            {"notice": mail.body_text[:3000]} if mail.body_text else {},
            "识别结果未知或失败，可能已产生费用；不会自动重试，请人工核对原文。")

    def _unknown_attempt(self, session: Session, attempt: JobMailExtractionAttempt,
                         scope_version: int) -> None:
        payload = json.loads(attempt.payload_json)
        if payload:
            source = ParsedMail(**payload)
            ingest_candidate(session, payload, [self._unknown_candidate(source).to_dict()],
                             connection_id=attempt.connection_id, scope_version=scope_version)
        attempt.state = "unknown"
        attempt.payload_json = "{}"
        attempt.finished_at = _now()

    def _folder_failed(self, run_id: str, folder: str, code: str) -> None:
        with self.sessions() as session:
            _transaction(session)
            fenced = self._fenced(session, run_id)
            if not fenced:
                return
            progress = json.loads(fenced[0].progress_json)
            progress["failed_folders"].append(folder)
            fenced[0].progress_json = json.dumps(progress)
            fenced[0].error_code = "folder_read_failed"
            session.commit()

    def _finish(self, run_id: str, error: str = "") -> None:
        with self.sessions() as session:
            _transaction(session)
            fenced = self._fenced(session, run_id)
            if not fenced:
                return
            run, connection = fenced
            progress = json.loads(run.progress_json)
            failed = bool(error or progress["failed_folders"])
            run.status = "failed" if failed else "completed"
            run.finished_at = _now()
            run.error_code = error or run.error_code
            if failed:
                connection.failure_count += 1
                if connection.failure_count >= 3:
                    connection.sync_mode = "manual"
                    connection.next_run_at = None
            else:
                connection.failure_count = 0
                connection.last_success_at = _now()
            session.commit()

    @staticmethod
    def _cancel_runs(session: Session, connection_id: str) -> None:
        for run in session.scalars(select(JobMailSyncRun).where(
                JobMailSyncRun.connection_id == connection_id, JobMailSyncRun.status == "running")):
            run.cancel_requested = True
            run.status = "cancelled"
            run.finished_at = _now()

    def cancel_sync(self, run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            _transaction(session)
            connection = self._connection(session)
            run = session.get(JobMailSyncRun, run_id)
            if connection is None or run is None or run.connection_id != connection.id:
                raise JobMailError("run_not_found", "同步记录不存在", 404)
            if run.status == "running":
                run.cancel_requested = True
                run.status = "cancelled"
                run.finished_at = _now()
            session.commit()
            return self._run(run) or {}

    def disconnect(self) -> dict[str, Any]:
        with self.sessions() as session:
            _transaction(session)
            connection = self._connection(session)
            if connection:
                self._cancel_runs(session, connection.id)
                connection.status = "disconnected"
                connection.scope_version += 1
                connection.sync_mode = "manual"
                connection.next_run_at = None
                connection.credential_ref = None  # Only synthetic connections are currently admitted.
            session.commit()
        return self.status()

    def import_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = payload.get("body_text")
        if not isinstance(body, str) or not body.strip() or len(body.encode()) > 100_000:
            raise JobMailError("invalid_import", "请粘贴不超过100KB的单封邮件文本", 422)
        body, truncated = clean_text(body)
        subject = str(payload.get("subject", "手动提供的邮件"))[:500]
        sender = str(payload.get("sender", "手动提供，身份未验证"))[:320]
        digest = hashlib.sha256("\0".join((subject, sender, body)).encode()).hexdigest()
        received = payload.get("received_at", _iso(_now()))
        if isinstance(received, datetime):
            received = received.isoformat()
        mail = ParsedMail(0, "", "手动提供", "", str(received), sender, subject,
                          body, digest, truncated, "manual")
        # Manual paste is explicitly an offline preview in this batch. It must
        # never inherit an injected/provider-enabled mailbox recognizer.
        candidates = OfflineRuleExtractor().extract(mail)
        with self.sessions() as session:
            _transaction(session)
            result = ingest_candidate(session, asdict(mail), [c.to_dict() for c in candidates])
            session.commit()
            return result

    def tick(self) -> None:
        if self._stopped.is_set():
            return
        with self.sessions() as session:
            for attempt in session.scalars(select(JobMailExtractionAttempt).where(
                    JobMailExtractionAttempt.created_at < _now() - timedelta(days=90),
                    JobMailExtractionAttempt.payload_json != "{}")):
                attempt.payload_json = "{}"
                attempt.state = "unknown"
            session.commit()
        with self.sessions() as session:
            connection = self._connection(session)
            due = bool(connection and connection.status == "connected"
                       and connection.sync_mode == "automatic" and connection.next_run_at
                       and connection.next_run_at <= _now())
        if due:
            try:
                self.start_sync(trigger="automatic")
            except JobMailError:
                pass

    def shutdown(self) -> None:
        self._stopped.set()
        with self.sessions() as session:
            _transaction(session)
            for run in session.scalars(select(JobMailSyncRun).where(
                    JobMailSyncRun.owner_token == self.owner, JobMailSyncRun.status == "running")):
                run.status = "interrupted"
                run.finished_at = _now()
                run.error_code = "backend_stopped"
            session.commit()
        with self._thread_lock:
            for thread in self._threads:
                thread.join(timeout=1)
