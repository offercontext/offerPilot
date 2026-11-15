from __future__ import annotations

# 业务地图：docs/architecture/code-map/materials.md（JD/简历分析）、questions.md（复盘生成题库）。

import json
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping

from offerpilot.ai.agent_contracts import ChatModel
from offerpilot.ai.types import Message
from offerpilot.repositories.application_jd_versions import (
    ApplicationJDService,
    JDVersionError,
    JDVersionValidationError,
)
from offerpilot.repositories.jd import JDAnalysesRepository, JDAnalysisCreate
from offerpilot.repositories.notes import NotesRepository
from offerpilot.repositories.questions import QuestionCreate, QuestionsRepository, question_hash
from offerpilot.repositories.resumes import ResumeMatchCreate, ResumesRepository


class InvalidJSONReply(RuntimeError):
    """The provider returned a response that violates the requested JSON contract."""


@dataclass
class JDAnalysisResult:
    id: int
    application_id: int | None
    jd_source: str
    result: dict[str, Any]


@dataclass
class ResumeMatchResult:
    id: int
    resume_id: int
    application_id: int | None
    result: dict[str, Any]


@dataclass
class GeneratedQuestionsResult:
    count: int
    skipped: int
    questions: list[Any]


MAX_WORKFLOW_REPLY_BYTES = 128 * 1024


