"""Knowledge 固定 tokenizer；独立加载经过校验的 cl100k_base 离线资源。

保持 knowledge-tokenizer-v1 的精确计数与 64,000 token 门禁，不依赖 Provider SDK
导入时的缓存初始化。显式缓存配置优先；缺失、损坏或禁用缓存时拒绝加载，不下载、
删除资源或估算 token。默认只读已安装 LiteLLM 分发包自带的同一编码数据，不导入 SDK。
"""

from __future__ import annotations

import base64
import functools
import hashlib
import os
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import tiktoken


TOKENIZER_VERSION = "cl100k_base-1"
TOKENIZER_KIND = "cl100k_base"
_MAX_TOKEN_LIMIT = 64_000
_ENCODING_CACHE_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
_ENCODING_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
# cl100k_base-1 的定义与固定 merges 数据共同确定 token 身份。
_PATTERN = r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"""
_SPECIAL_TOKENS = {
    "<|endoftext|>": 100257,
    "<|fim_prefix|>": 100258,
    "<|fim_middle|>": 100259,
    "<|fim_suffix|>": 100260,
    "<|endofprompt|>": 100276,
}


class TokenizerUnavailableError(RuntimeError):
    """固定 product tokenizer 不可用，调用方必须终止当前 ingest。"""


@dataclass(frozen=True)
class TokenCount:
    count: int
    tokenizer_version: str
    fallback: bool


def _encoding_resource() -> Path:
    for variable in ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR"):
        if variable in os.environ:
            configured = os.environ[variable]
            if not configured:
                raise TokenizerUnavailableError("explicit tokenizer cache is disabled")
            return Path(configured) / _ENCODING_CACHE_KEY
    custom = os.environ.get("CUSTOM_TIKTOKEN_CACHE_DIR")
    if custom:
        return Path(custom) / _ENCODING_CACHE_KEY
    return Path(str(distribution("litellm").locate_file(
        "litellm/litellm_core_utils/tokenizers/" + _ENCODING_CACHE_KEY
    )))


@functools.lru_cache(maxsize=1)
def _load_encoding() -> "tiktoken.Encoding | None":
    try:
        # 校验与解析同一次读取的 bytes，不再次打开文件，也不经过会删除/下载的缓存层。
        data = _encoding_resource().read_bytes()
        if hashlib.sha256(data).hexdigest() != _ENCODING_SHA256:
            return None
        ranks: dict[bytes, int] = {}
        for line in data.splitlines():
            if line:
                token, rank = line.split()
                ranks[base64.b64decode(token, validate=True)] = int(rank)
        import tiktoken

        return tiktoken.Encoding(
            name=TOKENIZER_KIND,
            pat_str=_PATTERN,
            mergeable_ranks=ranks,
            special_tokens=dict(_SPECIAL_TOKENS),
        )
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
