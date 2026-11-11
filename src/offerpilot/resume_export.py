"""Offline, read-only exports of saved, explicit resume fields.

Never use source files or source text as a fallback, including for upload copies.
Business map: docs/architecture/code-map/materials.md.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
from fpdf import FPDF
from fpdf.enums import WrapMode

MAX_SOURCE_BYTES = 512 * 1024
MAX_TEXT_BYTES = 128 * 1024
MAX_STRING_BYTES = 32 * 1024
MAX_ITEMS = 100
FONT_PATH = Path(__file__).parent / 'assets' / 'fonts' / 'NotoSansSC-Regular.ttf'
SCHEMA = 'resume-export-v1'
IGNORED = {'raw_text', 'import_review'}
FIELDS = {
    'contact': ('name', 'email', 'phone', 'location'),
    'career_intent': ('target_roles', 'target_locations'),
    'education': ('school', 'degree', 'major', 'start_date', 'end_date'),
    'experience': ('company', 'title', 'start_date', 'end_date', 'highlights'),
    'projects': ('name', 'role', 'start_date', 'end_date', 'highlights'),
}
LABELS = {
    'zh-CN': ('求职意向', '教育经历', '工作经历', '项目经历', '技能', '补充信息'),
    'en-US': ('Career interests', 'Education', 'Experience', 'Projects', 'Skills', 'Additional information'),
}


class ResumeExportError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = f'resume_export_{code}'
        self.message = message
        self.status = status
        super().__init__(self.code)


def invalid() -> ResumeExportError:
    return ResumeExportError('invalid_source', '简历字段格式无效，请在编辑器中检查并保存。')


def validate_request(payload: Any, *, preview: bool) -> tuple[str, str, str]:
    keys = {'locale'} if preview else {'locale', 'format', 'source_fingerprint'}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ResumeExportError('invalid_request', '导出请求字段无效。')
    locale = payload.get('locale')
    fmt = 'pdf' if preview else payload.get('format')
    fingerprint = '' if preview else payload.get('source_fingerprint')
    if (not isinstance(locale, str) or locale not in LABELS
            or not isinstance(fmt, str) or fmt not in ('pdf', 'docx')
            or not isinstance(fingerprint, str)
            or (not preview and not re.fullmatch(r'[0-9a-f]{64}', fingerprint))):
        raise ResumeExportError('invalid_request', '请选择支持的语言、格式，并重新预览后下载。')
    return locale, fmt, fingerprint


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise invalid()
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError as exc:
        raise invalid() from exc
    if size > MAX_STRING_BYTES:
        raise ResumeExportError('too_large', '单个字段过长，请缩短后重新导出。')
    # Reject invisible controls and invalid XML rather than silently changing text.
    if any(unicodedata.category(c) in ('Cc', 'Cs') and c not in '\n\r\t' for c in value):
        raise ResumeExportError('unsupported_text', '简历包含不支持的控制字符，请检查后重试。')
    return value


def _array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise invalid()
    if len(value) > MAX_ITEMS:
        raise ResumeExportError('too_large', '简历条目过多，请减少后重新导出。')
    return value


def _object(value: Any, allowed: tuple[str, ...] | set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid()
    if set(value) - set(allowed):
        raise ResumeExportError(
            'unsupported_fields', '简历包含暂不支持导出的自定义字段。请将其整理至补充信息后重试。'
        )
    return value


@dataclass(frozen=True)
class ExportSnapshot:
    resume_id: int
    title: str
    locale: str
    fingerprint: str
    blocks: tuple[tuple[str, str], ...]


def source_fingerprint(resume: Any, locale: str) -> str:
    """Include all relevant saved fields, even invalid ones, to reject stale previews first."""
    try:
        content = json.loads(resume.content_json)
    except (ValueError, TypeError, RecursionError):
        content = resume.content_json
    if isinstance(content, dict):
        content = {key: value for key, value in content.items() if key not in IGNORED}
    return hashlib.sha256(json.dumps(
        [SCHEMA, resume.id, resume.title, locale, content], ensure_ascii=True,
        sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def snapshot(resume: Any, locale: str) -> ExportSnapshot:
    """Validate full saved content; project only the editor's explicit user fields."""
    raw = resume.content_json
    try:
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_SOURCE_BYTES:
            raise ResumeExportError('too_large', '简历数据过大，无法导出。')
        content = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ResumeExportError):
            raise
        raise invalid() from exc
    allowed = set(FIELDS) | {'skills', 'additional_text'} | IGNORED
    _object(content, allowed)
    title = _string(resume.title)
    if not title.strip() or any(c in title for c in '\r\n\t'):
        raise ResumeExportError(
            'invalid_source', '请填写非空的单行简历标题，移除换行或制表符后保存。'
        )
    if len(title.encode('utf-8')) > 512:
        raise ResumeExportError('too_large', '简历标题过长，请缩短后重新导出。')
    projected: dict[str, Any] = {}
    for key in FIELDS:
        default: Any = {} if key in ('contact', 'career_intent') else []
        value = content.get(key, default)
        entries = [value] if isinstance(default, dict) else _array(value)
        normalized = []
        for entry in entries:
            entry = _object(entry, FIELDS[key])
            item: dict[str, Any] = {}
            for field, text in entry.items():
                item[field] = ([_string(s) for s in _array(text)]
                               if field in ('target_roles', 'target_locations', 'highlights')
                               else _string(text))
            normalized.append(item)
        projected[key] = normalized[0] if isinstance(default, dict) else normalized
    projected['skills'] = [_string(s) for s in _array(content.get('skills', []))]
    projected['additional_text'] = _string(content.get('additional_text', ''))
    encoded = json.dumps(projected, ensure_ascii=False, sort_keys=True).encode('utf-8')
    if len(encoded) > MAX_TEXT_BYTES:
        raise ResumeExportError('too_large', '简历内容过长，请缩短后重新导出。')
    fingerprint = source_fingerprint(resume, locale)
    blocks: list[tuple[str, str]] = []

    def add(kind: str, value: str) -> None:
        if value.strip():
            blocks.append((kind, value))

    contact = projected['contact']
    add('title', contact.get('name', '') or title)
    add('body', ' · '.join(contact.get(k, '') for k in ('email', 'phone', 'location')
                         if contact.get(k, '').strip()))
    body_start = len(blocks)
    labels = LABELS[locale]
    career = projected['career_intent']
    intent = ' · '.join(s for k in FIELDS['career_intent'] for s in career.get(k, []) if s.strip())
    if intent:
        add('heading', labels[0])
        add('body', intent)
    for index, key in enumerate(('education', 'experience', 'projects'), 1):
        section: list[tuple[str, str]] = []
        for entry in projected[key]:
            parts = [entry.get(k, '') for k in FIELDS[key]
                     if k not in ('start_date', 'end_date', 'highlights')]
            heading = ' · '.join(s for s in parts if s.strip())
            dates = ' - '.join(entry.get(k, '') for k in ('start_date', 'end_date')
                               if entry.get(k, '').strip())
            if heading or dates:
                section.append(('entry', ' | '.join(s for s in (heading, dates) if s)))
            section.extend(('bullet', s) for s in entry.get('highlights', []) if s.strip())
        if section:
            add('heading', labels[index])
            blocks.extend(section)
    if any(s.strip() for s in projected['skills']):
        add('heading', labels[4])
        add('body', ' · '.join(s for s in projected['skills'] if s.strip()))
    if projected['additional_text'].strip():
        add('heading', labels[5])
        add('body', projected['additional_text'])
    if len(blocks) == body_start and not any(s.strip() for s in contact.values()):
        raise ResumeExportError(
            'empty_content', '没有可导出的已保存内容。请先将原文整理为结构化字段或补充信息并保存。'
        )
    return ExportSnapshot(resume.id, title, locale, fingerprint, tuple(blocks))


