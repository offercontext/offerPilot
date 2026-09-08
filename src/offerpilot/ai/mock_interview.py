from __future__ import annotations

import hashlib
import json
import unicodedata
from time import perf_counter
from typing import Any, cast

from offerpilot.ai.types import Assistant, Message


class MockInterviewContractError(ValueError):
    def __init__(self, category: str, message: str | None = None):
        self.category = category
        super().__init__(f"{category}: {message or category}")


SAFE_EMPTY_FEEDBACK = {
    "schema_version": "mock-interview-feedback-v1",
    "proposal_status": "safe_empty",
    "strengths": [],
    "practice_points": [],
    "follow_up_questions": [],
    "next_practice_steps": [],
}

_FIELDS = set(SAFE_EMPTY_FEEDBACK)
_ITEM_FIELDS = {"id", "text", "evidence_ids"}
_FEEDBACK_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "text", "evidence_ids"],
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 120},
        "text": {"type": "string", "minLength": 1, "maxLength": 1000},
        "evidence_ids": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "minLength": 1},
        },
    },
}
MOCK_INTERVIEW_FEEDBACK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "proposal_status",
        "strengths",
        "practice_points",
        "follow_up_questions",
        "next_practice_steps",
    ],
    "properties": {
        "schema_version": {"const": "mock-interview-feedback-v1"},
        "proposal_status": {"enum": ["normal", "safe_empty"]},
        "strengths": {"type": "array", "maxItems": 8, "items": _FEEDBACK_ITEM_SCHEMA},
        "practice_points": {"type": "array", "maxItems": 8, "items": _FEEDBACK_ITEM_SCHEMA},
        "follow_up_questions": {"type": "array", "maxItems": 8, "items": _FEEDBACK_ITEM_SCHEMA},
        "next_practice_steps": {"type": "array", "maxItems": 8, "items": _FEEDBACK_ITEM_SCHEMA},
    },
    "oneOf": [
        {
            "properties": {
                "proposal_status": {"const": "safe_empty"},
                "strengths": {"maxItems": 0},
                "practice_points": {"maxItems": 0},
                "follow_up_questions": {"maxItems": 0},
                "next_practice_steps": {"maxItems": 0},
            },
        },
        {"properties": {"proposal_status": {"const": "normal"}}},
    ],
}
MOCK_INTERVIEW_QUESTION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "evidence_ids"],
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": 1000},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}
MOCK_INTERVIEW_QUESTION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "mock_interview_question",
        "strict": True,
        "schema": MOCK_INTERVIEW_QUESTION_OUTPUT_SCHEMA,
    },
}
MOCK_INTERVIEW_FEEDBACK_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "mock_interview_feedback",
        "strict": True,
        "schema": MOCK_INTERVIEW_FEEDBACK_SCHEMA,
    },
}
_FIXED_QUESTIONS = {
    "clarify_answer": "您希望进一步澄清哪一部分？",
    "add_example": "您希望补充一个具体例子吗？",
    "choose_next_focus": "下一次练习时，您想先补充哪一步？",
}
_FORMAT_REPAIR_CATEGORIES = {
    "invalid_json",
    "duplicate_key",
    "root_not_object",
    "unexpected_field",
    "field_type",
    "item_shape",
    "evidence_ids_not_array",
    "evidence_id_not_string",
    "missing_turn_evidence",
    "blank_value",
}

_FEEDBACK_TURN_EVIDENCE_RULE = (
    ' For a normal result, every item in strengths, practice_points, and '
    'next_practice_steps must include at least one completed-turn evidence '
    'reference: an evidence_ids entry whose catalog entry has source="turn". '
    'JD and Resume references are supplementary only and cannot replace the '
    'required Turn reference. If no completed-turn evidence supports a normal '
    'item or suggestion, return the exact safe_empty object instead of guessing.'
)


