from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import stat
import zipfile

import pytest

from scripts.companion_ai_controller import policy as p
from scripts.companion_ai_controller import verify as v

NOW = p.DEADLINE - 3600


def check(bundle=None, **kwargs):
    return v.verify_bundle(v.make_rehearsal_bundle() if bundle is None else bundle,
                           now=kwargs.pop("now", NOW), registration=p.fixture_registration(), **kwargs)


def test_before_and_paid_remain_rehearsal():
    bundle = v.make_rehearsal_bundle()
    before = check(bundle)
    paid = check(v.synthetic_approve(bundle), now=NOW + 1, phase="paid", approved=before)
    assert paid.mode == "rehearsal" and paid.live_allowed is False
    assert paid.prior_calls == 48 and paid.settled_micro_cny == 411934
    assert paid.payload_hashes == p.REQUEST_HASHES
    assert paid.binding_digest == before.binding_digest
    with pytest.raises(Exception):
        paid.live_allowed = True


def test_production_is_unconfigured():
    assert p.PRODUCTION_REGISTRATION is None
    with pytest.raises(p.Blocked, match="PRODUCTION_UNCONFIGURED"):
        v.verify_bundle(v.make_rehearsal_bundle(), now=NOW)
    with pytest.raises(p.Blocked, match="LIVE_ADMISSION_DISABLED"):
        v.verify_bundle(v.make_rehearsal_bundle(), now=NOW,
                        registration=replace(p.fixture_registration(), rehearsal=False))


@pytest.mark.parametrize("field,value", [
    ("ref", "refs/heads/master"), ("ref", "refs/heads/feat/20261021-companion-life-v1"),
    ("ref", "refs/tags/ai-validation-control"), ("attempt", 2), ("attempt", True),
    ("event", "push"), ("workflow_sha", "b" * 40), ("checkout_sha", "b" * 40),
    ("branch_sha", "b" * 40), ("configured_controller_sha", ""), ("workflow_id", 123),
    ("repository_id", 123), ("concurrency", "per-batch-nonce"), ("cancel_in_progress", True),
])
def test_platform_identity_rejections(field, value):
    bundle = v.make_rehearsal_bundle()
    bundle["platform"][field] = value
    with pytest.raises(p.Blocked, match="PLATFORM_BINDING_INVALID"):
        check(bundle)


@pytest.mark.parametrize("now", [p.DEADLINE - 1199, p.DEADLINE, float("nan"), float("inf"), 0, True])
def test_deadline_full_window(now):
    with pytest.raises(p.Blocked, match="DEADLINE_OR_WINDOW_EXPIRED"):
        check(now=now)


@pytest.mark.parametrize("field,value", [("head_sha", "b" * 40), ("attempt", 2),
    ("artifact_id", 1), ("workflow_id", 2), ("conclusion", "skipped"), ("status", "in_progress")])
def test_ci_binding(field, value):
    bundle = v.make_rehearsal_bundle()
    bundle["ci"][field] = value
    with pytest.raises(p.Blocked):
        check(bundle)


@pytest.mark.parametrize("payload", [b'{}', b'{"tools":[]}',
    b'{"command":"touch /tmp/should-never-exist"}',
    b'{"url":"https://evil.invalid"}', b'{"model":"other"}', b'__import__("os").system("id")'])
def test_candidate_is_only_fixed_data(payload):
    bundle = v.make_rehearsal_bundle()
    bundle["requests"][0] = payload
    with pytest.raises(p.Blocked, match="PAYLOAD_CHANGED"):
        check(bundle)


def test_no_seventh_or_manifest_rewrite():
    bundle = v.make_rehearsal_bundle()
    bundle["requests"].append(bundle["requests"][0])
    with pytest.raises(p.Blocked, match="EXACT_SIX_REQUIRED"):
        check(bundle)
    bundle = v.make_rehearsal_bundle()
    bundle["manifest"] += b"\n"
    with pytest.raises(p.Blocked, match="MANIFEST_CHANGED"):
        check(bundle)


