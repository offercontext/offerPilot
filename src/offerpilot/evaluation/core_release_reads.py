"""Reuse strict reads within one command, guarded by their complete input closure.

Discovery is deliberately schema-specific. Trial content and credential_ref are
opaque; they cannot introduce a file read. The original readers still decide
whether evidence is valid, including all hashes, authority and verdicts.
"""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Iterator, TypeVar

from offerpilot.evaluation.contracts import VariantConfig
from offerpilot.evaluation.core_contracts import RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_origin_reads import OriginReads
from offerpilot.evaluation.core_targeted_account import safe_path
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.runner import ROOT, git, target_environment

_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


def _merge(destination: dict[_Key, _Value], values: dict[_Key, _Value]) -> None:
    for key, value in values.items():
        if key in destination and destination[key] != value:
            raise ValueError("release dependency expectations changed between reads")
        destination[key] = value


def _regular(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    ):
        raise ValueError("linked release evidence input")


def _bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    safe_path(path)
    before = path.lstat()
    _regular(before)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("release evidence file unavailable")
    raw = path.read_bytes()
    after = path.lstat()
    _regular(after)
    if not stat.S_ISREG(after.st_mode):
        raise ValueError("release evidence file changed during read")
    safe_path(path)
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev, after.st_ino, after.st_size
    ) or len(raw) != after.st_size:
        raise ValueError("release evidence file changed during read")
    return raw, dict(kind="file", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def _tree(root: Path) -> dict[str, dict[str, Any]]:
    safe_path(root)
    values: dict[str, dict[str, Any]] = {}
    files = []
    directories = []
    def visit(path: Path) -> None:
        key = path.relative_to(root).as_posix()
        try:
            info = path.lstat()
        except FileNotFoundError:
            values[key] = dict(kind="missing")
            return
        _regular(info)
        if stat.S_ISDIR(info.st_mode):
            values[key] = dict(kind="directory")
            with os.scandir(path) as entries:
                names = sorted(entry.name for entry in entries)
            directories.append((path, info, names))
            for name in names:
                visit(path / name)
        elif stat.S_ISREG(info.st_mode):
            files.append((key, path))
        else:
            raise ValueError("release evidence node unavailable")
    visit(root)
    def hash_file(item: tuple[str, Path]) -> tuple[str, dict[str, Any]]:
        key, path = item
        before = path.lstat()
        _regular(before)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("release evidence file changed during read")
        digest = hashlib.sha256()
        size = 0
        chunk_size = min(max(before.st_size, 1), 1024 * 1024)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(chunk_size), b""):
                size += len(chunk)
                digest.update(chunk)
        after = path.lstat()
        _regular(after)
        if not stat.S_ISREG(after.st_mode):
            raise ValueError("release evidence file changed during read")
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev, after.st_ino, after.st_size
        ) or size != after.st_size:
            raise ValueError("release evidence file changed during read")
        return key, dict(kind="file", bytes=size, sha256=digest.hexdigest())
    # Bounded parallel reads avoid serial per-file I/O latency. Every byte is
    # hashed on each check; no stat/mtime cache and no unbounded worker creation.
    with ThreadPoolExecutor(max_workers=2) as pool:
        values.update(pool.map(hash_file, files))
    for path, before, names in reversed(directories):
        after = path.lstat()
        _regular(after)
        if not stat.S_ISDIR(after.st_mode):
            raise ValueError("release evidence directory changed during read")
        with os.scandir(path) as entries:
            current_names = sorted(entry.name for entry in entries)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or current_names != names:
            raise ValueError("release evidence directory changed during read")
    safe_path(root)
    return values


def _executable(interpreter: Path) -> dict[str, Any]:
    safe_path(interpreter.parent)
    # A normal POSIX venv may initially use a symlink for its executable. Pin its
    # invocation spelling, link target, resolved file and bytes; reject changes.
    info = interpreter.lstat()
    return dict(invocation=str(interpreter), link=(os.readlink(interpreter)
        if stat.S_ISLNK(info.st_mode) else None), resolved=str(interpreter.resolve()),
        file=_bytes(safe_path(interpreter.resolve()))[1])


