"""Knowledge 产品 tokenizer。

Spec §4.2 要求固定 `knowledge-tokenizer-v1`：pinned `cl100k_base` 计数，不随 Provider
切换而变化。本模块负责加载并缓存该编码器，向 Extractor 与上传 Preflight 提供稳定
token 计数。

tiktoken cl100k_base 在网络受限环境下需要下载 merges 文件。产品 tokenizer 是
固定契约，加载失败时必须拒绝 Extraction，不能用估算值越过 token 上限门禁。
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import tiktoken


TOKENIZER_VERSION = "cl100k_base-1"
TOKENIZER_KIND = "cl100k_base"
_MAX_TOKEN_LIMIT = 64_000


class TokenizerUnavailableError(RuntimeError):
    """固定 product tokenizer 不可用，调用方必须终止当前 ingest。"""


@dataclass(frozen=True)
class TokenCount:
    count: int
    tokenizer_version: str
    fallback: bool


@functools.lru_cache(maxsize=1)
def _load_encoding() -> "tiktoken.Encoding | None":
    try:
        import tiktoken
    except OSError:
        return None
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(TOKENIZER_KIND)
    except Exception:
        return None


def tokenizer_status() -> tuple[str, bool]:
    """返回固定 tokenizer 状态；加载失败时抛出稳定异常。"""

    if _load_encoding() is None:
        raise TokenizerUnavailableError(
            "tokenizer_unavailable: cl100k_base 无法加载，请安装 tiktoken 并准备编码文件"
        )
    return TOKENIZER_VERSION, False


def count_tokens(text: str) -> TokenCount:
    """对 `text` 进行稳定 token 计数。

    Spec §4.2：64,000 product tokens 上限基于 cl100k_base；任何解码路径都不允许
    `errors="ignore"` 或 `errors="replace"`。token 计数仅用于上限门禁，不参与
    Evidence 身份。
    """

    encoding = _load_encoding()
    if encoding is None:
        raise TokenizerUnavailableError(
            "tokenizer_unavailable: cl100k_base 无法加载，请安装 tiktoken 并准备编码文件"
        )
    if not text:
        return TokenCount(count=0, tokenizer_version=TOKENIZER_VERSION, fallback=False)
    encoded = encoding.encode(text)
    return TokenCount(
        count=len(encoded),
        tokenizer_version=TOKENIZER_VERSION,
        fallback=False,
    )


def max_token_limit() -> int:
    return _MAX_TOKEN_LIMIT
