"""KV1-03：无 Provider 的 Source / Evidence V1 验收 Profile。

ADR-0002：V1 profile 不依赖 AI Provider，只验证 Imported Source / Extraction / Evidence /
FTS / 搜索 / 回读 / 状态 / edge / bundle，不创建 Brief Job、不调用模型、不计算 Brief pass rate。
Brief 验收成为 ``brief`` profile（V1.1 候选），不污染 V1 默认语义。

覆盖：
- V1 profile 无 Provider 通过全部 V1 硬门禁，不含 Brief 指标 / 门禁。
- V1 导入只消费 Extraction queue，全程不创建 Brief Job（所有 Source 保持 not_started）。
- V1 门禁独立工作：回读失败仍触发硬门禁（不靠 Brief 门禁）。
- CLI ``oc knowledge-acceptance --profile v1`` 成功退出 0；未知 profile / fixtures 缺失退出非 0。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from offerpilot.config import Config
from offerpilot.knowledge.acceptance import AcceptanceReport, run_acceptance

REPO_FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def _run_v1(tmp_path: Path, **overrides: Any) -> AcceptanceReport:
    """V1 profile：无 Provider（空 Config），关闭 Brief 主验收与故障场景。"""
    return run_acceptance(
        fixtures_dir=REPO_FIXTURES,
        data_dir=tmp_path,
        config=Config(),
        enable_brief=False,
        enable_brief_failure_scenarios=False,
        **overrides,
    )


def test_kv1_03_v1_profile_passes_without_provider_or_brief(tmp_path: Path) -> None:
    """V1 profile 无 Provider 通过全部 V1 门禁，不创建 Brief Job、不含 Brief 指标/门禁。"""
    report = _run_v1(tmp_path)
    assert report.passed
    # V1 检索 / 回读 / 幂等 / edge / bundle 硬门禁。
    assert report.metrics["lexical_recall_at_5"] == 1.0
    assert report.metrics["lexical_mrr"] >= 0.9
    assert report.metrics["evidence_readback_rate"] == 1.0
    assert report.metrics["rerun_consistency_rate"] == 1.0
    assert report.metrics["edge_fixture_pass_rate"] == 1.0
    assert report.metrics["bundle_fixture_pass_rate"] == 1.0
    assert report.metrics["source_count"] == 5.0
    # V1 报告不含 Brief 指标 / 门禁。
    assert "brief_pass_rate" not in report.metrics
    assert "brief_failure_scenario_rate" not in report.metrics
    assert "brief_pass_rate" not in report.to_safe_json()["gates"]
    assert not any("brief" in failure.gate for failure in report.failures)
    assert report.provider_summary["mode"] == "v1-no-provider"
    # V1 导入不自动触发 Brief：所有 Source 保持 not_started。
    for source in report.source_results:
        assert source.brief_status == "not_started"
    for sandbox in (tmp_path, tmp_path / "edge-sandbox", tmp_path / "bundle-sandbox"):
        engine = create_engine(f"sqlite:///{sandbox / 'data.db'}")
        with engine.connect() as connection:
            brief_jobs = connection.execute(
                text("SELECT COUNT(*) FROM knowledge_jobs WHERE kind = 'brief'")
            ).scalar_one()
        assert brief_jobs == 0


def test_kv1_03_v1_readback_failure_fails_gate_without_brief(tmp_path: Path) -> None:
    """V1 门禁独立工作：回读失败触发硬门禁，无需 Brief 门禁即可定位失败。"""
    report = _run_v1(tmp_path, inject_readback_failure=True)
    assert not report.passed
    assert any(failure.gate == "evidence_readback" for failure in report.failures)


def test_kv1_03_cli_v1_profile_exits_zero_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """oc knowledge-acceptance --profile v1 成功时退出码 0，报告 mode=v1-no-provider。"""
    from offerpilot.cli import app

    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    result = CliRunner().invoke(app, ["knowledge-acceptance", "--profile", "v1"])
    assert result.exit_code == 0, result.output[-1000:]
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["provider_summary"]["mode"] == "v1-no-provider"
    assert "brief_pass_rate" not in payload["metrics"]
    assert "brief_pass_rate" not in payload["gates"]


def test_kv1_03_cli_unknown_profile_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未知 profile 退出非 0（参数错误，不运行验收）。"""
    from offerpilot.cli import app

    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    result = CliRunner().invoke(app, ["knowledge-acceptance", "--profile", "bogus"])
    assert result.exit_code != 0


def test_kv1_03_cli_fixtures_missing_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fixtures 目录缺失退出非 0（fixture 失败场景，可定位）。"""
    from offerpilot.cli import app

    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    result = CliRunner().invoke(
        app,
        [
            "knowledge-acceptance",
            "--profile",
            "v1",
            "--fixtures-dir",
            str(tmp_path / "does-not-exist"),
        ],
    )
    assert result.exit_code != 0


def test_kv1_03_cli_v1_real_ai_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--profile v1 --real-ai 退出非 0：V1 不接受真实 Provider。"""
    from offerpilot.cli import app

    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    result = CliRunner().invoke(
        app, ["knowledge-acceptance", "--profile", "v1", "--real-ai"]
    )
    assert result.exit_code != 0


def test_kv1_03_inconsistent_brief_flags_rejected(tmp_path: Path) -> None:
    """enable_brief_failure_scenarios=True 必须 enable_brief=True，否则 ValueError。"""
    with pytest.raises(ValueError, match="enable_brief_failure_scenarios"):
        run_acceptance(
            fixtures_dir=REPO_FIXTURES,
            data_dir=tmp_path,
            config=Config(),
            enable_brief=False,
            enable_brief_failure_scenarios=True,
        )