def parse_mock_interview_json(raw: str) -> dict[str, Any]:
    if "```" in raw:
        raise MockInterviewContractError("invalid_json", "fenced JSON is not allowed")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except MockInterviewContractError:
        raise
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise MockInterviewContractError("invalid_json") from exc
    if not isinstance(parsed, dict):
        raise MockInterviewContractError("root_not_object")
    return parsed


def validate_feedback(
    proposal: dict[str, Any], evidence_catalog: list[dict[str, str]]
) -> dict[str, Any]:
    """Validate a provider-shaped feedback proposal and expand evidence IDs.

    The provider only selects ``evidence_ids``; this function expands them into
    the frozen ``source/path/excerpt`` references that are persisted and exposed
    publicly. Unknown, duplicate, or cross-directory IDs are rejected strictly.
    """
    if set(proposal) != _FIELDS:
        raise MockInterviewContractError("unexpected_field")
    if proposal["schema_version"] != "mock-interview-feedback-v1":
        raise MockInterviewContractError("wrong_schema_version")
    status = proposal["proposal_status"]
    if status == "safe_empty":
        if proposal != SAFE_EMPTY_FEEDBACK:
            raise MockInterviewContractError("safe_empty_shape")
        return proposal
    if status != "normal":
        raise MockInterviewContractError("invalid_status")

    persisted: dict[str, Any] = {**proposal}
    seen_ids: set[str] = set()
    for field in ("strengths", "practice_points", "follow_up_questions", "next_practice_steps"):
        items = proposal[field]
        if not isinstance(items, list):
            raise MockInterviewContractError("field_type")
        if len(items) > 8:
            raise MockInterviewContractError("limit_exceeded")
        persisted_items: list[dict[str, Any]] = []
        for item in items:
            persisted_items.append(_validate_item(item, seen_ids, evidence_catalog, field))
        persisted[field] = persisted_items
    return persisted


def _validate_item(
    item: Any,
    seen_ids: set[str],
    evidence_catalog: list[dict[str, str]],
    field: str,
) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != _ITEM_FIELDS:
        raise MockInterviewContractError("item_shape")
    item_id = item["id"]
    text = item["text"]
    if not isinstance(item_id, str) or not item_id.strip() or item_id in seen_ids:
        raise MockInterviewContractError("duplicate_or_blank_id")
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise MockInterviewContractError("blank_value")
    seen_ids.add(item_id)
    evidence_ids = item["evidence_ids"]
    if not isinstance(evidence_ids, list):
        raise MockInterviewContractError("evidence_ids_not_array")
    if any(not isinstance(evidence_id, str) for evidence_id in evidence_ids):
        raise MockInterviewContractError("evidence_id_not_string")
    if field == "follow_up_questions" and not evidence_ids:
        if _FIXED_QUESTIONS.get(item_id) == text:
            return {"id": item_id, "text": text, "evidence_refs": []}
        if text in _FIXED_QUESTIONS.values():
            raise MockInterviewContractError("fixed_question")
        raise MockInterviewContractError("missing_evidence_ref")
    refs = _expand_evidence_ids(evidence_ids, evidence_catalog)
    if field in {"strengths", "practice_points", "next_practice_steps"}:
        if not any(ref["source"] == "turn" for ref in refs):
            raise MockInterviewContractError("missing_turn_evidence")
    return {"id": item_id, "text": text, "evidence_refs": refs}


def _escape_json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _append_resume_string_leaves(
    value: Any, path: str, catalog: list[dict[str, str]]
) -> None:
    if isinstance(value, dict):
        for key in sorted(value):
            if isinstance(key, str):
                _append_resume_string_leaves(
                    value[key], f"{path}/{_escape_json_pointer_token(key)}", catalog
                )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _append_resume_string_leaves(item, f"{path}/{index}", catalog)
        return
    if isinstance(value, str) and value:
        catalog.append({"source": "resume", "path": path, "value": value})


