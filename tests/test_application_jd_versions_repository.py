import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from offerpilot.db import init_database
from offerpilot.repositories.application_jd_versions import (
    ApplicationJDService,
    JDVersionConflictError,
    JDVersionValidationError,
)
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.jd import JDAnalysesRepository, JDAnalysisCreate
from offerpilot.repositories.resumes import ResumeCreate, ResumeMatchCreate, ResumesRepository


def _service(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    application = ApplicationsRepository(session_factory).create(
        ApplicationCreate(company_name="星云数据", position_name="后端工程师")
    )
    return ApplicationJDService(session_factory), application.id


def _valid(service, application_id, **overrides):
    values = {
        "application_id": application_id,
        "jd_text": "后端工程师\n负责 API 设计 🧭",
        "source_url": None,
        "source_kind": "ui",
        "expected_current_version_id": None,
        "idempotency_key": "jd-key-000000001",
    }
    values.update(overrides)
    return service.create_version(**values)


def test_rejects_invalid_jd_url_and_idempotency_inputs_without_inserting(tmp_path):
    service, application_id = _service(tmp_path)

    invalid_values = [None, 1, "", "   ", "x" * 60_001]
    for value in invalid_values:
        with pytest.raises(JDVersionValidationError):
            _valid(service, application_id, jd_text=value)

    invalid_urls = [1, object(), "relative/path", "javascript:alert(1)", "data:text/plain,x", "file:///tmp/a", "https://"]
    for url in invalid_urls:
        with pytest.raises(JDVersionValidationError):
            _valid(service, application_id, source_url=url)

    invalid_keys = ["short", "bad key with spaces", "中文幂等键", "x" * 129]
    for key in invalid_keys:
        with pytest.raises(JDVersionValidationError):
            _valid(service, application_id, idempotency_key=key)

    assert service.list_versions(application_id, 0, 50) == []


def test_expected_current_version_requires_strict_positive_integer_or_none(tmp_path):
    service, application_id = _service(tmp_path)
    valid = {
        "application_id": application_id,
        "jd_text": "后端工程师",
        "source_url": None,
        "source_kind": "ui",
        "idempotency_key": "jd-key-000000002",
    }
    for value in (True, False, "1", 0, -1):
        with pytest.raises(JDVersionValidationError):
            service.create_version(**valid, expected_current_version_id=value)

    result = service.create_version(**valid, expected_current_version_id=None)
    assert result.version.version_number == 1


def test_fingerprint_preserves_raw_jd_bytes_and_normalizes_url(tmp_path):
    service, application_id = _service(tmp_path)
    text = "  中文\n😀  "
    result = _valid(service, application_id, jd_text=text, source_url="  https://example.invalid/job  ")
    expected_payload = {
        "jd_text_utf8_b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "source_url": "https://example.invalid/job",
        "source_kind": "ui",
    }
    expected_request_hash = hashlib.sha256(
        json.dumps(expected_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert result.version.content_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert result.version.request_fingerprint_sha256 == expected_request_hash
    assert result.version.source_url == "https://example.invalid/job"

    whitespace = _valid(
        service,
        application_id,
        jd_text="different",
        source_url="   ",
        idempotency_key="jd-key-000000003",
        expected_current_version_id=result.version.id,
    )
    assert whitespace.version.source_url is None


def test_replay_precedes_cas_and_conflicts_do_not_mutate_original(tmp_path):
    service, application_id = _service(tmp_path)
    first = _valid(service, application_id)
    replay = _valid(service, application_id, expected_current_version_id=999)
    assert replay.replayed is True
    assert replay.version.id == first.version.id

    with pytest.raises(JDVersionConflictError) as error:
        _valid(service, application_id, jd_text="changed")
    assert error.value.code == "application_jd_idempotency_conflict"
    assert len(service.list_versions(application_id, 0, 50)) == 1

    second = _valid(
        service,
        application_id,
        jd_text="第二版",
        idempotency_key="jd-key-000000004",
        expected_current_version_id=first.version.id,
    )
    assert second.version.version_number == 2
    assert _valid(service, application_id, expected_current_version_id=None).version.id == first.version.id


def test_current_version_and_freeze_are_explicit_and_preview_is_stable(tmp_path):
    service, application_id = _service(tmp_path)
    text = "a" * 239 + "\n😀"
    result = _valid(service, application_id, jd_text=text, idempotency_key="jd-key-000000005")
    current = service.get_current(application_id)
    assert current is not None
    assert current.id == result.version.id
    frozen = service.freeze(current)
    assert frozen.jd_version_id == current.id
    assert frozen.jd_text == text
    assert service.list_versions(application_id, 0, 50)[0].preview == text[:240] + "…"

    with pytest.raises(JDVersionValidationError):
        service.require_current_version(application_id, 0)
    with pytest.raises(JDVersionConflictError):
        service.require_current_version(application_id, current.id + 1)


def test_analysis_and_match_claim_current_jd_in_atomic_write_transactions(tmp_path):
    db_path = tmp_path / "data.db"
    init_database(db_path)
    concurrent_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 5.0},
        pool_size=2,
        max_overflow=0,
    )
    session_factory = sessionmaker(bind=concurrent_engine, expire_on_commit=False)
    application = ApplicationsRepository(session_factory).create(
        ApplicationCreate(company_name="Acme", position_name="Backend")
    )
    service = ApplicationJDService(session_factory)
    first = service.create_version(
        application.id,
        jd_text="v1",
        source_url=None,
        source_kind="ui",
        expected_current_version_id=None,
        idempotency_key="jd-atomic-current-01",
    ).version
    resume = ResumesRepository(session_factory).create(
        ResumeCreate(name="Resume", parsed_data="Python")
    )
    analyses = JDAnalysesRepository(session_factory)
    matches = ResumesRepository(session_factory)

    def run_claim_with_blocked_update(claim, *, next_jd: str, expected_id: int, idempotency_key: str):
        check_started = Event()
        update_attempted = Event()
        release_check = Event()

        def coordinate_current_select(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "application_jd_versions" not in statement or "version_number" not in statement or check_started.is_set():
                return
            check_started.set()
            assert update_attempted.wait(timeout=5)
            assert release_check.wait(timeout=5)

        event.listen(concurrent_engine, "before_cursor_execute", coordinate_current_select)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                claim_future = pool.submit(claim)
                assert check_started.wait(timeout=5)

                def update():
                    update_attempted.set()
                    return service.create_version(
                        application.id,
                        jd_text=next_jd,
                        source_url=None,
                        source_kind="ui",
                        expected_current_version_id=expected_id,
                        idempotency_key=idempotency_key,
                    )

                update_future = pool.submit(update)
                assert update_attempted.wait(timeout=5)
                release_check.set()
                return claim_future.result(timeout=5), update_future.result(timeout=5)
        finally:
            event.remove(concurrent_engine, "before_cursor_execute", coordinate_current_select)

    analysis_result, changed = run_claim_with_blocked_update(
        lambda: analyses.create_for_current(
            JDAnalysisCreate(
                application_id=application.id,
                jd_version_id=first.id,
                jd_source="application_jd",
                jd_text="v1",
                result='{"ok":true}',
            ),
        ),
        next_jd="v2",
        expected_id=first.id,
        idempotency_key="jd-atomic-current-02",
    )

    assert analysis_result.jd_version_id == first.id
    assert changed.version.version_number == 2

    with pytest.raises(JDVersionConflictError):
        matches.create_match_for_current(
            ResumeMatchCreate(
                resume_id=resume.id,
                application_id=application.id,
                jd_version_id=first.id,
                jd_text="v1",
                result='{"match":true}',
            )
        )

    match_result, changed_again = run_claim_with_blocked_update(
        lambda: matches.create_match_for_current(
            ResumeMatchCreate(
                resume_id=resume.id,
                application_id=application.id,
                jd_version_id=changed.version.id,
                jd_text="v2",
                result='{"match":true}',
            ),
        ),
        next_jd="v3",
        expected_id=changed.version.id,
        idempotency_key="jd-atomic-current-03",
    )

    assert changed_again.version.version_number == 3
    assert match_result.jd_version_id == changed.version.id
    concurrent_engine.dispose()
