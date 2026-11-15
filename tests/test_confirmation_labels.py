"""Keep visible confirmation labels in sync with the live editable tool surface."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from offerpilot.ai.confirmation_receipt import edited_confirmation_receipt
from offerpilot.ai.tool_specs.catalog import build_model_tool_catalog
from offerpilot.ai.tool_specs.legacy import build_static_adapter_catalog


def _editable_fields():
    fields = {
        item.field
        for spec in build_model_tool_catalog().specs
        for item in spec.metadata.editable_fields
    }
    fields.update(
        item["field"]
        for adapter in build_static_adapter_catalog().ordered_adapters
        for item in adapter.editable_fields
    )
    return sorted(fields)


@pytest.fixture(scope="module")
def rendered_card_labels():
    """Exercise live backend fields through Vite, React and the production catalogs."""
    web = Path(__file__).resolve().parents[1] / "web"
    fields = _editable_fields()
    assert fields, "The live tool catalogs must expose editable fields"
    # Deliberately not an authored UI label: fallback must preserve unknown keys.
    fields.append("用户自定义字段 / custom_field")
    result = subprocess.run(
        ["node", "tests/contracts/confirmation-labels.mjs"],
        cwd=web,
        input=json.dumps(fields),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.startswith("CONFIRMATION_LABELS=")]
    assert len(lines) == 1, result.stdout + result.stderr
    return json.loads(lines[0].split("=", 1)[1])


def test_all_editable_fields_have_chinese_card_labels(rendered_card_labels):
    labels = rendered_card_labels["zh-CN"]
    for field in _editable_fields():
        assert re.search(r"[\u4e00-\u9fff]", labels[field]), (field, labels[field])


def test_all_editable_fields_have_english_card_labels(rendered_card_labels):
    for field in _editable_fields():
        label = rendered_card_labels["en-US"][field]
        assert label and label != field, (field, label)
        assert label != rendered_card_labels["zh-CN"][field], (field, label)
        assert not re.search(r"[\u4e00-\u9fff]", label), (field, label)


def test_unknown_card_field_labels_preserve_user_content(rendered_card_labels):
    field = "用户自定义字段 / custom_field"
    for locale in ("zh-CN", "en-US"):
        assert rendered_card_labels[locale][field] == field


def test_all_editable_fields_have_chinese_receipt_labels():
    for field in _editable_fields():
        receipt = edited_confirmation_receipt(
            result_json=json.dumps({field: "验收值"}), changed_fields=[field]
        )
        assert f"{field}=" not in receipt
        assert "=验收值" in receipt
        label = receipt.split("保存结果：", 1)[1].split("=", 1)[0]
        assert re.search(r"[\u4e00-\u9fff]", label), (field, label)
