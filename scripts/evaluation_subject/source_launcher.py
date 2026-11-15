"""Load bound subject/evaluator source freshly while retaining dependency caches.

Start this file directly with -I -B and a never-created initial pycache prefix.
The loader is installed before either checkout is imported. It does not compile
or write dependency caches; environment preparation is a separate operation.
"""
from __future__ import annotations

import hashlib
from importlib.abc import SourceLoader
from importlib.machinery import SourceFileLoader, SourcelessFileLoader
import json
import os
from pathlib import Path
import runpy
import sys
from types import CodeType
from typing import Any

POLICY = "bound-source-loader-v1"
_roots: tuple[Path, ...] = ()
_initial_prefix: str | None = None
_before_project_imports = False


def _protected(filename: str) -> bool:
    # A loader path inside a bound tree remains protected when a symlink points
    # outside it; an external alias into a bound tree is protected as well.
    lexical = Path(os.path.abspath(filename))
    if any(lexical.is_relative_to(root) for root in _roots):
        return True
    resolved = lexical.resolve(strict=True)
    return any(resolved.is_relative_to(root) for root in _roots)


def _fresh_code(self: SourceLoader, fullname: str) -> CodeType | None:
    filename = self.get_filename(fullname)
    if _protected(filename):
        return self.source_to_code(self.get_data(filename), filename)
    return _native_get_code(self, fullname)


_native_get_code = SourceFileLoader.get_code
_native_sourceless_get_code = SourcelessFileLoader.get_code


def _sourceless_code(self: SourcelessFileLoader, fullname: str) -> CodeType | None:
    if _protected(self.path):
        raise ImportError("bound subject/evaluator module requires source")
    return _native_sourceless_get_code(self, fullname)


def policy_proof() -> dict[str, Any]:
    """Fail closed if the loading policy no longer matches the installed guard."""
    if not (
        SourceFileLoader.get_code is _fresh_code
        and SourcelessFileLoader.get_code is _sourceless_code
        and sys.dont_write_bytecode
        and sys.pycache_prefix is None
        and _initial_prefix
        and not Path(_initial_prefix).exists()
        and _before_project_imports
    ):
        raise RuntimeError("bound source loading policy is inactive")
    return dict(
        policy=POLICY,
        protected_roots=[str(root) for root in _roots],
        installed_before_project_imports=_before_project_imports,
        guard_active=True,
        sourceless_bound_modules_blocked=True,
        initial_empty_prefix=_initial_prefix,
        dependency_cache_prefix=None,
        bytecode_writes_disabled=True,
        launcher_file=str(Path(__file__).resolve()),
        launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )


def main() -> None:
    global _roots, _initial_prefix, _before_project_imports
    args = json.loads(sys.argv[1])
    checkout = Path(args["checkout"]).resolve(strict=True)
    evaluator = Path(args["evaluator"]).resolve(strict=True)
    bridge = Path(args["bridge"]).resolve(strict=True)
    if evaluator != Path(__file__).resolve().parents[2] or bridge.suffix != ".py":
        raise ValueError("source launcher/evaluator identity differs")
    _initial_prefix = sys.pycache_prefix
    if not (sys.flags.isolated and sys.dont_write_bytecode and _initial_prefix
            and Path(_initial_prefix).is_absolute() and not Path(_initial_prefix).exists()):
        raise ValueError("source launcher requires isolated bytecode-disabled fresh startup")
    _roots = tuple(sorted({(checkout / "src").resolve(strict=True),
                           (evaluator / "src").resolve(strict=True),
                           (evaluator / "scripts").resolve(strict=True)}))
    _before_project_imports = not any(
        name == "offerpilot" or name.startswith("offerpilot.")
        or name == "scripts" or name.startswith("scripts.")
        for name in sys.modules
    )
    if not _before_project_imports:
        raise ValueError("project imported before source guard")
    SourceFileLoader.get_code = _fresh_code  # type: ignore[method-assign,assignment]
    SourcelessFileLoader.get_code = _sourceless_code  # type: ignore[method-assign]
    # Only the bound source trees bypass valid pyc reads. Python validates its
    # normal third-party/stdlib caches; -B continues to prohibit all new writes.
    sys.pycache_prefix = None
    sys.modules["fixed_evaluation_source_launcher"] = sys.modules[__name__]
    policy_proof()
    sys.path.insert(0, str(checkout / "src"))
    sys.path.insert(1, str(evaluator))
    sys.argv = [str(bridge)]
    runpy.run_path(str(bridge), run_name="__main__")


if __name__ == "__main__":
    main()