@pytest.mark.parametrize("payload", [b'{"a":1,"a":2}', b'{"a":NaN}', b'null' * 20000,
                                     b'{', b'\xff'])
def test_strict_json(payload):
    with pytest.raises(p.Blocked):
        v.strict_json(payload)


def test_digest_cannot_be_replaced_alongside_artifact():
    bundle = v.make_rehearsal_bundle()
    bundle["artifact"] = b"not zip"
    bundle["artifact_digest"] = hashlib.sha256(bundle["artifact"]).hexdigest()
    with pytest.raises(p.Blocked, match="ARTIFACT_DIGEST_MISMATCH"):
        check(bundle)


def test_toctou_and_owner():
    bundle = v.make_rehearsal_bundle()
    before = check(bundle)
    paid_bundle = v.synthetic_approve(bundle)
    with pytest.raises(p.Blocked, match="TOCTOU"):
        check(paid_bundle, phase="paid", approved=before, now=NOW-1)
    paid_bundle["review"]["reviewer_id"] = 999
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_INVALID"):
        check(paid_bundle, phase="paid", approved=before)
    with pytest.raises(p.Blocked):
        check(bundle, phase="paid", approved=before)
    bundle["review"] = {"state": "approved"}
    with pytest.raises(p.Blocked, match="PREAPPROVAL_FORBIDDEN"):
        check(bundle)


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "symlink", "traversal", "skip", "false_pass"])
def test_archive_parser_independently_rejects_bad_members(monkeypatch, mutation):
    stream = io.BytesIO()
    aggregate = v._aggregate()
    if mutation == "skip":
        aggregate["finished"][v.SYNTHETIC_NODES[0]]["call"] = "skipped"
    if mutation == "false_pass":
        aggregate["collected"] = []
        aggregate["finished"] = {}
    with zipfile.ZipFile(stream, "w") as archive:
        name = "../companion-ai-offline.json" if mutation == "traversal" else "companion-ai-offline.json"
        info = zipfile.ZipInfo(name)
        info.external_attr = ((stat.S_IFLNK if mutation == "symlink" else stat.S_IFREG) | 0o600) << 16
        archive.writestr(info, json.dumps(aggregate))
        if mutation in ("extra", "duplicate"):
            archive.writestr(name if mutation == "duplicate" else "requests.py", "pass")
    raw = stream.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    # Unit-test the parser after a trusted digest match, not an admission path.
    monkeypatch.setattr(v, "SYNTHETIC_ARTIFACT_SHA256", digest)
    with pytest.raises(p.Blocked):
        v.verify_artifact(raw, digest)


@pytest.mark.parametrize("value", [None, [], 42, {"mode": "synthetic_platform"}])
def test_malformed_outer_input_is_constant(value):
    with pytest.raises(p.Blocked):
        check(value if value is not None else 42)


def test_post_review_new_valid_history_is_still_toctou():
    bundle = v.make_rehearsal_bundle()
    before = check(bundle)
    paid = v.synthetic_approve(bundle)
    reg = p.fixture_registration()
    paid["history"]["dry_runs"].append({"id": 920001, "number": 1,
        "workflow_id": reg.dry_run_workflow_id, "attempt": 1, "kind": "dry_run",
        "status": "completed", "paid_started": False, "batch_key": None,
        "controller_sha": reg.controller_sha})
    with pytest.raises(p.Blocked, match="TOCTOU"):
        check(paid, phase="paid", approved=before)


def test_no_hidden_protection_claim_is_accepted():
    bundle = v.make_rehearsal_bundle()
    bundle["environment"]["full_branch_protection_machine_verified"] = True
    with pytest.raises(p.Blocked, match="ENVIRONMENT_BINDING_INVALID"):
        check(bundle)


def test_late_approval_cannot_extend_cutoff():
    bundle = v.make_rehearsal_bundle()
    before = check(bundle)
    with pytest.raises(p.Blocked, match="DEADLINE_OR_WINDOW_EXPIRED"):
        check(v.synthetic_approve(bundle), phase="paid", approved=before,
              now=p.DEADLINE-1199)