def build_mock_interview_evidence_catalog(
    snapshot: dict[str, Any], turns: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Build the stable, provider-facing catalog for the frozen interview input."""
    catalog: list[dict[str, str]] = []
    jd = snapshot.get("jd")
    jd_text = jd.get("text") if isinstance(jd, dict) else None
    if isinstance(jd_text, str) and jd_text:
        catalog.append({"source": "jd", "path": "/jd/text", "value": jd_text})

    resume = snapshot.get("resume")
    resume_content = resume.get("content_json") if isinstance(resume, dict) else None
    _append_resume_string_leaves(resume_content, "/resume/content_json", catalog)

    completed_turns: list[tuple[int, str]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        answer = turn.get("answer")
        turn_no = turn.get("turn_no")
        if not isinstance(answer, str) or not answer.strip():
            continue
        if isinstance(turn_no, bool) or not isinstance(turn_no, (int, str)):
            continue
        try:
            normalized_turn_no = int(turn_no)
        except ValueError:
            continue
        if normalized_turn_no < 1:
            continue
        completed_turns.append((normalized_turn_no, answer))
    for turn_no, answer in sorted(completed_turns, key=lambda item: item[0]):
        catalog.append(
            {
                "source": "turn",
                "path": f"/turns/{turn_no:03d}/answer",
                "value": answer,
            }
        )
    return catalog


def _provider_evidence_catalog(
    snapshot: dict[str, Any], turns: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Expose opaque, deterministic references so the model never copies citations.

    The persisted/public contract remains ``source + path + excerpt``.  The model
    only selects an ID from this request-scoped catalog; the server expands that
    ID back to an exact frozen excerpt after the response is parsed.
    """
    return [
        {"id": f"ev_{index:03d}", **entry}
        for index, entry in enumerate(
            build_mock_interview_evidence_catalog(snapshot, turns), start=1
        )
    ]


def _normalize_question_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _expand_evidence_ids(
    evidence_ids: Any, catalog: list[dict[str, str]]
) -> list[dict[str, str]]:
    if not isinstance(evidence_ids, list):
        raise MockInterviewContractError("evidence_ids_not_array")
    if not evidence_ids:
        raise MockInterviewContractError("missing_evidence_ref")
    if len(evidence_ids) > 4:
        raise MockInterviewContractError("limit_exceeded")
    if any(not isinstance(evidence_id, str) for evidence_id in evidence_ids):
        raise MockInterviewContractError("evidence_id_not_string")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise MockInterviewContractError("duplicate_evidence_ref")
    by_id = {entry["id"]: entry for entry in catalog}
    refs: list[dict[str, str]] = []
    for evidence_id in evidence_ids:
        entry = by_id.get(evidence_id)
        if entry is None:
            raise MockInterviewContractError("unknown_evidence_ref")
        refs.append(
            {
                "source": entry["source"],
                "path": entry["path"],
                "excerpt": entry["value"],
            }
        )
    return refs


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MockInterviewContractError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise MockInterviewContractError("invalid_json", value)


def should_retry_mock_interview_format(category: str) -> bool:
    return category in _FORMAT_REPAIR_CATEGORIES


def _format_repair_instruction(category: str) -> str:
    instruction = (
        " Format repair: the previous response failed the structural check "
        f"{category!r}. Return only one raw JSON object matching the declared schema. "
        "Each item must be an object with exactly id, text, and evidence_ids. "
        "evidence_ids must contain one to four IDs copied verbatim from the "
        "evidence_catalog id field. Do not output source, path, or excerpt values "
        "and never invent an ID. Do not repeat or explain the previous response."
    )
    if category == "missing_turn_evidence":
        instruction += _FEEDBACK_TURN_EVIDENCE_RULE
    return instruction


def build_mock_interview_diagnostic(
    failure_category: str,
    repair_attempted: bool,
    repair_count: int,
    elapsed_ms: int,
    provider_request_id: str,
    _sensitive_value: str = "",
) -> dict[str, Any]:
    redacted_request_id = _redact_provider_request_id(provider_request_id)
    return {
        "failure_category": failure_category,
        "repair_attempted": repair_attempted,
        "repair_count": repair_count,
        "elapsed_ms": elapsed_ms,
        "provider_request_id": redacted_request_id,
    }


def _redact_provider_request_id(provider_request_id: str) -> str:
    if not provider_request_id:
        return ""
    digest = hashlib.sha256(provider_request_id.encode("utf-8")).hexdigest()[:12]
    return f"request-redacted-{digest}"


def _assistant_provider_request_id(assistant: Assistant) -> str:
    blocks = getattr(assistant, "provider_blocks", None)
    if not isinstance(blocks, dict):
        return ""
    value = blocks.get("request_id")
    return value if isinstance(value, str) else ""


class MockInterviewProviderError(RuntimeError):
    def __init__(self, category: str, diagnostic: dict[str, Any] | None = None):
        self.category = category
        self.diagnostic = diagnostic or {}
        super().__init__(category)


class MockInterviewUnverifiableError(RuntimeError):
    def __init__(self, category: str, diagnostic: dict[str, Any] | None = None):
        self.category = category
        self.diagnostic = diagnostic or {}
        super().__init__(category)


def generate_feedback(
    model: Any, snapshot: dict[str, Any], turns: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate one strictly validated feedback proposal, or a safe empty result.

    The provider sees only the frozen snapshot and transcript and selects
    evidence IDs; the server expands them into exact frozen citations. Contract
    repair is deliberately bounded to one retry; provider failures remain
    distinguishable from a model response that cannot be verified.
    """
    if not any(
        isinstance(turn.get("answer"), str) and turn["answer"].strip() for turn in turns
    ):
        return dict(SAFE_EMPTY_FEEDBACK), build_mock_interview_diagnostic(
            "no_answer_evidence", False, 0, 0, ""
        )
    if model is None:
        raise MockInterviewProviderError("mock_interview_provider_error")

    started = perf_counter()
    repair_count = 0
    last_category = "invalid_json"
    failure_categories: list[str] = []
    provider_request_id = ""
    evidence_catalog = _provider_evidence_catalog(snapshot, turns)
    for attempt in range(2):
        prompt = _feedback_prompt(evidence_catalog, turns, last_category if attempt else "")
        try:
            assistant = _complete_feedback(model, prompt)
            provider_request_id = _assistant_provider_request_id(assistant)
        except Exception as exc:
            diagnostic = getattr(exc, "diagnostic", None)
            safe_diagnostic = dict(diagnostic) if isinstance(diagnostic, dict) else {}
            if failure_categories:
                safe_diagnostic.setdefault("failure_categories", failure_categories)
            raise MockInterviewProviderError(
                "mock_interview_provider_error",
                safe_diagnostic or None,
            ) from exc
        try:
            parsed = parse_mock_interview_json(assistant.content)
            validated = validate_feedback(parsed, evidence_catalog)
            return validated, build_mock_interview_diagnostic(
                "", repair_count > 0, repair_count,
                int((perf_counter() - started) * 1000), provider_request_id
            )
        except MockInterviewContractError as exc:
            last_category = exc.category
            failure_categories.append(last_category)
            if attempt == 0 and should_retry_mock_interview_format(last_category):
                repair_count = 1
                continue
            break
    raise MockInterviewUnverifiableError(
        last_category,
        {
            "failure_category": last_category,
            "repair_attempted": repair_count > 0,
            "repair_count": repair_count,
            "elapsed_ms": int((perf_counter() - started) * 1000),
            "failure_categories": failure_categories,
            "provider_request_id": _redact_provider_request_id(provider_request_id),
        },
    )


def generate_question(
    model: Any, snapshot: dict[str, Any], turns: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate one strictly validated question plus its sanitized diagnostic."""
    if model is None:
        raise MockInterviewProviderError("mock_interview_provider_error")
    started = perf_counter()
    last_category = "invalid_json"
    failure_categories: list[str] = []
    provider_request_id = ""
    evidence_catalog = _provider_evidence_catalog(snapshot, turns)
    allowed_evidence_ids = [entry["id"] for entry in evidence_catalog]
    answered_turns = [
        turn
        for turn in turns
        if isinstance(turn.get("answer"), str) and str(turn["answer"]).strip()
    ]
    latest_turn = max(answered_turns, key=lambda turn: int(turn["turn_no"]), default=None)
    latest_turn_no = int(latest_turn["turn_no"]) if latest_turn is not None else None
    latest_turn_path = f"/turns/{latest_turn_no:03d}/answer" if latest_turn_no is not None else ""
    latest_turn_evidence_ids = [
        entry["id"]
        for entry in evidence_catalog
        if entry["source"] == "turn" and entry["path"] == latest_turn_path
    ]
    previous_questions = {
        _normalize_question_text(str(turn.get("question", "")))
        for turn in answered_turns
        if str(turn.get("question", "")).strip()
    }
    round_instruction = (
        " This is the opening question. Ask one relevant interview question grounded in the frozen JD, "
        "resume, or confirmed preparation evidence."
        if latest_turn_no is None
        else (
            f" This is a follow-up round; latest answered turn is {latest_turn_no}. "
            f"The latest answer evidence ID is {','.join(latest_turn_evidence_ids)}. "
            "Ask one new follow-up about a concrete implementation detail, difficulty, trade-off, result, "
            "or validation method from that latest answer. The question must not repeat or paraphrase any "
            "previous question, and evidence_ids must include the latest answer evidence ID."
        )
    )
    for attempt in range(2):
        repair_instruction = (
            " Format repair: the previous response failed the structural check "
            f"{last_category!r}. Return only one raw JSON object with exactly "
            "question and evidence_ids. evidence_ids must be a non-empty array "
            "containing one to four IDs copied exactly from evidence_catalog."
            if attempt
            else ""
        )
        messages = [
            Message(
                role="system",
                content=(
                    "只返回原始 JSON，字段必须严格为 question 和 evidence_ids。"
                    "question 必须是非空字符串；evidence_ids 必须是 1 至 4 个字符串组成的数组。"
                    "每个 ID 必须从 evidence_catalog 的 id 字段逐字选择，不得输出 source、path、value 或自行构造 ID。"
                    "不得评分、预测录用或添加额外字段。"
                    "The server expands selected IDs to exact frozen citations; never copy or rewrite citation text."
                    + round_instruction
                    + repair_instruction
                ),
            ),
            Message(
                role="user",
                content=json.dumps(
                        {
                            "snapshot": snapshot,
                            "evidence_catalog": evidence_catalog,
                            "turns": turns,
                            "repair_failure_category": last_category if attempt else None,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        ]
        try:
            schema = {
                "type": "json_schema",
                "json_schema": {
                    "name": "mock_interview_question",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["question", "evidence_ids"],
                        "properties": {
                            "question": {"type": "string", "minLength": 1, "maxLength": 1000},
                            "evidence_ids": {
                                "type": "array",
                                "items": {"type": "string", "enum": allowed_evidence_ids},
                            },
                        },
                    },
                },
            }
            if getattr(model, "supports_json_schema", False):
                assistant = cast(Assistant, model.complete(messages, [], response_format=schema))
            else:
                assistant = cast(Assistant, model.complete(messages, []))
            provider_request_id = _assistant_provider_request_id(assistant)
            elapsed_ms = int((perf_counter() - started) * 1000)
            parsed = parse_mock_interview_json(assistant.content)
            if set(parsed) != {"question", "evidence_ids"}:
                raise MockInterviewContractError("unexpected_field")
            question = parsed["question"]
            if not isinstance(question, str) or not question.strip() or len(question) > 1000:
                raise MockInterviewContractError("blank_value")
            refs = _expand_evidence_ids(parsed["evidence_ids"], evidence_catalog)
            if _normalize_question_text(question) in previous_questions:
                raise MockInterviewContractError("duplicate_question")
            if latest_turn_evidence_ids and not set(latest_turn_evidence_ids).intersection(parsed["evidence_ids"]):
                raise MockInterviewContractError("missing_latest_turn_evidence")
            return (
                {
                    "question": question,
                    "evidence_refs": refs,
                },
                build_mock_interview_diagnostic(
                    "", attempt > 0, 1 if attempt > 0 else 0, elapsed_ms, provider_request_id
                ),
            )
        except MockInterviewProviderError:
            raise
        except MockInterviewContractError as exc:
            last_category = exc.category
            failure_categories.append(last_category)
            if attempt == 0 and should_retry_mock_interview_format(last_category):
                continue
            raise MockInterviewUnverifiableError(
                last_category,
                {
                    "failure_category": last_category,
                    "repair_attempted": attempt > 0,
                    "repair_count": 1 if attempt > 0 else 0,
                    "elapsed_ms": int((perf_counter() - started) * 1000),
                    "failure_categories": failure_categories,
                    "provider_request_id": _redact_provider_request_id(provider_request_id),
                },
            ) from exc
        except Exception as exc:
            diagnostic = getattr(exc, "diagnostic", None)
            safe_diagnostic = dict(diagnostic) if isinstance(diagnostic, dict) else {}
            if failure_categories:
                safe_diagnostic.setdefault("failure_categories", failure_categories)
            raise MockInterviewProviderError(
                "mock_interview_provider_error",
                safe_diagnostic or None,
            ) from exc
    raise MockInterviewUnverifiableError(
        last_category,
        {
            "failure_category": last_category,
            "repair_attempted": True,
            "repair_count": 1,
            "elapsed_ms": int((perf_counter() - started) * 1000),
            "failure_categories": failure_categories,
            "provider_request_id": _redact_provider_request_id(provider_request_id),
        },
    )


def _complete_feedback(model: Any, prompt: str) -> Assistant:
    schema_text = json.dumps(MOCK_INTERVIEW_FEEDBACK_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    messages = [
        Message(
            role="system",
            content=(
                "你是文本模拟面试练习助手。只返回符合 mock-interview-feedback-v1 的原始 JSON。"
                "顶层必须严格包含 schema_version、proposal_status、strengths、practice_points、"
                "follow_up_questions、next_practice_steps，且不得有额外字段。"
                "normal 的每项必须包含 id、text、evidence_ids；evidence_ids 必须是 0 到 4 个字符串组成的数组，"
                "每个 ID 必须从 evidence_catalog 的 id 字段逐字选择，不得输出 source、path、excerpt 或自行构造 ID。"
                "safe_empty 必须是四个空数组的固定结构。"
                "不得评分、预测录用或编造证据。没有可靠建议时返回安全空结构。"
                "完整 JSON Schema 如下：" + schema_text
                + " evidence_catalog is the only allowed evidence directory; every evidence ID must "
                "be copied verbatim from its id field."
                + _FEEDBACK_TURN_EVIDENCE_RULE
            ),
        ),
        Message(role="user", content=prompt),
    ]
    if getattr(model, "supports_json_schema", False):
        return cast(Assistant, model.complete(messages, [], response_format=MOCK_INTERVIEW_FEEDBACK_RESPONSE_FORMAT))
    return cast(Assistant, model.complete(messages, []))


def _feedback_prompt(
    evidence_catalog: list[dict[str, str]],
    turns: list[dict[str, Any]],
    failure_category: str,
) -> str:
    payload: dict[str, Any] = {
        "evidence_catalog": evidence_catalog,
        "turns": turns,
    }
    if failure_category:
        payload["repair_failure_category"] = failure_category
        payload["repair_instruction"] = _format_repair_instruction(failure_category)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
