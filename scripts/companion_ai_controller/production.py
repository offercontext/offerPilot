"""Unarmed production bridge. Only fixed public metadata may grant admission.

The CLI has no reader, transport, URL, key, or fake-mode arguments. A deployment
registration is intentionally absent in this development branch. Test injection
belongs to monkeypatched unit tests, never to this public execution interface.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import zipfile

from scripts.companion_ai_controller import admission, policy as p
from scripts.companion_ai_controller.read_only import H_RUN
from scripts.companion_ai_validation import broker, shadow_driver
from scripts.companion_ai_validation.run import clean_environment

ROOT = Path(__file__).resolve().parents[2]
REGISTRATION_VARIABLE = 'OFFERPILOT_COMPANION_AI_REGISTRATION'
KEY_NAME = 'OFFERPILOT_COMPANION_DEEPSEEK_20261021'
METADATA_TOKEN_NAME = 'OFFERPILOT_COMPANION_METADATA_TOKEN'
HANDOFF_LIMIT = 16384
DRIVER_FILES = ('summary.json', 'requests.jsonl', 'responses.jsonl', 'budget.jsonl')
EVIDENCE_FILES = DRIVER_FILES + ('admission.json', 'verification.json')


def load_registration() -> Any:
    raw = os.environ.get(REGISTRATION_VARIABLE, '')
    if not raw:
        return None
    p.demand(len(raw.encode()) <= HANDOFF_LIMIT, 'REGISTRATION_SIZE_INVALID')
    data = broker.decode(raw.encode())
    p.demand('controller_sha' not in data, 'SELF_PINNED_CONTROLLER_FORBIDDEN')
    return admission.Registration.from_mapping(
        data, controller_sha=os.environ.get('OFFERPILOT_COMPANION_AI_CONTROLLER_SHA', ''))


def runtime_context() -> Any:
    p.demand(os.environ.get('CONTROLLER_CANDIDATE_SHA') == p.SOURCE_SHA
             and os.environ.get('CONTROLLER_CI_RUN_ID') == str(H_RUN)
             and os.environ.get('CONTROLLER_MANIFEST_SHA256') == p.MANIFEST_SHA256,
             'FIXED_INPUTS_REQUIRED')
    names = {'repository_id': 'GITHUB_REPOSITORY_ID', 'ref': 'GITHUB_REF', 'sha': 'GITHUB_SHA',
             'workflow_sha': 'GITHUB_WORKFLOW_SHA', 'run_id': 'GITHUB_RUN_ID',
             'attempt': 'GITHUB_RUN_ATTEMPT', 'event': 'GITHUB_EVENT_NAME',
             'actor_id': 'GITHUB_ACTOR_ID'}
    values: dict[str, Any] = {key: os.environ.get(name, '') for key, name in names.items()}
    for name in ('repository_id', 'run_id', 'attempt', 'actor_id'):
        value = values[name]
        p.demand(isinstance(value, str) and value.isascii() and value.isdecimal(), 'RUN_CONTEXT_INVALID')
        values[name] = int(value)
    p.demand(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'XiaoZheBrother'
             and values['attempt'] == 1, 'TRIGGERING_ACTOR_INVALID')
    # The API must independently match this fixed owner ID; no invented GitHub
    # triggering-actor-ID environment variable is relied on.
    values['triggering_actor_id'] = p.OWNER_ID
    return admission.RunContext(**values)


def encode_handoff(record: Any) -> str:
    raw = p.canonical(asdict(record))
    p.demand(len(raw) <= HANDOFF_LIMIT, 'HANDOFF_SIZE_INVALID')
    return base64.b64encode(raw).decode('ascii')


def decode_handoff(encoded: str) -> Any:
    p.demand(isinstance(encoded, str) and 0 < len(encoded) <= HANDOFF_LIMIT * 2, 'HANDOFF_INVALID')
    try:
        raw = base64.b64decode(encoded, validate=True)
        p.demand(0 < len(raw) <= HANDOFF_LIMIT, 'HANDOFF_SIZE_INVALID')
        data = broker.decode(raw)
        return admission.ProductionAdmission.from_mapping(data)
    except (ValueError, TypeError, KeyError, broker.GateError):
        raise p.Blocked('HANDOFF_INVALID') from None


def _checkout(expected: str) -> None:
    env = {'PATH': os.defpath, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_NO_REPLACE_OBJECTS': '1'}
    def git(*args: str) -> bytes:
        return subprocess.run(['git', '-c', 'core.fsmonitor=false', *args], cwd=ROOT,
                              env=env, capture_output=True, check=True, timeout=20).stdout
    p.demand(git('rev-parse', 'HEAD').decode().strip() == expected
             and not git('status', '--porcelain', '--untracked-files=no'), 'CHECKOUT_CHANGED')


def _fresh(record: Any, now: float) -> None:
    p.demand(record.mode == 'production_verified' and record.phase == 'paid'
             and record.checked_at <= now <= record.expires_at <= record.checked_at + 120
             and now + p.WINDOW <= record.deadline == p.DEADLINE,
             'PAID_ADMISSION_EXPIRED')


def verify_current(phase: str) -> tuple[Any, Any, broker.RunWindow]:
    registration = load_registration()
    p.demand(registration is not None, 'PRODUCTION_UNCONFIGURED')
    context = runtime_context()
    window = broker.RunWindow(shadow_review=True)
    _checkout(registration.controller_sha)
    metadata_token = None
    if registration.reader_mode == 'github_actions_read':
        metadata_token = os.environ.get(METADATA_TOKEN_NAME, '')
        p.demand(bool(metadata_token), 'EXPLICIT_METADATA_TOKEN_REQUIRED')
    try:
        if phase == 'before':
            record = admission.verify_before(registration, context, now=window.effective_wall(),
                                             metadata_token=metadata_token)
        else:
            before = decode_handoff(os.environ.get('CONTROLLER_BEFORE', ''))
            record = admission.verify_paid(registration, context, before,
                                           now=window.effective_wall(), metadata_token=metadata_token)
            _fresh(record, window.effective_wall())
    finally:
        # The short-lived metadata capability is never inherited by the provider
        # transport. No implicit GITHUB_TOKEN lookup or anonymous fallback exists.
        if METADATA_TOKEN_NAME in os.environ:
            del os.environ[METADATA_TOKEN_NAME]
        metadata_token = None
    _checkout(registration.controller_sha)
    return record, context, window


def _claim(run_id: int) -> None:
    folder = Path(os.environ.get('RUNNER_TEMP', ''))
    p.demand(folder.is_absolute() and folder.is_dir() and not folder.is_symlink(), 'RUNNER_TEMP_REQUIRED')
    path = folder / f'companion-controller-{run_id}.claimed'
    try:
        with path.open('xb') as stream:
            stream.write(b'One irreversible execution attempt; never remove for retry.\n')
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        raise p.Blocked('EXECUTION_ALREADY_CLAIMED') from None


def _read_key() -> str:
    return os.environ.pop(KEY_NAME, '')


def _publish_archive(folder: Path, record: Any) -> None:
    p.demand(type(record) is admission.ProductionAdmission and record.phase == 'paid'
             and record.mode == 'production_verified', 'EVIDENCE_ADMISSION_INVALID')
    contents = {}
    for name in DRIVER_FILES:
        path = folder / name
        p.demand(path.is_file() and not path.is_symlink(), 'EVIDENCE_INCOMPLETE')
        raw = path.read_bytes()
        p.demand(len(raw) <= 2_000_000, 'EVIDENCE_OVERSIZE')
        contents[name] = raw
    contents['admission.json'] = p.canonical({
        'schema': 1, 'scope': 'production_metadata_not_semantic_correctness',
        'before_binding_digest': record.binding_digest, 'paid': asdict(record),
        'owner_approval_verified': True, 'hidden_rule_settings_machine_verified': False,
    })
    contents['verification.json'] = p.canonical({
        'schema': 1, 'run_id': record.run_id, 'controller_sha': record.controller_sha,
        'source_sha': record.source_sha, 'batch_key': record.batch_key,
        'scope': 'bound_raw_evidence_not_provider_invoice_or_semantic_grade',
        'files_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()},
    })
    manifest = {'schema': 1, 'mode': 'production_evidence',
                'files_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}}
    pending = folder / 'evidence.pending.zip'
    with zipfile.ZipFile(pending, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
        archive.writestr('manifest.json', p.canonical(manifest))
    with zipfile.ZipFile(pending) as archive:
        p.demand(set(archive.namelist()) == set(EVIDENCE_FILES) | {'manifest.json'}, 'EVIDENCE_ARCHIVE_INVALID')
        for name, raw in contents.items():
            p.demand(archive.read(name) == raw, 'EVIDENCE_ARCHIVE_INVALID')
    with pending.open('rb') as stream:
        os.fsync(stream.fileno())
    os.replace(pending, folder / 'evidence.zip')
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def send_current(evidence_dir: Path) -> bool:
    record, context, window = verify_current('paid')
    p.demand(not evidence_dir.exists() and not evidence_dir.is_symlink(), 'EVIDENCE_ALREADY_EXISTS')
    _claim(context.run_id)
    # No I/O between the final anchored freshness check and the secret read.
    _fresh(record, window.effective_wall())
    key = _read_key()
    p.demand(isinstance(key, str) and 0 < len(key) <= 4096 and not any(c.isspace() for c in key),
             'PROVIDER_CREDENTIAL_MISSING')
    clean_environment()
    transport = broker.HTTPSUpstream(key, window=window)
    forbidden = (key.encode(), base64.b64encode(key.encode()), key.encode().hex().encode())
    quarantined = False

    def checked_send(raw: bytes) -> tuple[int, bytes]:
        nonlocal quarantined
        status, response = transport(raw)
        if any(value and value in response for value in forbidden):
            quarantined = True
            raise broker.GateError('credential_echo_quarantined')
        return status, response

    key = ''
    report = shadow_driver.execute(checked_send, p.SOURCE_SHA, evidence_dir, window=window)
    # Any suspected credential echo stops publication; keep the durable unknown
    # liability but never place credential-bearing bytes in an upload path.
    if quarantined:
        raise p.Blocked('CREDENTIAL_ECHO_QUARANTINED')
    _publish_archive(evidence_dir, record)
    return report['matrix']['passed'] is True


def probe_current() -> dict[str, Any]:
    """Independent no-credential platform probe, never a production admission."""
    registration = load_registration()
    p.demand(registration is not None, 'PRODUCTION_UNCONFIGURED')
    context = runtime_context()
    _checkout(registration.controller_sha)
    return admission.control_probe(registration, context)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('before', 'paid', 'send', 'control-probe'))
    parser.add_argument('--evidence-dir', type=Path)
    args = parser.parse_args()
    try:
        if args.phase == 'control-probe':
            print(json.dumps(probe_current(), sort_keys=True))
            return 0
        if args.phase == 'send':
            p.demand(args.evidence_dir is not None, 'EVIDENCE_PATH_REQUIRED')
            return 0 if send_current(args.evidence_dir) else 1
        record, _, _ = verify_current(args.phase)
        if args.phase == 'before':
            output = os.environ.get('GITHUB_OUTPUT', '')
            p.demand(bool(output), 'JOB_OUTPUT_REQUIRED')
            with open(output, 'a') as stream:
                stream.write('before=' + encode_handoff(record) + '\n')
                stream.write(f'summary_url=https://github.com/offercontext/offerPilot/actions/runs/{record.run_id}\n')
            summary_path = os.environ.get('GITHUB_STEP_SUMMARY', '')
            p.demand(bool(summary_path), 'JOB_SUMMARY_REQUIRED')
            with open(summary_path, 'a') as summary:
                summary.write(
                    '# Fixed six-review approval request\n'
                    f'Controller: `{record.controller_sha}`\n\n'
                    f'Source: `{p.SOURCE_SHA}`; tree: `{p.SOURCE_TREE}`\n\n'
                    'Diff: https://github.com/offercontext/offerPilot/compare/'
                    f'61c748e772d389a57016b526bd63294ddda19ffe...{p.SOURCE_SHA}\n\n'
                    f'Source CI: https://github.com/offercontext/offerPilot/actions/runs/{H_RUN}\n\n'
                    f'Manifest: `{p.MANIFEST_SHA256}`; binding: `{record.binding_digest}`\n\n'
                    f'Payload hashes: `{list(p.REQUEST_HASHES)}`\n\n'
                    'Exactly six fixed reviews; deepseek-flash; max output 768; no tools or retries.\n\n'
                    'Prior 48 calls / 0.411934 CNY. Campaign cap 20 CNY; 3 CNY reservation per call.\n\n'
                    'Deadline 2026-10-28 00:00 UTC / 08:00 Beijing; full 20 minutes required.\n\n'
                    'Owner confirms hidden lock/bypass settings remain unchanged.\n')
        print(json.dumps({'mode': record.mode, 'phase': record.phase, 'binding_digest': record.binding_digest,
                          'source_sha': record.source_sha, 'controller_sha': record.controller_sha,
                          'batch_key': record.batch_key, 'deadline': record.deadline}))
        return 0
    except Exception:
        print(json.dumps({'passed': False, 'error_code': 'PRODUCTION_BLOCKED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
