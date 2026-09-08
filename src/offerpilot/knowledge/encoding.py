"""Knowledge 编码矩阵。

Spec §4.3 编码规则：
- 支持 UTF-8、UTF-8 BOM、UTF-16LE/BE BOM。
- 无 BOM 内容先按 UTF-8 严格解码。
- GBK/GB18030 等无 BOM 内容只在编码检测达到固定置信阈值且严格解码成功时接受。
- 禁止 `errors="ignore"` 和 `errors="replace"`。
- 无法确定编码时拒绝上传。
- Snapshot 记录原始编码、检测方法、规范化版本和 tokenizer 版本。

charset_normalizer 在短 GBK 文本上经常误判为 Big5（两者共享大段双字节码位），
因此本模块对 GBK 家族采用更保守的两阶段策略：

1. 字节长度低于 ``_GBK_MIN_BYTES`` 时直接拒绝：短样本置信度不足。
2. 字节长度达标时要求 best 候选必须是 GBK 家族，且 chaos 指标低于阈值，
   再通过 Python 标准库的严格解码确认。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Spec §4.3 GBK 家族接受阈值。经验值：
# - charset_normalizer 在 100 字节以下的纯中文样本上经常误判 Big5/GBK；
# - 100 字节以上且 chaos <= 0.05 时从未观察到 Big5 误判中文样本。
# 低于该长度或 chaos 高于该阈值的候选一律拒绝，提示用户转为 UTF-8。
_GBK_MIN_BYTES = 100
_GBK_MAX_CHAOS = 0.05

# Spec §4.3 接受的编码名称规范化映射。charset_normalizer 可能返回 "gb18030"
# 或 "gbk"；我们统一以 Python 标准编码名记录。
_ACCEPTED_GBK_FAMILY = {
    "gbk",
    "gb18030",
    "gb2312",
    "chinese",
    "csiso58gb231280",
    "iso_ir_58",
}


@dataclass(frozen=True)
class DecodedContent:
    text: str
    encoding: str
    detection_method: str


class EncodingError(Exception):
    """Spec §4.3 编码识别失败，携带稳定 error_code。"""

    def __init__(self, code: str = "encoding_unknown", message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def decode_source_bytes(raw_bytes: bytes) -> DecodedContent:
    """严格按 Spec §4.3 解码 Source 原始字节。

    顺序：
    1. UTF-8 BOM → utf-8-sig 严格解码。
    2. UTF-16LE/BE BOM → utf-16 严格解码（Python 自动处理 endian，并去除 BOM）。
    3. 无 BOM：先 UTF-8 严格解码。
    4. UTF-8 失败：用 charset_normalizer 检测；若结果为 GBK 家族且 chaos 达标且
       严格解码成功，接受；否则拒绝。
    """

    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        try:
            text = raw_bytes[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EncodingError(
                "encoding_unknown",
                "原文标识为 UTF-8 BOM 但字节序列不是合法 UTF-8；请转换为 UTF-8 后重试",
            ) from exc
        return DecodedContent(
            text=text,
            encoding="utf-8-sig",
            detection_method="bom-utf8",
        )

    if raw_bytes.startswith(b"\xff\xfe"):
        try:
            text = bytes(raw_bytes).decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise EncodingError(
                "encoding_unknown",
                "原文标识为 UTF-16LE BOM 但字节序列不是合法 UTF-16LE；请转换为 UTF-8 后重试",
            ) from exc
        return DecodedContent(
            text=_strip_bom(text),
            encoding="utf-16-le",
            detection_method="bom-utf16le",
        )

    if raw_bytes.startswith(b"\xfe\xff"):
        try:
            text = bytes(raw_bytes).decode("utf-16-be")
        except UnicodeDecodeError as exc:
            raise EncodingError(
                "encoding_unknown",
                "原文标识为 UTF-16BE BOM 但字节序列不是合法 UTF-16BE；请转换为 UTF-8 后重试",
            ) from exc
        return DecodedContent(
            text=_strip_bom(text),
            encoding="utf-16-be",
            detection_method="bom-utf16be",
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return DecodedContent(
            text=text,
            encoding="utf-8",
            detection_method="strict-utf8",
        )

    # UTF-8 严格解码失败：尝试 charset_normalizer 检测 GBK 家族。
    gbk_result = _try_gbk_family(raw_bytes)
    if gbk_result is not None:
        return gbk_result

    raise EncodingError(
        "encoding_unknown",
        "无法确定原文编码；请将文件另存为 UTF-8（含 BOM）后重试",
    )


def _strip_bom(text: str) -> str:
    """Spec §7.1：规范化时不静默删除 Unicode 控制字符；U+FEFF 是 BOM，单独处理。"""

    if text.startswith("﻿"):
        return text[1:]
    return text


def _try_gbk_family(raw_bytes: bytes) -> Optional[DecodedContent]:
    """Spec §4.3：仅当 charset_normalizer 置信度达标且严格解码成功时接受 GBK/GB18030。"""

    if len(raw_bytes) < _GBK_MIN_BYTES:
        return None

    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return None

    try:
        best = from_bytes(raw_bytes).best()
    except Exception:
        return None
    if best is None:
        return None

    encoding_name = (best.encoding or "").lower().replace("-", "_")
    if encoding_name not in _ACCEPTED_GBK_FAMILY:
        return None

    chaos = float(best.chaos or 0.0)
    if chaos > _GBK_MAX_CHAOS:
        return None

    candidate_codec = "gb18030" if encoding_name in {"gb18030", "gb2312", "chinese"} else "gbk"
    try:
        text = bytes(raw_bytes).decode(candidate_codec)
    except UnicodeDecodeError:
        return None

    return DecodedContent(
        text=text,
        encoding=candidate_codec,
        detection_method=f"charset-normalizer:{encoding_name}:chaos-{chaos:.3f}",
    )
