"""Genuine offline workflow path: synthetic platform contract, fixed fake broker."""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
import json
import os
from pathlib import Path
import zipfile

from scripts.companion_ai_controller import policy, verify
from scripts.companion_ai_controller.execute import (
    EVIDENCE_KIND, FILES, RehearsalError, evidence_manifest, execute_rehearsal,
)

RECEIPTS = ("admission.json", "verification.json")


def run(output_dir: Path) -> bool:
    if output_dir.exists() or output_dir.is_symlink():
        raise RehearsalError("REHEARSAL_OUTPUT_EXISTS")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    status_file = output_dir.with_name(output_dir.name + "-status.json")
    with status_file.open("x") as stream:
        json.dump({"evidence_kind": EVIDENCE_KIND, "state": "started_not_complete",
                   "provider_calls": 0, "production_protection_verified": False}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    bundle = verify.make_rehearsal_bundle()
    now = policy.DEADLINE - 3600
    registration = policy.fixture_registration()
    before = verify.verify_bundle(bundle, now=now, registration=registration)
    paid = verify.verify_bundle(verify.synthetic_approve(bundle), now=now, phase="paid", registration=registration,
                                approved=before)
    if paid.live_allowed:
        raise RehearsalError("REHEARSAL_LIVE_ADMISSION_FORBIDDEN")
    report = execute_rehearsal(output_dir)
    (output_dir / "admission.json").write_text(json.dumps({
        "evidence_kind": EVIDENCE_KIND, "synthetic_platform_metadata": True,
        "production_protection_verified": False, "before": asdict(before), "paid": asdict(paid),
    }, ensure_ascii=False, indent=2) + "\n")
    expected_hashes = evidence_manifest(output_dir)
    (output_dir / "verification.json").write_text(json.dumps({
        "evidence_kind": EVIDENCE_KIND, "provider_calls": 0,
        "files_sha256": expected_hashes, "live_allowed": False,
        "production_protection_verified": False,
    }, ensure_ascii=False, indent=2) + "\n")
    # Actual runner -> explicit workflow upload files -> ZIP has no broad globs.
    temporary = output_dir / "rehearsal.pending.zip"
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as z:
        for name in FILES + RECEIPTS:
            z.write(output_dir / name, name)
    with zipfile.ZipFile(temporary) as z:
        if set(z.namelist()) != set(FILES + RECEIPTS):
            raise RehearsalError("REHEARSAL_ARCHIVE_MEMBERS")
        for name in FILES + RECEIPTS:
            if (z.read(name) != (output_dir / name).read_bytes()
                    or (name in expected_hashes
                        and hashlib.sha256(z.read(name)).hexdigest() != expected_hashes[name])):
                raise RehearsalError("REHEARSAL_ARCHIVE_CHANGED")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, output_dir / "rehearsal.zip")
    complete = status_file.with_suffix(".pending")
    with complete.open("x") as stream:
        json.dump({"evidence_kind": EVIDENCE_KIND, "state": "completed_fake_only",
                   "fake_passed": report["matrix"]["passed"] is True, "provider_calls": 0,
                   "production_protection_verified": False}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(complete, status_file)
    for directory in (output_dir, output_dir.parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return report["matrix"]["passed"] is True


def main() -> int:
    parser = argparse.ArgumentParser(description="No-secret controller rehearsal; no live mode")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        ok = run(args.output_dir)
    except Exception:
        # Never interpolate input, an environment variable, upstream data or paths.
        print("CONTROLLER_REHEARSAL_BLOCKED")
        return 1
    print("CONTROLLER_REHEARSAL_PASSED" if ok else "CONTROLLER_REHEARSAL_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