@lru_cache(maxsize=1)
def _font_characters() -> frozenset[int]:
    with TTFont(FONT_PATH) as font:
        return frozenset(font.getBestCmap())


def _check_characters(source: ExportSnapshot) -> None:
    supported = _font_characters()
    if any(ord(c) not in supported and c not in '\r\n\t'
           for _, text in source.blocks for c in text):
        raise ResumeExportError(
            'unsupported_text', '简历包含当前字体不支持的字符，请替换特殊符号后重试。'
        )


def render_pdf(source: ExportSnapshot) -> bytes:
    _check_characters(source)
    pdf = FPDF(format='A4')
    pdf.set_margins(18, 17, 18)
    pdf.set_auto_page_break(auto=True, margin=17)
    pdf.set_creation_date(datetime(2000, 1, 1, tzinfo=timezone.utc))
    pdf.add_font('Resume', fname=str(FONT_PATH))
    pdf.add_page()
    for kind, text in source.blocks:
        size = {'title': 20, 'heading': 12, 'entry': 10.5}.get(kind, 10)
        if kind == 'heading':
            if pdf.will_page_break(17):
                pdf.add_page()
            pdf.ln(3)
        pdf.set_font('Resume', size=size)
        pdf.set_text_color(*( (28, 55, 78) if kind in ('title', 'heading') else (30, 30, 30)))
        pdf.multi_cell(w=0, h=size * 0.48 + 1, text=('- ' if kind == 'bullet' else '')
                       + text.replace('\r\n', '\n').replace('\r', '\n').replace('\t', '    '),
                       new_x='LMARGIN', new_y='NEXT', wrapmode=WrapMode.CHAR)
        pdf.ln(1)
    return bytes(pdf.output())


