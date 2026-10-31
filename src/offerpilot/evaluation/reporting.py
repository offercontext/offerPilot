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
    return {"run_id": str(manifest.run_id), "plan_kind": manifest.plan_kind, "planned": len(rows), "counts": counts,
            "trials": rows, "exit_code": code, "release_eligible": False}


def write_report(path: Path, summary: dict[str, Any], manifest: RunManifest | None = None) -> None:
    lines = ["# Harness P0 结果", "", "release_eligible=false；仅覆盖声明的隔离业务表和区间。",
             "", "| 执行项 | 版本 | 结果 | 已知失败 | 缺失 |",
             "| --- | --- | --- | --- | --- |"]
    for r in summary["trials"]:
        lines.append(f"| {r['execution_item_id']} | {r['variant_id']} | {r['verdict']} | "
                     f"{', '.join(r['known_failures'])} | {', '.join(r['unknowns'])} |")
    if manifest:
        lines += ["", f"运行：{manifest.run_id}；计划：{manifest.plan_kind}；"
                  f"observer/bridge/grader={manifest.observer_version}/{manifest.bridge_version}/{manifest.grader_version}",
                  f"Python：{manifest.environment['python']}；SQLite：{manifest.environment['sqlite']}；OS：{manifest.environment['os']}",
                  f"评估器 hash：{manifest.evaluator_hash}", ""]
        for name, variant in manifest.variants.items():
            lines += [f"{name}: commit={variant['commit']}，diff={variant['diff_hash']}，lock={variant['lock_hash']}",
                      f"解释器：{variant['interpreter']}"]
        lines += ["", "验证入口：`oc eval validate`、`oc eval run`、`oc eval compare --policy harness-p0-v1`（参数见开发文档）。",
                  "私有证据位于 `private/<variant-execution_item-repeat>/evidence-index.json`；result.json 保存文件引用。",
                  "覆盖仅限声明表的普通 INSERT/UPDATE/DELETE；REPLACE、级联、DDL、其他表/库、物理篡改未覆盖。"]
        for r in summary["trials"]:
            if r.get("consumption", {}).get("coverage_gaps"):
                lines.append(f"{r['variant_id']}/{r['execution_item_id']} gaps: " + ", ".join(r["consumption"]["coverage_gaps"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
