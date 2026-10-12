from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from offerpilot.evaluation.contracts import RunManifest, RuleResult, TrialResult


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def aggregate(manifest: RunManifest, results: list[TrialResult]) -> dict[str, Any]:
    by_key = {r.identity(): r for r in results}
    planned = {p.identity() for p in manifest.planned_trials}
    if len(by_key) != len(results) or set(by_key) - planned:
        raise ValueError("duplicate or unplanned result")
    rows = []
    for p in manifest.planned_trials:
        result = by_key.get(p.identity())
        if result is not None:
            if (result.case_id, result.case_version) != (p.case_id, p.case_version):
                raise ValueError("result case binding differs from plan")
            rules = {r.rule_id: r for r in result.rules}
            invalid = [name for name in p.required_rules if name in rules and (
                not rules[name].required or rules[name].status == "not_applicable")]
            if invalid:
                raise ValueError("result downgraded planned required rules")
            missing = [name for name in p.required_rules if name not in rules]
            if missing:
                added = [RuleResult(rule_id=name, status="unknown", reason_code="missing_rule")
                         for name in missing]
                result = result.model_copy(update={
                    "rules": result.rules + added, "unknowns": result.unknowns + missing,
                    "verdict": "fail" if result.known_failures else "incomplete",
                })
            row = result.model_dump(mode="json")
        else:
            row = {**p.model_dump(mode="json"), "execution_status": "not_run",
                   "verdict": "incomplete", "known_failures": [],
                   "unknowns": list(p.required_rules)}
        row["coverage_kind"] = p.coverage_kind
        rows.append(row)
    counts = {v: sum(r["verdict"] == v for r in rows) for v in ("pass", "fail", "incomplete")}
    errors = any(r["execution_status"] in {"setup_error", "unsupported", "eval_error"} for r in rows)
    code = 1 if counts["fail"] else 2 if errors else 3 if counts["incomplete"] else 0
    return {"run_id": str(manifest.run_id), "planned": len(rows), "counts": counts,
            "trials": rows, "exit_code": code, "release_eligible": False}


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = ["# Harness P0 结果", "", "release_eligible=false；仅覆盖声明的隔离业务表和区间。",
             "", "| 执行项 | 版本 | 结果 | 已知失败 | 缺失 |",
             "| --- | --- | --- | --- | --- |"]
    for r in summary["trials"]:
        lines.append(f"| {r['execution_item_id']} | {r['variant_id']} | {r['verdict']} | "
                     f"{', '.join(r['known_failures'])} | {', '.join(r['unknowns'])} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
