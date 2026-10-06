# Build on the target OS. PyInstaller does not cross-compile Windows executables.
from pathlib import Path
from importlib.metadata import distribution
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["TIKTOKEN_CACHE_DIR"] = str(ROOT / "desktop" / "build" / "tiktoken-cache")

# LiteLLM dispatches providers through lazy imports and reads bundled pricing,
# tokenizer and provider JSON. SQLAlchemy resolves sqlite and uvicorn its worker
# implementations dynamically; tiktoken discovers its namespace extension.
data = copy_metadata("offerpilot", recursive=True)
for package in ("litellm", "tiktoken", "tzdata", "certifi", "jsonschema_specifications"):
    data += collect_data_files(package)
data += [(str(ROOT / "desktop" / "build" / "tiktoken-cache"), "tiktoken-cache")]
hidden = [
    "offerpilot.agent_runtime.journal", "offerpilot.agent_runtime.trace",
    "offerpilot.ai.tool_runtime.pipeline", "offerpilot.ai.tool_runtime.legacy_proof",
    "sqlalchemy.dialects.sqlite", "sqlalchemy.dialects.sqlite.pysqlite",
    "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl", "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
]
hidden += collect_submodules("tiktoken_ext")
# Compiled charset_normalizer 3.4.x modules dynamically import their helpers.
hidden += collect_submodules("charset_normalizer")
# Recent wheels put a hashed mypyc support extension at site-packages root,
# outside the package namespace. Read its wheel manifest rather than hardcode
# an OS/Python-specific hash or include unrelated developer mypy binaries.
for entry in distribution("charset-normalizer").files or []:
    if "__mypyc" in entry.name and entry.name.endswith((".so", ".pyd")):
        hidden.append(entry.name.split(".", 1)[0])
hidden += collect_submodules("litellm", filter=lambda name: not name.startswith("litellm.proxy"))

analysis = Analysis(
    [str(ROOT / "desktop" / "backend_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=data,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff", "tkinter"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
exe = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name="offerpilot-backend", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True,
)
COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="offerpilot-backend")