def _source_names(checkout: Path) -> set[bytes]:
    return set(git(checkout, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")) - {b""}


def _guard_snapshot(checkout: Path) -> dict[str, Any]:
    """The runner.snapshot fingerprint, with bounded parallel byte reads.

    The original strict/historical snapshot function is unchanged. Equivalence
    tests include tracked-ignored, untracked, binary and missing source entries.
    """
    names = sorted(_source_names(checkout))
    def entry(raw: bytes) -> tuple[str, str]:
        name = os.fsdecode(raw).replace("\\", "/")
        path = checkout / name
        try:
            before = path.lstat()
        except FileNotFoundError:
            return name, "missing"
        _regular(before)
        if stat.S_ISDIR(before.st_mode):
            return name, "missing"  # Same gitlink/directory treatment as snapshot.
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("release source node unavailable")
        content_hash = file_hash(path)
        after = path.lstat()
        _regular(after)
        if not stat.S_ISREG(after.st_mode):
            raise ValueError("release source file changed during byte sampling")
        if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise ValueError("release source file changed during byte sampling")
        return name, content_hash
    with ThreadPoolExecutor(max_workers=2) as pool:
        files = dict(pool.map(entry, names))
    return dict(commit=git(checkout, "rev-parse", "HEAD").decode().strip(),
        diff_hash=digest([git(checkout, "diff", "--binary", "HEAD").hex(), files]),
        lock_hash=file_hash(checkout / "uv.lock"), files_hash=digest(files))


def _source_nodes(checkout: Path) -> dict[str, str]:
    safe_path(checkout)
    # Snapshot preserves HEAD, dirty diff and source bytes. Node/ancestor checks
    # additionally reject a same-content symlink or Windows junction replacement.
    names = _source_names(checkout)
    paths = set()
    for raw in names - {b""}:
        path = checkout / os.fsdecode(raw)
        paths.add(path)
        paths.update(p for p in path.parents if p.is_relative_to(checkout))
    nodes = {}
    for path in paths:
        try:
            info = path.lstat()
            _regular(info)
            if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
                raise ValueError("release source node unavailable")
            nodes[str(path)] = "file" if stat.S_ISREG(info.st_mode) else "directory"
        except FileNotFoundError:
            nodes[str(path)] = "missing"
    safe_path(checkout)
    return nodes


def _source(target: dict[str, Any]) -> dict[str, Any]:
    checkout = safe_path(Path(target["checkout"]).absolute())
    interpreter = Path(target["interpreter"]).absolute()
    executable, nodes = _executable(interpreter), _source_nodes(checkout)
    variant = VariantConfig(variant_id="candidate", checkout=str(checkout), interpreter=str(interpreter))
    with ThreadPoolExecutor(max_workers=2) as pool:
        source = pool.submit(_guard_snapshot, checkout)
        runtime = pool.submit(target_environment, variant)
        observed = dict(snapshot=source.result(), interpreter=executable, runtime=runtime.result())
    # Check after both futures, not merely before their reads. A persistent
    # same-content link replacement must not disappear inside snapshot's hash.
    if _source_nodes(checkout) != nodes or _executable(interpreter) != executable:
        raise ValueError("release source nodes/runtime invocation changed during read")
    observed["nodes"] = nodes
    return observed


def _pin_source(target: dict[str, Any]) -> dict[str, Any]:
    """Pure file reads before trust; never invoke a manifest-selected program."""
    return dict(snapshot={k: target[k] for k in ("commit", "diff_hash", "files_hash", "lock_hash")},
        runtime=deepcopy(target["runtime"]),
        interpreter=_executable(Path(target["interpreter"]).absolute()))


class DependencyClosure:
    """A byte/topology freeze, never a replacement for a strict evidence reader."""

    def __init__(self) -> None:
        self.files: dict[Path, dict[str, Any]] = {}
        self.trees: dict[Path, dict[str, dict[str, Any]]] = {}
        self.sources: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
        self._jsons: dict[Path, dict[str, Any]] = {}
        self._tree_roots: set[Path] = set()
        self._source_targets: dict[tuple[str, str], dict[str, Any]] = {}
        self._runtime_active = False
        self.paths: dict[Path, str] = {}
        self.resolves: dict[Path, dict[str, str]] = {}

    def file(self, path: Path) -> Path:
        path = path.absolute()
        if path in self.files:
            return path  # Discovery only; freeze/check still reread every byte.
        path = safe_path(path)
        _, state = _bytes(path)
        self._remember(path, state)
        return path

    def _remember(self, path: Path, state: dict[str, Any]) -> None:
        if path in self.files and self.files[path] != state:
            raise ValueError("release dependency changed during discovery")
        for root, entries in self.trees.items():
            if path.is_relative_to(root) and entries.get(path.relative_to(root).as_posix()) != state:
                raise ValueError("release dependency changed during discovery")
        self.files[path] = state

    def obj(self, path: Path) -> dict[str, Any]:
        path = safe_path(path.absolute())
        if path not in self._jsons:
            # Hash and discovery use exactly the same bytes, including files
            # already enumerated in a tree; never parse one version and pin another.
            raw, state = _bytes(path)
            self._remember(path, state)
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("release dependency object unavailable")
            self._jsons[path] = value
        return self._jsons[path]

    def path(self, path: Path) -> None:
        path = path.absolute()
        if path in self.paths:
            return  # Same discovery graph; not a later freshness boundary.
        path = safe_path(path)
        for node in [path, *path.parents]:
            state = self._path_kind(node)
            if node in self.paths and self.paths[node] != state:
                raise ValueError("release path changed during discovery")
            self.paths[node] = state

    @staticmethod
    def _resolved_state(path: Path) -> dict[str, str]:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return dict(kind="missing", resolved=str(path.resolve()))
        if stat.S_ISLNK(info.st_mode):
            return dict(kind="symlink", target=os.readlink(path), resolved=str(path.resolve()))
        _regular(info)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("release interpreter path unavailable")
        return dict(kind="file", resolved=str(path.resolve()))

    def resolve_path(self, path: Path) -> None:
        path = path.absolute()
        if path in self.resolves:
            return  # Complete remeasurement remains in freeze and every check.
        self.path(path.parent)
        state = self._resolved_state(path)
        self.path(Path(state["resolved"]))
        if path in self.resolves and self.resolves[path] != state:
            raise ValueError("release resolution changed during discovery")
        self.resolves[path] = state

    def _check_paths(self) -> None:
        # Every ancestor is already in this deduplicated set; checking each node
        # directly avoids resolving the same long ancestry for every leaf.
        for path, expected_kind in sorted(self.paths.items(), key=lambda item: len(item[0].parts)):
            if self._path_kind(path) != expected_kind:
                raise ValueError("release dependency path topology changed")
        for path, expected_resolution in self.resolves.items():
            if self._resolved_state(path) != expected_resolution:
                raise ValueError("release interpreter resolution changed")

    @staticmethod
    def _path_kind(path: Path) -> str:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return "missing"
        _regular(info)
        if stat.S_ISREG(info.st_mode):
            return "file"
        if stat.S_ISDIR(info.st_mode):
            return "directory"
        raise ValueError("release dependency path unavailable")

    def tree(self, directory: Path) -> None:
        directory = safe_path(directory.absolute())
        self._tree_roots.add(directory)

    def source(self, target: dict[str, Any]) -> None:
        key = (target["checkout"], target["interpreter"])
        self._source_targets[key] = deepcopy(target)

    def _sample(self) -> tuple[dict[Path, Any], dict[tuple[str, str], Any]]:
        # Independent trees/source measurements share one bounded scheduling
        # interval. Their members and bytes are still read afresh on every call.
        with ThreadPoolExecutor(max_workers=4) as pool:
            trees = {root: pool.submit(_tree, root) for root in self._tree_roots}
            sources = {key: pool.submit(_source if self._runtime_active else _pin_source, target)
                for key, target in self._source_targets.items()}
            return ({root: future.result() for root, future in trees.items()},
                {key: future.result() for key, future in sources.items()})

    def freeze(self) -> DependencyClosure:
        trees, sources = self._sample()
        if any(trees[root] != state for root, state in self.trees.items()):
            raise ValueError("release dependency membership changed during discovery")
        self.trees = trees
        self.sources = {key: (self._source_targets[key], state) for key, state in sources.items()}
        for path, state in self.files.items():
            covered = False
            for root, entries in self.trees.items():
                if path.is_relative_to(root):
                    covered = True
                    if entries.get(path.relative_to(root).as_posix()) != state:
                        raise ValueError("release dependency changed during discovery")
            if not covered and _bytes(path)[1] != state:
                raise ValueError("release dependency changed during discovery")
        self._check_paths()
        return self

    def _bound(self, value: dict[str, Any]) -> Path:
        # Semantic hash checking remains with bound_file/the strict reader.
        return self.file(Path(value["path"]))

    def _authority(self, value: dict[str, Any], *, original: bool = False) -> None:
        if "record_ref" not in value:
            return
        path = safe_path(Path(value["record_ref"]).absolute())
        raw, state = _bytes(path)
        self._remember(path, state)
        # Review references may be plain text. Only the old promotion authority
        # has a supported JSON contract with a further request_ref dependency.
        if not original:
            return
        try:
            authority = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return  # Ordinary PromotionApproval references may be plain text.
        if isinstance(authority, dict) and authority.get("kind") == "explicit-human-maintainer-approval-record":
            request = self.obj(Path(authority["request_ref"]))
            for ref in request["evidence"].values():
                self._bound(ref)

    def review(self, bundle: dict[str, Any]) -> None:
        for field in ("independent_reviews", "adjudications", "exceptions"):
            for item in bundle.get(field, []):
                self._authority(item)

    def _gate(self, path: Path) -> None:
        self.review(self.obj(path).get("frozen_review", {}))

    def _original(self, record: dict[str, Any]) -> None:
        directories = {Path(record["run_directory"])} | {
            Path(source["directory"]) for source in record["sources"]}
        for directory in directories:
            self.tree(directory)
            self._origins(directory, source_paths=False)
        self._gate(Path(record["gate_path"]))
        self._authority(record.get("approval", {}), original=True)
        manifest = self.obj(Path(record["run_directory"]) / "run-manifest.json")
        # The historical reader measures this primary candidate's live source
        # and runtime; its other old comparison arms are read as sealed evidence.
        if "candidate" in manifest["variants"]:
            self.source(manifest["variants"]["candidate"])

    def _request(self, envelope: dict[str, Any]) -> None:
        value = envelope["request"]
        self._bound(value["original_record"])
        self.tree(Path(value["run_directory"]))
        for ref in value["raw_files"].values():
            self._bound(ref)
        for field in ("policy", "experiment", "measurement", "historical_proof"):
            self._bound(value[field])
        self.review(self.obj(self._bound(value["review"])))
        self._gate(self._bound(value["gate"]))
        history = self.obj(self._bound(value["history_descriptor"]))
        for field in ("native_receipt", "approval_file"):
            self._bound(history[field])
        self._origins(Path(value["run_directory"]))

    def _origins(self, directory: Path, *, source_paths: bool = True) -> None:
        # These are the exact live Path.resolve dependencies in source_origins.
        # Only supported manifest/results/facts fields are traversed; never scan
        # arbitrary answer content for strings resembling paths or references.
        manifest = self.obj(directory / "run-manifest.json")
        for target in manifest["variants"].values():
            if source_paths:
                self.path(Path(target["checkout"]))
            self.resolve_path(Path(target["interpreter"]))
        result_path = safe_path((directory / "trial-results.json").absolute())
        raw, state = _bytes(result_path)
        self._remember(result_path, state)
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("release result list unavailable")
        for row in rows:
            if row["execution_status"] == "not_run":
                continue
            private = safe_path(Path(row["private_directory"]).absolute())
            if not private.is_relative_to((directory / "private").absolute()):
                raise ValueError("release private dependency path unavailable")
            try:
                facts = self.obj(private / "fragment-facts.json")["value"]
            except (OSError, ValueError, KeyError):
                # The strict reader retains its original missing/corrupt verdict.
                # The complete tree pins absent nodes or malformed raw bytes.
                continue
            if not isinstance(facts, dict):
                continue
            for handshake in facts.get("handshakes", []):
                if "interpreter" in handshake:
                    self.resolve_path(Path(handshake["interpreter"]))
                if source_paths:
                    for field in ("product_file", "product_factory_file", "evaluator_file"):
                        if field in handshake:
                            self.path(Path(handshake[field]))
                    loading = handshake.get("source_loading", {})
                    for field in ("launcher_file", "initial_empty_prefix"):
                        if field in loading:
                            self.path(Path(loading[field]))

    @classmethod
    def for_preflight(cls, path: Path) -> DependencyClosure:
        closure = cls()
        value = closure.obj(path)["value"]
        closure.path(ROOT)
        for directory in (ROOT / "src/offerpilot/evaluation", ROOT / "scripts/evaluation_subject", ROOT / "evals"):
            closure.tree(directory)
        for name, ref in value["inputs"].items():
            bound = closure.file(Path(ref["path"]))
            if name == "review":
                closure.review(closure.obj(bound))
        # provider/grant descriptors are byte-bound; credential_ref is opaque.
        return closure.freeze()

    @classmethod
    def for_baseline(cls, store: Path, baseline_id: str) -> DependencyClosure:
        closure = cls()
        store = safe_path(store.absolute())
        closure.tree(store)
        # The store's member set selects which generation/request/authority
        # schemas are traversed. Pin it before discovery so a new generation
        # cannot be accepted by the strict resolver with an undiscovered closure.
        closure.trees[store] = _tree(store)
        closure._original(closure.obj(store / "records" / (baseline_id + ".json")))
        head_path = store / "evidence-generations" / (baseline_id + ".json")
        head = closure.obj(head_path).get("generation_id") if head_path.exists() else None
        generations = store / "evaluator-refreshes"
        for path in sorted(generations.glob("*.json")):
            generation = closure.obj(path)
            request = closure._bound(generation["request"])
            approval = closure.obj(closure._bound(generation["approval_file"]))
            closure._authority(approval)
            closure._authority(generation["approval"])
            if generation["generation_id"] == head:
                closure._request(closure.obj(request))
        # generation_state verifies these source records even for revoked or
        # superseded generations; all event/CAS bytes are already tree-bound.
        for path in sorted((store / "events").glob("*.json")):
            event = closure.obj(path)
            if event.get("action") == "revoke_refresh":
                closure._authority(event["revocation"])
        return closure.freeze()

    @classmethod
    def for_run(cls, directory: Path) -> DependencyClosure:
        closure = cls()
        closure.tree(directory)
        # read_run consumes sealed evidence, not live subject programs. Subject
        # runtime declarations/observed handshakes remain strict-reader inputs.
        closure._origins(directory, source_paths=False)
        return closure.freeze()

    def activate_sources(self) -> None:
        """Only call after the original strict resolver has returned successfully."""
        self._runtime_active = True

    def check(self) -> None:
        self._check_paths()
        trees, sources = self._sample()
        for root, expected in self.trees.items():
            if trees[root] != expected:
                raise ValueError("release evidence directory changed after verification")
        for path, expected in self.files.items():
            covered = [entries.get(path.relative_to(root).as_posix())
                for root, entries in trees.items() if path.is_relative_to(root)]
            if (any(state != expected for state in covered) if covered else _bytes(path)[1] != expected):
                raise ValueError("release evidence file changed after verification")
        for key, (_, expected) in self.sources.items():
            if any(sources[key].get(field) != state for field, state in expected.items()):
                raise ValueError("release source/runtime changed after verification")
        # The first post-trust sample must match the pre-pinned declarations and
        # executable bytes before it can add the measured node topology.
        for key, observed in sources.items():
            self.sources[key] = (self._source_targets[key], observed)
        self._check_paths()

    @classmethod
    def union(cls, closures: list[DependencyClosure]) -> DependencyClosure:
        result = cls()
        for closure in closures:
            _merge(result.files, closure.files)
            _merge(result.trees, closure.trees)
            _merge(result.sources, closure.sources)
            _merge(result.paths, closure.paths)
            _merge(result.resolves, closure.resolves)
            result._source_targets.update(closure._source_targets)
            if closure.sources:
                if not closure._runtime_active:
                    raise ValueError("release source validation not activated")
                result._runtime_active = True
        # A parent tree already guards every member in a nested tree. Compare
        # expected subtrees before removing overlap; conflicting freezes fail.
        for child in sorted(result.trees, key=lambda p: len(p.parts), reverse=True):
            parents = [root for root in result.trees if child != root and child.is_relative_to(root)]
            if parents:
                parent = max(parents, key=lambda p: len(p.parts))
                relative = child.relative_to(parent)
                expected = {Path(name).relative_to(relative).as_posix(): value
                    for name, value in result.trees[parent].items()
                    if Path(name).is_relative_to(relative)}
                if result.trees[child] != expected:
                    raise ValueError("release nested dependency expectations changed")
                del result.trees[child]
        result._tree_roots = set(result.trees)
        return result

    def metrics(self) -> dict[str, int]:
        entries = dict(self.files)
        for root, values in self.trees.items():
            entries.update({root / name: value for name, value in values.items()})
        return dict(trees=len(self.trees), sources=len(self.sources), nodes=len(entries),
            files=sum(v["kind"] == "file" for v in entries.values()),
            bytes=sum(v.get("bytes", 0) for v in entries.values()))


class CommandReads:
    """Explicit per-call-stack state. No global, persistent or caller-supplied cache."""

    def __init__(self) -> None:
        self._baselines: dict[tuple[Path, str], tuple[DependencyClosure, Any]] = {}
        self._runs: dict[Path, tuple[DependencyClosure, Any]] = {}
        self._inputs: list[DependencyClosure] = []
        self._pending_runs: dict[Path, tuple[RunManifestV2, list[TrialResultV2]]] = {}
        self._collecting = False
        self._origin_baseline: DependencyClosure | None = None
        self._origin_seen: set[Path] = set()
        self._origin_scopes: list[OriginReads] = []

    @contextmanager
    def origin_scope(self, directory: Path) -> Iterator[OriginReads]:
        """Only the current pre-frozen baseline collection can issue a scope."""
        key = safe_path(directory.absolute())
        closure = self._origin_baseline
        if (not self._collecting or closure is None or key not in closure.trees
                or key in self._origin_seen):
            raise ValueError("duplicate or out-of-scope origin qualification")
        frozen = closure.trees[key]
        values = {}
        for name in ("run-manifest.json", "trial-results.json"):
            raw, state = _bytes(key / name)
            if frozen.get(name) != state:
                raise ValueError("origin input differs from pre-frozen run bytes")
            values[name] = json.loads(raw)
        manifest = RunManifestV2.model_validate(values["run-manifest.json"])
        rows = [TrialResultV2.model_validate(row) for row in values["trial-results.json"]]
        scope = OriginReads(key, manifest, rows,
            _active=lambda: self._collecting and self._origin_baseline is closure)
        self._origin_seen.add(key)
        self._origin_scopes.append(scope)
        try:
            yield scope
        finally:
            scope.close()

    def _close_origins(self) -> None:
        for scope in self._origin_scopes:
            scope.close()
        self._origin_scopes.clear()
        self._origin_seen.clear()
        self._origin_baseline = None

    def inputs(self, path: Path) -> None:
        self._inputs.append(DependencyClosure.for_preflight(path))

    def receipt(self, path: Path) -> tuple[dict[str, Any], str]:
        closure = DependencyClosure()
        value = closure.obj(path)
        closure.freeze()
        self._inputs.append(closure)
        return deepcopy(value), closure.files[path.absolute()]["sha256"]

    def collect_qualified_run(
        self, directory: Path, manifest: RunManifestV2, rows: list[TrialResultV2]
    ) -> None:
        key = safe_path(directory.absolute())
        if not self._collecting or key in self._pending_runs or key in self._runs:
            raise ValueError("duplicate or out-of-scope qualified run capture")
        self._pending_runs[key] = (manifest.model_copy(deep=True), [row.model_copy(deep=True) for row in rows])

    def _qualified(self, value: Any, closure: DependencyClosure) -> dict[Path, tuple[DependencyClosure, Any]]:
        allowed = {safe_path(Path(source["directory"]).absolute()): source["manifest_hash"]
            for source in value["sources"]} if self._pending_runs else {}
        adopted: dict[Path, tuple[DependencyClosure, Any]] = {}
        for directory, pair in self._pending_runs.items():
            if directory not in closure.trees or directory not in allowed or directory in self._runs:
                raise ValueError("qualified run outside frozen baseline sources")
            if digest(pair[0].model_dump(mode="json")) != allowed[directory]:
                raise ValueError("qualified run manifest differs from resolved source")
            raw_manifest = RunManifestV2.model_validate(closure.obj(directory / "run-manifest.json"))
            if digest(raw_manifest.model_dump(mode="json")) != allowed[directory]:
                raise ValueError("qualified run differs from frozen manifest bytes")
            run_closure = DependencyClosure()
            run_closure.trees[directory] = deepcopy(closure.trees[directory])
            run_closure._tree_roots.add(directory)
            run_closure.paths = deepcopy(closure.paths)
            run_closure.resolves = deepcopy(closure.resolves)
            adopted[directory] = (run_closure, deepcopy(pair))
        return adopted

    @staticmethod
    def _run_closure(directory: Path, baselines: list[DependencyClosure]) -> DependencyClosure:
        key = safe_path(directory.absolute())
        prior = [closure.trees[key] for closure in baselines if key in closure.trees]
        if not prior:
            return DependencyClosure.for_run(directory)
        if any(value != prior[0] for value in prior):
            raise ValueError("release run dependency expectations changed")
        closure = DependencyClosure()
        closure.trees[key] = deepcopy(prior[0])
        closure._tree_roots.add(key)
        closure._origins(key, source_paths=False)
        return closure

    def baseline_and_runs(
        self, store: Path, baseline_id: str, baseline_reader: Callable[..., Any],
        requested_dirs: list[Path], run_reader: Callable[..., Any],
    ) -> tuple[Any, dict[Path, Any]]:
        """One cold read-only batch; no trial may execute before this returns."""
        if self._baselines or self._runs or self._collecting:
            raise ValueError("read-only batch requires a new command context")
        baseline = DependencyClosure.for_baseline(store, baseline_id)
        try:
            self._origin_baseline = baseline
            self._collecting = True
            value = baseline_reader(store, baseline_id)
            self._collecting = False
            staged = self._qualified(value, baseline)
            expected_sources = {safe_path(Path(source["directory"]).absolute()): source["manifest_hash"]
                for source in value["sources"]}
            directories = {safe_path(path.absolute()) for path in requested_dirs} | {
                safe_path(Path(source["directory"]).absolute()) for source in value["sources"]}
            for directory in directories:
                if directory not in staged:
                    closure = self._run_closure(directory, [baseline])
                    parsed = run_reader(directory)  # The unchanged strict reader.
                    if directory in expected_sources and digest(parsed[0].model_dump(mode="json")) != expected_sources[directory]:
                        raise ValueError("baseline run differs from resolved source")
                    staged[directory] = (closure, deepcopy(parsed))
            baseline.activate_sources()  # Only after the complete strict reads.
            union = DependencyClosure.union([baseline, *[lease for lease, _ in staged.values()], *self._inputs])
            union.check()
            # Prepare every deep copy before atomically making any read reusable.
            record = deepcopy(value)
            checked = {directory: deepcopy(staged[directory][1]) for directory in directories}
            adopted = {directory: (lease, deepcopy(parsed)) for directory, (lease, parsed) in staged.items()}
            self._baselines[(store.absolute(), baseline_id)] = (union, deepcopy(record))
            self._runs.update(adopted)
            return record, checked
        finally:
            self._collecting = False
            self._pending_runs.clear()
            self._close_origins()

    def baseline(self, store: Path, baseline_id: str, reader: Callable[..., Any]) -> Any:
        key = (store.absolute(), baseline_id)
        if key not in self._baselines:
            closure = DependencyClosure.for_baseline(store, baseline_id)
            try:
                self._origin_baseline = closure
                self._collecting = True
                value = reader(store, baseline_id)
                self._collecting = False
                closure.activate_sources()
                closure.check()
                adopted = self._qualified(value, closure)
                self._baselines[key] = (closure, deepcopy(value))
                self._runs.update(adopted)
            finally:
                self._collecting = False
                self._pending_runs.clear()
                self._close_origins()
        else:
            self._baselines[key][0].check()
        return deepcopy(self._baselines[key][1])

    def run(self, directory: Path, reader: Callable[..., Any]) -> Any:
        key = directory.absolute()
        if key not in self._runs:
            closure = self._run_closure(directory, [lease for lease, _ in self._baselines.values()])
            value = reader(directory)
            closure.check()
            self._runs[key] = (closure, deepcopy(value))
        else:
            self._runs[key][0].check()
        return deepcopy(self._runs[key][1])

    def check(self) -> None:
        DependencyClosure.union([closure for closure, _ in [
            *self._baselines.values(), *self._runs.values()]] + self._inputs).check()