class WorkflowError(ValueError):
    """A safe, transport-independent error for these three finite use cases."""

    def __init__(self, message: str, status_code: int, code: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class JDInput:
    # None means the caller did not supply text. Even explicit empty text cannot
    # be silently replaced by the saved Application JD.
    jd_text: str | None = None
    jd_url: str = ""
    application_id: int | None = None
    jd_version_id: int | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> JDInput:
        return cls(
            jd_text=str(payload.get("jd_text") or "") if "jd_text" in payload else None,
            jd_url=str(payload.get("jd_url") or ""),
            application_id=payload.get("application_id"),
            jd_version_id=payload.get("jd_version_id"),
        )


def resolve_jd_input(versions: ApplicationJDService, request: JDInput) -> JDInput:
    application_id = request.application_id
    if application_id is not None:
        if type(application_id) is not int or not 1 <= application_id <= 2**63 - 1:
            raise WorkflowError(
                "application_id must be a positive integer", 422, "application_id_invalid"
            )
        if (
            request.jd_text is not None
            or type(request.jd_version_id) is not int
            or not 1 <= request.jd_version_id <= 2**63 - 1
        ):
            raise WorkflowError("请使用当前岗位资料版本", 422, "application_jd_version_required")
        try:
            version = versions.require_current_version(application_id, request.jd_version_id)
        except JDVersionValidationError as exc:
            raise WorkflowError("岗位资料版本无效", 422, "application_jd_version_required") from exc
        except JDVersionError as exc:
            raise WorkflowError(
                "岗位资料已变化，请重新加载", 409, "application_jd_source_conflict"
            ) from exc
        jd_text = version.jd_text
        jd_version_id = version.id
    else:
        if request.jd_version_id is not None:
            raise WorkflowError(
                "jd_version_id requires application_id", 422, "application_jd_version_required"
            )
        jd_text = request.jd_text or ""
        jd_version_id = None
    if not jd_text.strip():
        raise WorkflowError("jd_text is required", 422, "jd_text_required")
    if request.jd_url:
        raise WorkflowError("jd_url is record-only", 422, "jd_url_not_supported")
    return JDInput(jd_text=jd_text, application_id=application_id, jd_version_id=jd_version_id)


def _complete_workflow_json(load_model: Callable[[], ChatModel], user: str) -> dict[str, Any]:
    # Load only after input/source validation, preserving HTTP's 4xx-before-503
    # contract. Provider and parser details can contain secrets or source text.
    model = load_model()
    try:
        return complete_json(
            model,
            system=structured_ai_system(),
            user=user,
            max_reply_bytes=MAX_WORKFLOW_REPLY_BYTES,
            reject_non_finite=True,
            reject_duplicate_keys=True,
        )
    except InvalidJSONReply as exc:
        raise WorkflowError(
            "AI response must be a valid JSON object", 502, "ai_invalid_response"
        ) from exc
    except RuntimeError as exc:
        raise WorkflowError("AI provider request failed", 502, "ai_provider_error") from exc


def _source_commit_error(exc: JDVersionError) -> WorkflowError:
    if isinstance(exc, JDVersionValidationError):
        return WorkflowError("JD version is invalid.", 422, "application_jd_version_required")
    return WorkflowError(
        "JD source changed while the provider was running.", 409, "application_jd_source_conflict"
    )


def analyze_jd(
    load_model: Callable[[], ChatModel],
    repo: JDAnalysesRepository,
    versions: ApplicationJDService,
    request: JDInput,
) -> JDAnalysisResult:
    source = resolve_jd_input(versions, request)
    assert source.jd_text is not None
    result = _complete_workflow_json(load_model, jd_analysis_prompt(source.jd_text))
    try:
        analysis = repo.create_for_current(
            JDAnalysisCreate(
                application_id=source.application_id,
                jd_source="text",
                jd_text=source.jd_text,
                result=json.dumps(result, ensure_ascii=False),
                jd_version_id=source.jd_version_id,
            )
        )
    except JDVersionError as exc:
        raise _source_commit_error(exc) from exc
    return JDAnalysisResult(
        id=analysis.id,
        application_id=source.application_id,
        jd_source="text",
        result=result,
    )


def match_resume(
    load_model: Callable[[], ChatModel],
    repo: ResumesRepository,
    versions: ApplicationJDService,
    resume_id: int,
    request: JDInput,
) -> ResumeMatchResult:
    resume = repo.get(resume_id)
    if resume is None:
        raise WorkflowError("Resume not found", 404)
    if not resume.parsed_data:
        raise WorkflowError("Resume has no text content", 400)
    source = resolve_jd_input(versions, request)
    assert source.jd_text is not None
    result = _complete_workflow_json(
        load_model,
        resume_match_prompt(resume.parsed_data, source.jd_text),
    )
    try:
        match = repo.create_match_for_current(
            ResumeMatchCreate(
                resume_id=resume_id,
                application_id=source.application_id,
                jd_text=source.jd_text,
                result=json.dumps(result, ensure_ascii=False),
                jd_version_id=source.jd_version_id,
            )
        )
    except JDVersionError as exc:
        raise _source_commit_error(exc) from exc
    return ResumeMatchResult(
        id=match.id,
        resume_id=resume_id,
        application_id=source.application_id,
        result=result,
    )


def generate_questions(
    load_model: Callable[[], ChatModel],
    questions: QuestionsRepository,
    notes: NotesRepository,
    source: str = "notes",
    topic: str = "",
    application_id: int = 0,
    count: int = 8,
) -> GeneratedQuestionsResult:
    # 当前只从面试笔记的 questions 字段生成题库；来源为空时在加载模型之前失败。
    # 这是题库生成路径，不隐式读取 Knowledge，也不创建模拟面试 Attempt。
    source = source.strip() or "notes"
    if source != "notes":
        raise WorkflowError("不支持的来源类型", 400)
    if type(application_id) is not int or not 0 <= application_id <= 2**63 - 1:
        raise WorkflowError(
            "application_id must be a non-negative integer", 422, "application_id_invalid"
        )
    if type(count) is not int:
        raise WorkflowError("count must be an integer", 422, "question_count_invalid")
    note_rows = notes.list(application_id=application_id) if application_id > 0 else notes.list()
    context_text = "\n\n".join(
        note.questions.strip() for note in note_rows if note.questions.strip()
    )
    if not context_text.strip():
        raise WorkflowError("所选来源没有可用于生成题目的内容", 400)
    result = _complete_workflow_json(
        load_model,
        questions_prompt("面试复盘真题", context_text, clamp_question_count(count)),
    )
    saved, skipped = persist_generated_questions(
        questions,
        result.get("questions", []),
        source_type="ai_notes",
        application_id=application_id if application_id > 0 else None,
        topic=topic,
    )
    return GeneratedQuestionsResult(count=len(saved), skipped=skipped, questions=saved)


def complete_json(
    model: ChatModel,
    system: str,
    user: str,
    *,
    strict_json: bool = False,
    max_reply_bytes: int | None = None,
    reject_non_finite: bool = False,
    reject_duplicate_keys: bool = False,
) -> dict[str, Any]:
    try:
        assistant = model.complete(
            [Message(role="system", content=system), Message(role="user", content=user)],
            [],
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        if not isinstance(assistant.content, str):
            raise InvalidJSONReply("AI response content must be text")
        if max_reply_bytes is not None and len(assistant.content.encode("utf-8")) > max_reply_bytes:
            raise InvalidJSONReply("AI response exceeds the configured byte limit")
        return parse_json_reply(
            assistant.content,
            allow_fenced=not strict_json,
            reject_non_finite=strict_json or reject_non_finite,
            reject_duplicate_keys=strict_json or reject_duplicate_keys,
        )
    except InvalidJSONReply:
        raise
    except Exception as exc:
        raise InvalidJSONReply(str(exc)) from exc


def parse_json_reply(
    reply: str,
    *,
    allow_fenced: bool = True,
    reject_non_finite: bool = False,
    reject_duplicate_keys: bool = False,
) -> dict[str, Any]:
    text = reply.strip()
    if not allow_fenced and (text.startswith("```") or text.endswith("```")):
        raise RuntimeError("AI response must be raw JSON without Markdown fences")
    if allow_fenced and text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :].strip()
        fence = text.rfind("```")
        if fence >= 0:
            text = text[:fence].strip()
    json_options: dict[str, Any] = {}
    if reject_non_finite:
        json_options["parse_constant"] = _reject_non_finite_json_constant
        json_options["parse_float"] = _parse_finite_json_float
    if reject_duplicate_keys:
        json_options["object_pairs_hook"] = _reject_duplicate_json_keys
    value = json.loads(text, **json_options)
    if not isinstance(value, dict):
        raise RuntimeError("AI response must be a JSON object")
    return value


def _parse_finite_json_float(value: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("non-finite JSON number is not allowed")
    return number


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def structured_ai_system() -> str:
    return (
        "你是一名专业的招聘求职分析师。只输出 JSON，不要使用 markdown 代码块。"
        "所有文字使用简体中文，数组字段为空时返回 []。"
    )


def jd_analysis_prompt(jd_text: str) -> str:
    return f"""请分析以下岗位描述（JD），输出如下 JSON：
{{
  "summary": "一句话总结这个岗位",
  "requirements": ["关键要求点，每条一句话"],
  "tech_stack": ["涉及的技术栈/工具"],
  "experience_years": "要求的年限，如 3-5 年，无要求填 不限",
  "education": "学历要求，如 本科及以上，无要求填 不限",
  "highlights": ["这个岗位吸引人的亮点"],
  "suggestions": ["针对求职者的准备建议，每条一句话"]
}}

JD 内容：
{truncate_for_prompt(jd_text)}"""


def resume_match_prompt(resume_text: str, jd_text: str) -> str:
    return f"""请对比以下简历和岗位 JD，评估匹配度，输出如下 JSON：
{{
  "match_score": 0到100的整数匹配度,
  "matched": ["简历中与 JD 匹配的点"],
  "gaps": ["简历中相对 JD 缺失或薄弱的点"],
  "suggestions": ["针对这份 JD 该如何优化简历/补足能力的建议"],
  "summary": "一句话总评"
}}

简历内容：
{truncate_for_prompt(resume_text)}

JD 内容：
{truncate_for_prompt(jd_text)}"""


def questions_prompt(source_label: str, context_text: str, count: int) -> str:
    return f"""你是一名资深技术面试官。请基于以下【{source_label}】设计 {count} 道面试题。
严格输出如下 JSON，不要输出多余文字：
{{
  "questions": [
    {{
      "category": "分类",
      "difficulty": "easy|medium|hard",
      "question": "题目",
      "reference_answer": "参考答案要点",
      "tags": ["关键词"]
    }}
  ]
}}

材料内容：
{truncate_for_prompt(context_text)}"""


def persist_generated_questions(
    repo: QuestionsRepository,
    generated: Any,
    source_type: str,
    application_id: int | None,
    topic: str = "",
) -> tuple[list[Any], int]:
    # 同时对已保存题目和本批结果做文本归一化去重；skipped 只计重复题，非结构项直接忽略。
    if not isinstance(generated, list):
        return [], 0
    seen = set(repo.hashes())
    to_create: list[QuestionCreate] = []
    skipped = 0
    for item in generated:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or "").strip()
        if not text:
            continue
        digest = question_hash(text)
        if digest in seen:
            skipped += 1
            continue
        seen.add(digest)
        tags_value = item.get("tags") or []
        tags = [str(tag) for tag in tags_value] if isinstance(tags_value, list) else []
        to_create.append(
            QuestionCreate(
                application_id=application_id,
                topic=topic,
                category=str(item.get("category") or "").strip(),
                difficulty=normalize_difficulty(str(item.get("difficulty") or "medium")),
                question=text,
                reference_answer=str(item.get("reference_answer") or "").strip(),
                tags=tags,
                source_type=source_type,
                status="new",
            )
        )
    return repo.bulk_create(to_create), skipped


def normalize_difficulty(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"easy", "简单"}:
        return "easy"
    if normalized in {"hard", "困难", "难"}:
        return "hard"
    return "medium"


def clamp_question_count(count: int) -> int:
    if count <= 0:
        return 8
    return min(count, 20)


def truncate_for_prompt(value: str, max_chars: int = 12000) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...(已截断)"
