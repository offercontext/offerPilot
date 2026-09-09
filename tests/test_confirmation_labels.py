"""Keep visible confirmation labels in sync with the live editable tool surface."""

import json
import re
from pathlib import Path

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


def test_all_editable_fields_have_chinese_card_labels():
    source = (Path(__file__).resolve().parents[1] / "web/src/components/ChatPanel/ProposalCard.tsx").read_text(
        encoding="utf-8"
    )
    block = source.split("const FIELD_LABELS: Record<string, string> = {", 1)[1].split("};", 1)[0]
    labels = dict(re.findall(r"(\w+): '([^']+)'", block))
    labels.update(re.findall(r"FIELD_LABELS\.(\w+) = '([^']+)'", source))
    missing = [field for field in _editable_fields() if not re.search(r"[\u4e00-\u9fff]", labels.get(field, ""))]
    assert missing == []


def test_all_editable_fields_have_chinese_receipt_labels():
    for field in _editable_fields():
        receipt = edited_confirmation_receipt(
            result_json=json.dumps({field: "验收值"}), changed_fields=[field]
        )
        assert f"{field}=" not in receipt
        assert "=验收值" in receipt
        label = receipt.split("保存结果：", 1)[1].split("=", 1)[0]
        assert re.search(r"[\u4e00-\u9fff]", label), (field, label)
