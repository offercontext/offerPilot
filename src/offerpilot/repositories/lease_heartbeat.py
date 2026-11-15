from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event, Thread, current_thread
from types import TracebackType

from sqlalchemy import text, update
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import OpportunityFitReviewStage


DEFAULT_OPPORTUNITY_FIT_LEASE_SECONDS = 120.0
DEFAULT_OPPORTUNITY_FIT_HEARTBEAT_INTERVAL_SECONDS = 30.0


class LeaseHeartbeat:
    """Renew one fenced Opportunity Fit Provider lease while work is active."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        stage_id: int,
        stage_generation: int,
        provider_call_token: str,
        lease_seconds: float = DEFAULT_OPPORTUNITY_FIT_LEASE_SECONDS,
        interval_seconds: float = DEFAULT_OPPORTUNITY_FIT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if interval_seconds <= 0 or interval_seconds >= lease_seconds:
            raise ValueError("interval_seconds must be positive and shorter than lease_seconds")
        self._session_factory = session_factory
        self._stage_id = stage_id
        self._stage_generation = stage_generation
        self._provider_call_token = provider_call_token
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._stop = Event()
        self._lost = Event()
        self._thread: Thread | None = None

    @property
    def lost_ownership(self) -> bool:
        return self._lost.is_set()

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> LeaseHeartbeat:
        if self._thread is not None:
            raise RuntimeError("lease heartbeat already started")
        self._thread = Thread(
            target=self._run,
            name=f"opportunity-fit-lease-{self._stage_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(1.0, self._interval_seconds * 2.0))
            if thread.is_alive():
                self._lost.set()

    def __enter__(self) -> LeaseHeartbeat:
        return self.start()

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            if not self._renew_once():
                return

    def _renew_once(self) -> bool:
        if self._stop.is_set():
            return False
        try:
            with self._session_factory() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                if self._stop.is_set():
                    session.rollback()
                    return False
                stage = session.get(OpportunityFitReviewStage, self._stage_id)
                now = datetime.now(timezone.utc)
                lease_expires_at = stage.lease_expires_at if stage is not None else None
                if lease_expires_at is not None and lease_expires_at.utcoffset() is None:
                    lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
                if self._stop.is_set():
                    session.rollback()
                    return False
                # Legacy raw SQL writes include a UTC offset; ORM writes do not.
                # Compare parsed instants under the write lock, before the CAS.
                if lease_expires_at is None or lease_expires_at <= now:
                    session.rollback()
                    self._lost.set()
                    return False
                expires_at = now + timedelta(seconds=self._lease_seconds)
                result = session.execute(
                    update(OpportunityFitReviewStage)
                    .where(
                        OpportunityFitReviewStage.id == self._stage_id,
                        OpportunityFitReviewStage.stage_generation == self._stage_generation,
                        OpportunityFitReviewStage.provider_call_token == self._provider_call_token,
                        OpportunityFitReviewStage.status == "generating",
                        OpportunityFitReviewStage.lease_expires_at.is_not(None),
                        OpportunityFitReviewStage.lease_expires_at > now,
                    )
                    .values(lease_expires_at=expires_at)
                    .execution_options(synchronize_session=False)
                )
                if self._stop.is_set():
                    session.rollback()
                    return False
                if getattr(result, "rowcount", 0) != 1:
                    session.rollback()
                    self._lost.set()
                    return False
                session.commit()
            return True
        except Exception:
            self._lost.set()
            return False
