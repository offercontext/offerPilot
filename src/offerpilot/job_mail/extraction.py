"""Bounded, offline mail evidence extraction. Mail never becomes an instruction.

This deliberately conservative first-batch recognizer is not an AI provider and
cannot call tools. Ambiguous or unsupported notices remain review-only.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Any

MAX_RAW_BYTES = 1_000_000
MAX_BODY_BYTES = 30_000


class MailRejected(ValueError):
    pass


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "object", "svg", "head"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "object", "svg", "head"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def clean_text(text: str, *, html: bool = False) -> tuple[str, bool]:
    if html:
        parser = _TextOnly()
        parser.feed(text[:MAX_RAW_BYTES])
        text = "".join(parser.parts)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    raw = text.encode("utf-8")
    return raw[:MAX_BODY_BYTES].decode("utf-8", errors="ignore").strip(), len(raw) > MAX_BODY_BYTES


@dataclass(frozen=True)
class ParsedMail:
    uid: int
    uidvalidity: str
    folder: str
    message_id: str
    received_at: str
    sender: str
    subject: str
    body_text: str
    fingerprint: str
    truncated: bool = False
    source: str = "imap"


def parse_mail(raw: bytes, *, uid: int, uidvalidity: str, folder: str,
               received_at: datetime) -> ParsedMail:
    if len(raw) > MAX_RAW_BYTES:
        raise MailRejected("message_too_large")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    part = message.get_body(preferencelist=("plain", "html"))
    content = ""
    is_html = False
    if part is not None and part.get_content_disposition() != "attachment":
        try:
            content = str(part.get_content())
        except (LookupError, UnicodeError):
            raise MailRejected("invalid_encoding") from None
        is_html = part.get_content_type() == "text/html"
    body, truncated = clean_text(content, html=is_html)
    sender = parseaddr(str(message.get("From", "")))[1][:320]
    subject = str(message.get("Subject", ""))[:500]
    message_id = str(message.get("Message-ID", ""))[:500].strip()
    # Message-ID alone is spoofable and can be reused. Including exact canonical
    # content prevents template/semantic merges, while permitting folder moves.
    digest = hashlib.sha256("\0".join((message_id, sender, subject, body)).encode()).hexdigest()
    return ParsedMail(uid, uidvalidity, folder, message_id,
                      received_at.astimezone(timezone.utc).isoformat(), sender,
                      subject, body, digest, truncated)


@dataclass(frozen=True)
class Candidate:
    action: str
    event_type: str
    subtype: str
    time_mode: str
    scheduled_at: str | None
    duration_minutes: int | None
    location: str
    company: str
    position: str
    evidence: dict[str, str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.action == "update_event":
            result["proposed_fields"] = {"location": self.location} if self.location else {}
        return result


def _label(text: str, label: str) -> str:
    match = re.search(rf"(?:^|\n)\s*(?:{label})\s*[:：]\s*([^\n]+)", text)
    return match.group(1).strip()[:200] if match else ""


def recognize(mail: ParsedMail) -> list[Candidate]:
    """Return evidence, never executable model/tool output or implied approval.

    Recognize categories in natural notices. Only explicit ISO dates including
    timezone are machine-filled; dates without a timezone, relative dates,
    multiple appointments and forwarded chains require the user to fill fields.
    """
    text = mail.subject + "\n" + mail.body_text
    if not re.search(r"面试|笔试|测评|录用|offer|interview|assessment", text, re.I):
        return []
    event_type = "interview"
    subtype = ""
    if re.search(r"测评|assessment", text, re.I):
        event_type, subtype = "written_test", "assessment"
    elif re.search(r"笔试", text):
        event_type = "written_test"
    action = "create_event"
    reason = "请核对邮件依据并选择目标投递；规则提取不验证发件身份。"
    mode = "fixed"
    if re.search(r"(?:补充|更新)(?:面试)?(?:地点|会议地址|会议链接)|(?:地点|会议链接|会议地址)补充", text):
        action, reason = "update_event", "地点补充需要选择已有事件并核对；保留未提及的时间、时长和备注。"
    if re.search(r"取消|撤销|\bcancel(?:led|ed|lation)?\b|\bpostpon(?:ed|ement)\b", text, re.I):
        action, reason = "manual_only", "取消通知仅保留建议，请到原记录人工处理。"
    elif re.search(r"改期|改至|调整至|reschedul|推迟|延期", text, re.I):
        action, reason = "manual_only", "改期需确认原安排与提醒，本批仅保留建议。"
    elif re.search(r"录用|\boffer\b", text, re.I):
        action, reason = "manual_only", "Offer仅保留建议，收到不代表接受，请人工录入。"
    if re.search(r"截止|之前|日前|deadline|完成.{0,6}前|\b(?:by|before|no later than)\s+\d{4}-\d{2}-\d{2}", text, re.I):
        mode, action = "deadline", "manual_only"
    elif re.search(r"任选|时间窗口|between|至.{0,12}(任选|期间)|\bfrom\s+\d{4}-\d{2}-\d{2}.{0,30}\b(?:to|until|through)\b", text, re.I):
        mode, action = "window", "manual_only"
    elif re.search(r"另行通知|待定|to be confirmed", text, re.I):
        mode, action = "unknown", "manual_only"
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})\b", text)
    scheduled = None
    if len(dates) == 1 and mode == "fixed":
        try:
            scheduled = datetime.fromisoformat(dates[0].replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    duration = re.search(r"(?:时长|持续|duration)\s*[:：]?\s*(\d{1,4})\s*(?:分钟|minutes|min)", text, re.I)
    minutes = int(duration.group(1)) if duration else None
    if minutes is not None and not 0 < minutes <= 1440:
        minutes = None
    evidence: dict[str, str] = ({"notice": mail.body_text[:3000]} if mail.body_text else {})
    if scheduled and dates[0] in mail.body_text:
        evidence["scheduled_at"] = dates[0]
    if duration and duration.group(0) in mail.body_text:
        evidence["duration_minutes"] = duration.group(0)
    if mail.truncated or len(dates) > 1 or re.search(r"转发|原始邮件|Forwarded|Original Message", text, re.I):
        action = "manual_only"
        reason = "邮件被截断、包含多个时间或转发引用；请逐项人工核对。"
    if mode != "fixed":
        reason = "截止、窗口或时间待定本批不写入日程，不会虚构时长或午夜。"
    if action == "manual_only" and mode == "fixed" and scheduled is None:
        mode = "unknown"
    return [Candidate(action, event_type, subtype, mode, scheduled, minutes,
                      _label(text, "地点|location"), _label(text, "公司|company"),
                      _label(text, "岗位|position"), evidence, reason)]
