"""Exploratory synthetic retrieval comparison; not a release or model-quality gate."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from offerpilot.context_projector.contracts import canonical_json  # noqa: E402
from offerpilot.context_sources.contracts import ContributorPolicy  # noqa: E402
from offerpilot.context_sources.loader import _contributor  # noqa: E402
from offerpilot.context_sources.memory import select_memory  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def measure(selected, relevant):
    groups = [item["group"] for item in selected]
    unique = set(groups)
    relevant = set(relevant)
    return {
        "recall": len(unique & relevant) / len(relevant) if relevant else None,
        "selected_count": len(groups),
        "irrelevant_count": sum(group not in relevant for group in groups),
        "duplicate_count": len(groups) - len(unique),
        "missing": sorted(relevant - unique),
        "unwanted": sorted(unique - relevant),
        "selected": groups,
        "empty_correct": not groups if not relevant else None,
    }


def evaluate(fixture):
    assert len({item["key"] for item in fixture["memories"]}) == len(fixture["memories"])
    assert len({case["id"] for case in fixture["queries"]}) == len(fixture["queries"])
    groups = {item["key"]: item.get("group", item["key"]) for item in fixture["memories"]}
    results = []
    for seed in fixture["id_order_seeds"]:
        records = list(fixture["memories"])
        random.Random(seed).shuffle(records)
        pool = [{"id": f"{index:036d}", "version": 1, "content": item["content"],
                 "category": item["category"], "valid_until": None, "evidence_status": "user_confirmed"}
                for index, item in enumerate(records)]
        identities = {item["id"]: groups[record["key"]] for item, record in zip(pool, records, strict=True)}
        for case in fixture["queries"]:
            assert set(case["relevant"]) <= set(groups.values())
            for budget in fixture["budgets"]:
                feasible = set()
                for item in pool:
                    single, _ = _contributor(
                        "confirmed_memory", ContributorPolicy(enabled=True, max_units=budget), [item],
                        source_revision="confirmed_memory:retrieval-v1")
                    if single.messages:
                        feasible.add(identities[item["id"]])
                for strategy in ("id_order", "task_lexical"):
                    items = pool if strategy == "id_order" else select_memory(
                        pool, case["query"], confirmed_interview_context=case.get("readiness", False)).items
                    contributor, _ = _contributor(
                        "confirmed_memory", ContributorPolicy(enabled=True, max_units=budget), items,
                        source_revision="confirmed_memory:retrieval-v1" if strategy == "task_lexical" else None)
                    selected = []
                    if contributor.messages:
                        selected = json.loads(contributor.messages[0].content.split("\n", 1)[1])["items"]
                    used = sum(len(canonical_json(message.canonical_value())) + 1 for message in contributor.messages)
                    assert used <= budget
                    metrics = measure([{"group": identities[item["id"]]} for item in selected], case["relevant"])
                    feasible_relevant = set(case["relevant"]) & feasible
                    selected_groups = {identities[item["id"]] for item in selected}
                    metrics["individually_fitting_recall"] = (
                        len(selected_groups & feasible_relevant) / len(feasible_relevant) if feasible_relevant else None)
                    metrics["oversize_relevant"] = sorted(set(case["relevant"]) - feasible)
                    results.append({"case": case["id"], "family": case["family"], "seed": seed,
                                    "budget": budget, "strategy": strategy, "bytes": used, **metrics})
    return results


def summarize(rows):
    recall = [r["recall"] for r in rows if r["recall"] is not None]
    fitting = [r["individually_fitting_recall"] for r in rows if r["individually_fitting_recall"] is not None]
    empty = [r["empty_correct"] for r in rows if r["empty_correct"] is not None]
    selected = sum(r["selected_count"] for r in rows)
    return {"trials": len(rows), "macro_recall": sum(recall) / len(recall) if recall else None,
            "individually_fitting_macro_recall": sum(fitting) / len(fitting) if fitting else None,
            "irrelevant_fraction": sum(r["irrelevant_count"] for r in rows) / selected if selected else None,
            "duplicate_fraction": sum(r["duplicate_count"] for r in rows) / selected if selected else None,
            "empty_accuracy": sum(empty) / len(empty) if empty else None,
            "mean_bytes": sum(r["bytes"] for r in rows) / len(rows),
            "mean_budget_utilization": sum(r["bytes"] / r["budget"] for r in rows) / len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "evals/fixtures/memory-retrieval-exploratory-v1.json")
    args = parser.parse_args()
    raw = args.fixture.read_bytes()
    fixture = json.loads(raw)
    results = evaluate(fixture)
    summary = {strategy: summarize([r for r in results if r["strategy"] == strategy])
               for strategy in ("id_order", "task_lexical")}
    by_budget = {str(budget): {strategy: summarize([r for r in results if r["strategy"] == strategy and r["budget"] == budget])
                             for strategy in summary} for budget in fixture["budgets"]}
    by_family = {family: {strategy: summarize([r for r in results if r["strategy"] == strategy and r["family"] == family])
                         for strategy in summary} for family in sorted({r["family"] for r in results})}
    print(json.dumps({"fixture_sha256": sha256(raw).hexdigest(),
                      "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                      "strategy_source_sha256": sha256((ROOT / "src/offerpilot/context_sources/memory.py").read_bytes()).hexdigest(),
                      "scope": "eligible synthetic records, contributor budget only; exploratory, not held-out",
                      "summary": summary, "by_budget": by_budget, "by_family": by_family, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
