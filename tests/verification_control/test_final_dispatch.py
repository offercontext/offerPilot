"""N02: revoke a durable allowance while request evidence is still blocking."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import httpx
import pytest

from scripts.real_ai_control.archive import EvidenceArchive
from scripts.real_ai_control.protocol import SendPermissionDenied
from scripts.real_ai_control.transport import ControlledTransport, LogicalCall, logical_scope
from tests.verification_control.test_channel_transport import KEY, pipes, request


@pytest.mark.parametrize("pause", ["body", "last_metadata"])
@pytest.mark.parametrize(
    "fault", ["deny", "eof", "archive", "permit_expired", "logical_expired", "binding", "closed", "finishing"]
)
def test_revocation_during_request_archive_never_enters_inner(tmp_path, monkeypatch, pause, fault):
    paused, resume = threading.Event(), threading.Event()
    sends, permits = [], []
    with pipes(tmp_path, limit=1, timeout=5) as (channel, journal, broker, errors):
        archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))
        original_body, original_event = archive.body, archive.event
        original_permission = channel.permission

        def permission(*args):
            permit = original_permission(*args)
            permits.append(permit)
            return permit

        def wait_for_revocation():
            paused.set()
            assert resume.wait(3), "test did not release the request archival barrier"

        def body(kind, **kwargs):
            capture = original_body(kind, **kwargs)
            original_end = capture.end

            def end(*args, **kwargs):
                original_end(*args, **kwargs)
                if pause == "body" and kind == "provider-request":
                    wait_for_revocation()

            capture.end = end
            return capture

        def event(kind, **kwargs):
            row = original_event(kind, **kwargs)
            # The old event claimed dispatch; the fixed pre-admission event
            # must explicitly describe preparation, not an observed send.
            if pause == "last_metadata" and kind in {
                "provider-dispatch",
                "provider-dispatch-ready",
            }:
                wait_for_revocation()
            return row

        monkeypatch.setattr(channel, "permission", permission)
        monkeypatch.setattr(archive, "body", body)
        monkeypatch.setattr(archive, "event", event)

        def inner(req):
            sends.append(req)
            return httpx.Response(200, content=b"offline response")

        transport = ControlledTransport(httpx.MockTransport(inner), channel, archive)
        scope = LogicalCall()
        req = request()
        req.extensions["timeout"] = {name: 1 for name in ("read", "write", "connect", "pool")}

        def run():
            try:
                with logical_scope(scope):
                    response = transport.handle_request(req)
                    response.read()
                    response.close()
                return None
            except BaseException as error:
                return type(error).__name__

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            try:
                assert paused.wait(3), "request did not reach the archival pause"
                retained = journal.verify()["retained_cny"]
                assert journal.requests == 1 and len(permits) == 1
                if fault == "deny":
                    with pytest.raises(SendPermissionDenied):
                        channel.permission(request("B denied"), "completion-" + "f" * 32)
                    assert channel.sending_closed is True
                elif fault == "eof":
                    channel.writer.close()
                    channel.thread.join(timeout=2)
                    assert channel.dead.is_set()
                elif fault == "archive":
                    archive.failed("metadata", "OfflineConcurrentArchiveFailure")
                    assert archive.incomplete is True
                elif fault == "permit_expired":
                    permits[0].expires_at = time.monotonic() - 1
                elif fault == "logical_expired":
                    scope.deadline = time.monotonic() - 1
                elif fault == "binding":
                    req.headers["user-agent"] = "changed-after-request-archive"
                elif fault == "closed":
                    transport.close()
                else:
                    channel.finish(archive.summary()["events_sha256"], native_exit=1)
            finally:
                resume.set()
            outcome = future.result(timeout=3)
        proof = journal.verify()
        observed = dict(
            pause=pause,
            fault=fault,
            actual_inner_calls=len(sends),
            outcome=outcome,
            allowed_requests=proof["allowed_requests"],
            retained_cny_before=retained,
            retained_cny_after=proof["retained_cny"],
            permit_used=permits[0].used,
            observed_eof=channel.dead.is_set(),
            observed_deny=channel.sending_closed,
            raw_response_received=None if not sends else True,
            model_result=None,
            archive_complete=archive.summary()["complete"],
        )
        (tmp_path / "barrier-observation.json").write_text(json.dumps(observed, indent=2))
        transport.close()
        if not channel.dead.is_set():
            channel.finish(archive.summary()["events_sha256"], native_exit=1)
        assert sends == [], observed
        assert outcome in {"PermissionError", "TimeoutError", "ArchiveIncomplete"}, observed
        assert proof["allowed_requests"] == 1 and proof["retained_cny"] == retained
        assert archive.summary()["complete"] is False
        assert not any(row["kind"] == "provider-dispatch" for row in archive.events)
        aborted = [row for row in archive.events if row["kind"] == "provider-dispatch-aborted"]
        assert len(aborted) == 1 and aborted[0]["transport_entered"] is False
        assert aborted[0]["response_received"] is None and aborted[0]["body_complete"] is None
        # A failed final admission cannot mint a replacement logical ID/nonce.
        with logical_scope(scope), pytest.raises(PermissionError):
            transport.handle_request(req)
        assert journal.requests == 1
    assert not errors


def test_concurrent_admitted_requests_release_gate_locks_and_remain_in_flight_at_later_deny(tmp_path):
    entered, resume = threading.Barrier(3), threading.Event()
    sends = []
    with pipes(tmp_path, limit=2, timeout=5) as (channel, journal, broker, errors):
        archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))

        def inner(req):
            sends.append(req)
            entered.wait(timeout=3)
            assert resume.wait(3)
            return httpx.Response(200, content=b"offline response")

        transport = ControlledTransport(httpx.MockTransport(inner), channel, archive)

        def run(number):
            req = request(str(number))
            req.extensions["timeout"] = {name: 1 for name in ("read", "write", "connect", "pool")}
            with logical_scope(LogicalCall()):
                response = transport.handle_request(req)
                response.read()
                response.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, number) for number in range(2)]
            try:
                entered.wait(timeout=3)
                assert channel.state_lock.acquire(blocking=False)
                channel.state_lock.release()
                assert archive.lock.acquire(blocking=False)
                archive.lock.release()
                # Deny is processed while both admitted inner calls block;
                # admission is not a shared lock held across network IO.
                with pytest.raises(SendPermissionDenied):
                    channel.permission(request("denied"), "completion-" + "f" * 32)
                assert channel.sending_closed is True
            finally:
                resume.set()
            for future in futures:
                future.result(timeout=3)
        assert len(sends) == 2 and journal.requests == 2
        assert len([row for row in archive.events if row["kind"] == "provider-dispatch"]) == 2
        assert not any(row["kind"] == "provider-dispatch-aborted" for row in archive.events)
        assert archive.summary()["complete"] is True
        transport.close()
        channel.finish(archive.summary()["events_sha256"], native_exit=1)
    assert broker.complete is True and not errors


def test_existing_raw_stream_does_not_block_another_admission_or_allow_duplicate_logical_send(tmp_path):
    class Source(httpx.SyncByteStream):
        def __iter__(self):
            yield b"offline streamed body"

    sends = []
    with pipes(tmp_path, timeout=5) as (channel, journal, broker, errors):
        archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))

        def inner(req):
            sends.append(req)
            return httpx.Response(200, stream=Source())

        transport = ControlledTransport(httpx.MockTransport(inner), channel, archive)
        responses, scopes = [], []
        for number in range(2):
            scope = LogicalCall()
            scopes.append(scope)
            req = request(str(number))
            req.extensions["timeout"] = {name: 1 for name in ("read", "write", "connect", "pool")}
            with logical_scope(scope):
                responses.append(transport.handle_request(req))
            assert archive.active == number + 1
        with logical_scope(scopes[0]), pytest.raises(PermissionError):
            transport.handle_request(req)
        assert len(sends) == journal.requests == 2
        for response in responses:
            response.read()
            response.close()
        assert archive.summary()["complete"] is True
        transport.close()
        channel.finish(archive.summary()["events_sha256"], native_exit=1)
    assert broker.complete is True and not errors