def render_docx(source: ExportSnapshot) -> bytes:
    _check_characters(source)
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(17)
    section.left_margin = section.right_margin = Mm(18)
    for name, size in (('Normal', 10), ('Title', 20), ('Heading 1', 12)):
        style = doc.styles[name]
        style.font.name = 'Noto Sans CJK SC'
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(30, 30, 30)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), 'Noto Sans CJK SC')
        style.paragraph_format.space_after = Pt(4)
    # The bundled Word template can carry a title border; keep a clean resume.
    for border in doc.styles.element.xpath('.//w:pBdr'):
        border.getparent().remove(border)
    doc.core_properties.author = ''
    doc.core_properties.last_modified_by = ''
    doc.core_properties.title = ''
    doc.core_properties.created = datetime(2000, 1, 1, tzinfo=timezone.utc)
    doc.core_properties.modified = datetime(2000, 1, 1, tzinfo=timezone.utc)
    for kind, text in source.blocks:
        style_name = {'title': 'Title', 'heading': 'Heading 1', 'bullet': 'List Bullet'}.get(kind)
        paragraph = doc.add_paragraph(text, style=style_name)
        if kind == 'entry':
            paragraph.paragraph_format.keep_with_next = True
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def response_headers(source: ExportSnapshot, fmt: str, *, preview: bool) -> dict[str, str]:
    # Windows reserved names, paths, quotes and CR/LF cannot enter either filename.
    title = re.sub(r'[^\w\- .]', '_', source.title, flags=re.UNICODE).strip(' .')[:80]
    name = f'resume-{source.resume_id}-{title or "export"}.{fmt}'
    disposition = 'inline' if preview else 'attachment'
    return {
        'Content-Disposition': f'{disposition}; filename="resume-{source.resume_id}.{fmt}"; '
                               f"filename*=UTF-8''{quote(name, safe='')}",
        'X-Resume-Export-Fingerprint': source.fingerprint,
        'X-Resume-Export-Title': quote(source.title, safe=''),
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    }
