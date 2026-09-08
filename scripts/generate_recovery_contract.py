"""Generate Python and TypeScript recovery-policy modules from the JSON contract.

Usage:
    uv run python scripts/generate_recovery_contract.py           # write generated files
    uv run python scripts/generate_recovery_contract.py --check   # fail if out of sync

The JSON contract is the single source of truth. Generated files are committed
so runtime code never reads the JSON; re-running the generator must produce a
zero-byte diff (asserted by tests/test_recovery_policy.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "contracts" / "recovery-policy.v1.json"
PYTHON_OUTPUT = REPO_ROOT / "src" / "offerpilot" / "reliability" / "recovery_policy_generated.py"
TS_OUTPUT = REPO_ROOT / "web" / "src" / "lib" / "recoveryPolicy" / "generatedRecoveryPolicy.ts"

ERROR_FIELDS = (
    "error_code",
    "http_status",
    "disposition",
    "attempt_retention",
    "input_frozen",
    "preserve_idempotency_key",
    "provider_retry_allowed",
    "user_action",
)
POLICY_FIELDS = (
    "disposition",
    "attempt_retention",
    "input_frozen",
    "preserve_idempotency_key",
    "provider_retry_allowed",
    "user_action",
)


def _fail(message: str) -> None:
    print(f"recovery contract error: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_and_validate(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read {path}: {exc}")
    for field in ("contract_name", "version", "domain", "dispositions", "attempt_retentions", "user_actions", "unknown_code_policy", "network_transport_policy", "errors"):
        if field not in contract:
            _fail(f"missing top-level field {field!r}")
    errors = contract["errors"]
    if not isinstance(errors, list) or not errors:
        _fail("errors must be a non-empty list")
    seen: set[str] = set()
    for entry in errors:
        if not isinstance(entry, dict):
            _fail("each error entry must be an object")
        missing = [field for field in ERROR_FIELDS if field not in entry]
        if missing:
            _fail(f"error entry missing fields {missing}: {entry}")
        extra = [field for field in entry if field not in ERROR_FIELDS]
        if extra:
            _fail(f"error entry has unexpected fields {extra}: {entry.get('error_code')}")
        code = entry["error_code"]
        if not isinstance(code, str) or not code:
            _fail("error_code must be a non-empty string")
        if code in seen:
            _fail(f"duplicate error_code {code!r}")
        seen.add(code)
        status = entry["http_status"]
        if not isinstance(status, int) or isinstance(status, bool) or not 400 <= status <= 599:
            _fail(f"{code}: http_status must be an int in 400..599")
        if entry["disposition"] not in contract["dispositions"]:
            _fail(f"{code}: unknown disposition {entry['disposition']!r}")
        if entry["attempt_retention"] not in contract["attempt_retentions"]:
            _fail(f"{code}: unknown attempt_retention {entry['attempt_retention']!r}")
        if entry["user_action"] not in contract["user_actions"]:
            _fail(f"{code}: unknown user_action {entry['user_action']!r}")
        for field in ("input_frozen", "preserve_idempotency_key", "provider_retry_allowed"):
            if not isinstance(entry[field], bool):
                _fail(f"{code}: {field} must be a boolean")
    for policy_name in ("unknown_code_policy", "network_transport_policy"):
        policy = contract[policy_name]
        if not isinstance(policy, dict):
            _fail(f"{policy_name} must be an object")
        missing = [field for field in POLICY_FIELDS if field not in policy]
        if missing:
            _fail(f"{policy_name} missing fields {missing}")
        if policy["disposition"] not in contract["dispositions"]:
            _fail(f"{policy_name}: unknown disposition")
        if policy["attempt_retention"] not in contract["attempt_retentions"]:
            _fail(f"{policy_name}: unknown attempt_retention")
        if policy["user_action"] not in contract["user_actions"]:
            _fail(f"{policy_name}: unknown user_action")
        for field in ("input_frozen", "preserve_idempotency_key", "provider_retry_allowed"):
            if not isinstance(policy[field], bool):
                _fail(f"{policy_name}: {field} must be a boolean")
    return contract


def _policy_block(indent: str, values: dict[str, Any]) -> str:
    lines = [f"{indent}disposition={values['disposition']!r},"]
    for field in ("attempt_retention", "user_action"):
        lines.append(f"{indent}{field}={values[field]!r},")
    for field in ("input_frozen", "preserve_idempotency_key", "provider_retry_allowed"):
        lines.append(f"{indent}{field}={values[field]!r},")
    return "\n".join(lines)


def render_python(contract: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Generated by scripts/generate_recovery_contract.py from")
    lines.append("# contracts/recovery-policy.v1.json -- do not edit by hand.")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from typing import Final")
    lines.append("")
    lines.append(f"CONTRACT_NAME: Final[str] = {contract['contract_name']!r}")
    lines.append(f"CONTRACT_VERSION: Final[int] = {contract['version']}")
    lines.append(f"DOMAIN: Final[str] = {contract['domain']!r}")
    lines.append(f"DISPOSITIONS: Final[tuple[str, ...]] = {tuple(contract['dispositions'])!r}")
    lines.append(f"ATTEMPT_RETENTIONS: Final[tuple[str, ...]] = {tuple(contract['attempt_retentions'])!r}")
    lines.append("UNKNOWN_CODE_DISPOSITION: Final[str] = "
                 f"{contract['unknown_code_policy']['disposition']!r}")
    lines.append("UNKNOWN_CODE_PROVIDER_RETRY_ALLOWED: Final[bool] = "
                 f"{contract['unknown_code_policy']['provider_retry_allowed']!r}")
    lines.append("NETWORK_TRANSPORT_DISPOSITION: Final[str] = "
                 f"{contract['network_transport_policy']['disposition']!r}")
    lines.append("")
    lines.append("class RecoveryPolicyEntry:")
    lines.append("    \"\"\"One error_code's frozen recovery contract.\"\"\"")
    lines.append("")
    lines.append("    __slots__ = (")
    lines.append("        'error_code', 'http_status', 'disposition', 'attempt_retention',")
    lines.append("        'input_frozen', 'preserve_idempotency_key', 'provider_retry_allowed',")
    lines.append("        'user_action',")
    lines.append("    )")
    lines.append("")
    lines.append("    def __init__(self, error_code: str, http_status: int, disposition: str,")
    lines.append("                 attempt_retention: str, input_frozen: bool,")
    lines.append("                 preserve_idempotency_key: bool, provider_retry_allowed: bool,")
    lines.append("                 user_action: str) -> None:")
    for field in ERROR_FIELDS:
        lines.append(f"        self.{field} = {field}")
    lines.append("")
    lines.append("    def __repr__(self) -> str:")
    lines.append("        return (")
    lines.append("            f'RecoveryPolicyEntry({self.error_code}, disposition={self.disposition})'")
    lines.append("        )")
    lines.append("")
    lines.append("    def __eq__(self, other: object) -> bool:")
    lines.append("        if not isinstance(other, RecoveryPolicyEntry):")
    lines.append("            return NotImplemented")
    lines.append("        return self.error_code == other.error_code")
    lines.append("")
    lines.append("    def __hash__(self) -> int:")
    lines.append("        return hash(self.error_code)")
    lines.append("")
    lines.append("RECOVERY_POLICIES: Final[dict[str, RecoveryPolicyEntry]] = {")
    for entry in sorted(contract["errors"], key=lambda item: item["error_code"]):
        lines.append(f"    {entry['error_code']!r}: RecoveryPolicyEntry(")
        body = _policy_block("        ", entry)
        body = f"        error_code={entry['error_code']!r},\n        http_status={entry['http_status']},\n" + body
        lines.append(body)
        lines.append("    ),")
    lines.append("}")
    lines.append("")
    lines.append("UNKNOWN_CODE_POLICY: Final[dict[str, object]] = {")
    for field in POLICY_FIELDS:
        lines.append(f"    {field!r}: {contract['unknown_code_policy'][field]!r},")
    lines.append("}")
    lines.append("")
    lines.append("NETWORK_TRANSPORT_POLICY: Final[dict[str, object]] = {")
    for field in POLICY_FIELDS:
        lines.append(f"    {field!r}: {contract['network_transport_policy'][field]!r},")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def render_typescript(contract: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("// Generated by scripts/generate_recovery_contract.py from")
    lines.append("// contracts/recovery-policy.v1.json -- do not edit by hand.")
    lines.append("")
    lines.append("export type RecoveryDisposition =")
    for disposition in contract["dispositions"]:
        lines.append(f"  | '{disposition}'")
    lines.append(";")
    lines.append("")
    lines.append("export type RecoveryUserAction =")
    for action in contract["user_actions"]:
        lines.append(f"  | '{action}'")
    lines.append(";")
    lines.append("")
    lines.append("export type RecoveryAttemptRetention =")
    for retention in contract["attempt_retentions"]:
        lines.append(f"  | '{retention}'")
    lines.append(";")
    lines.append("")
    lines.append("export interface RecoveryPolicyEntry {")
    lines.append("  error_code: string;")
    lines.append("  http_status: number;")
    lines.append("  disposition: RecoveryDisposition;")
    lines.append("  attempt_retention: RecoveryAttemptRetention;")
    for field in ("input_frozen", "preserve_idempotency_key", "provider_retry_allowed"):
        lines.append(f"  {field}: boolean;")
    lines.append("  user_action: RecoveryUserAction;")
    lines.append("}")
    lines.append("")
    lines.append("export interface FallbackRecoveryPolicy {")
    lines.append("  policy_name: string;")
    lines.append("  disposition: RecoveryDisposition;")
    lines.append("  attempt_retention: RecoveryAttemptRetention;")
    for field in ("input_frozen", "preserve_idempotency_key", "provider_retry_allowed"):
        lines.append(f"  {field}: boolean;")
    lines.append("  user_action: RecoveryUserAction;")
    lines.append("}")
    lines.append("")
    lines.append(f"export const CONTRACT_VERSION = {contract['version']};")
    lines.append(f"export const DOMAIN = '{contract['domain']}';")
    lines.append("")
    lines.append("export const RECOVERY_POLICIES: Readonly<Record<string, RecoveryPolicyEntry>> = {")
    for entry in sorted(contract["errors"], key=lambda item: item["error_code"]):
        lines.append(f"  {entry['error_code']}: {{")
        lines.append(f"    error_code: '{entry['error_code']}',")
        lines.append(f"    http_status: {entry['http_status']},")
        lines.append(f"    disposition: '{entry['disposition']}',")
        lines.append(f"    attempt_retention: '{entry['attempt_retention']}',")
        lines.append(f"    input_frozen: {str(entry['input_frozen']).lower()},")
        lines.append(f"    preserve_idempotency_key: {str(entry['preserve_idempotency_key']).lower()},")
        lines.append(f"    provider_retry_allowed: {str(entry['provider_retry_allowed']).lower()},")
        lines.append(f"    user_action: '{entry['user_action']}',")
        lines.append("  },")
    lines.append("};")
    lines.append("")
    for key in ("unknown_code_policy", "network_transport_policy"):
        policy = contract[key]
        const_name = "UNKNOWN_CODE_POLICY" if key == "unknown_code_policy" else "NETWORK_TRANSPORT_POLICY"
        lines.append(f"export const {const_name}: FallbackRecoveryPolicy = {{")
        lines.append(f"  policy_name: '{policy['policy_name']}',")
        lines.append(f"  disposition: '{policy['disposition']}',")
        lines.append(f"  attempt_retention: '{policy['attempt_retention']}',")
        lines.append(f"  input_frozen: {str(policy['input_frozen']).lower()},")
        lines.append(f"  preserve_idempotency_key: {str(policy['preserve_idempotency_key']).lower()},")
        lines.append(f"  provider_retry_allowed: {str(policy['provider_retry_allowed']).lower()},")
        lines.append(f"  user_action: '{policy['user_action']}',")
        lines.append("};")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files are not up to date")
    args = parser.parse_args()
    contract = load_and_validate()
    outputs = {
        PYTHON_OUTPUT: render_python(contract),
        TS_OUTPUT: render_typescript(contract),
    }
    stale: list[str] = []
    for path, content in outputs.items():
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != content:
                stale.append(str(path.relative_to(REPO_ROOT)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    if args.check and stale:
        print(f"generated recovery contract out of date: {', '.join(stale)}", file=sys.stderr)
        print("run: uv run python scripts/generate_recovery_contract.py", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
