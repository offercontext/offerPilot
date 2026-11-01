"""Run every collected backend node in serial processes, preserving file fixtures."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from scripts.test_selection.core import SelectionError, digest, file_hash, runtime_environment, snapshot


def partition(catalog: list[dict[str, Any]], limit: int) -> list[list[str]]:
    if type(limit) is not int or not 1 <= limit <= 1000 or not catalog:
        raise SelectionError('invalid full backend partition')
    files: dict[str, list[str]] = {}
    seen: set[str] = set()
    for row in catalog:
        node, file = row['id'], row['file']
        if (not isinstance(node, str) or not isinstance(file, str) or node in seen
                or not file.startswith('tests/') or node.split('::')[0] != file):
            raise SelectionError('invalid or duplicate full backend node')
        seen.add(node)
        files.setdefault(file, []).append(node)
    groups: list[list[str]] = []
    current: list[str] = []
    for file in sorted(files):
        nodes = files[file]
        if current and len(current) + len(nodes) > limit:
            groups.append(current)
            current = []
        current.extend(nodes)
    if current:
        groups.append(current)
    return groups


def reconcile(report: dict[str, Any], expected: list[str]) -> dict[str, int]:
    if type(report.get('exit_code')) is not int or report['exit_code'] != 0:
        raise SelectionError('backend report did not return native success')
    rows = report.get('reports')
    if not isinstance(rows, list) or any(not isinstance(row, dict)
            or row.get('when') not in {'call', 'setup'} or row.get('outcome') not in {'passed', 'skipped'}
            or (row['when'] == 'setup' and row['outcome'] != 'skipped') for row in rows):
        raise SelectionError('backend execution has failure or invalid phase')
    ids = [row.get('id') for row in rows]
    if Counter(ids) != Counter(expected):
        raise SelectionError('backend execution node coverage differs from frozen full catalog')
    return dict(passed=sum(row['outcome'] == 'passed' for row in rows),
                skipped=sum(row['outcome'] == 'skipped' for row in rows))


def save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def command(ids: list[str], output: Path, name: str) -> list[str]:
    files = list(dict.fromkeys(node.split('::')[0] for node in ids))
    return [sys.executable, '-m', 'pytest', '-q', '-rs', *files,
            '--selection-ids', str(output / (name + '.ids.json')),
            '--selection-report', str(output / (name + '.report.json'))]


def verify(root: Path, output: Path) -> dict[str, Any]:
    plan: dict[str, Any] = read(output / 'backend-plan.json')
    result: dict[str, Any] = read(output / 'backend-gate.json')
    if (os.environ.get('PYTEST_ADDOPTS') or result.get('passed') is not True
            or type(result.get('exit_code')) is not int or result['exit_code'] != 0
            or digest({key: value for key, value in plan.items() if key != 'plan_hash'}) != plan['plan_hash']
            or result['plan_hash'] != plan['plan_hash'] or snapshot(root) != plan['snapshot']
            or runtime_environment(root) != plan['environment']):
        raise SelectionError('full backend receipt or current source/environment differs')
    for name, expected in plan['collection_evidence'].items():
        if name not in {'collection-native.json', 'collect.stdout.log', 'collect.stderr.log'} or file_hash(output / name) != expected:
            raise SelectionError('full backend collection evidence changed')
    if set(plan['collection_evidence']) != {'collection-native.json', 'collect.stdout.log', 'collect.stderr.log'}:
        raise SelectionError('full backend collection proof is incomplete')
    collected = read(output / 'collection-native.json')
    if (type(collected['native_exit']) is not int or collected['native_exit'] != 0
            or collected['command'] != [sys.executable, '-m', 'pytest', 'tests', '--collect-only', '-q',
                                       '--selection-catalog', str(output / 'full-catalog.json')]):
        raise SelectionError('full backend collection invocation differs')
    if file_hash(output / 'full-catalog.json') != plan['catalog_hash']:
        raise SelectionError('full backend catalog changed')
    catalog = read(output / 'full-catalog.json')
    groups = partition(catalog, plan['max_tests'])
    if groups != plan['groups'] or len(groups) != len(result['groups']):
        raise SelectionError('full backend partition changed or is incomplete')
    counts = dict(passed=0, skipped=0)
    for index, ids in enumerate(groups, 1):
        name = f'group-{index:03d}'
        row = result['groups'][index - 1]
        if (row['name'] != name or type(row['native_exit']) is not int or row['native_exit'] != 0
                or row['command'] != command(ids, output, name)
                or read(output / (name + '.ids.json')) != ids):
            raise SelectionError('backend native invocation differs')
        for suffix in ('ids.json', 'report.json', 'stdout.log', 'stderr.log'):
            if file_hash(output / (name + '.' + suffix)) != row['evidence'][suffix]:
                raise SelectionError('backend native evidence changed')
        actual = reconcile(read(output / (name + '.report.json')), ids)
        if row['counts'] != actual:
            raise SelectionError('backend group totals differ')
        for key in counts:
            counts[key] += actual[key]
    if result['counts'] != counts or sum(counts.values()) != len(catalog):
        raise SelectionError('full backend execution denominator differs')
    return result


def run(root: Path, output: Path, max_tests: int = 400) -> dict[str, Any]:
    if os.environ.get('PYTEST_ADDOPTS'):
        raise SelectionError('full isolated gate requires empty PYTEST_ADDOPTS; filters are not permitted')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    before, environment = snapshot(root), runtime_environment(root)
    result: dict[str, Any] = dict(passed=False, exit_code=2, groups=[], counts=dict(passed=0, skipped=0))
    try:
        catalog_path = output / 'full-catalog.json'
        collect = [sys.executable, '-m', 'pytest', 'tests', '--collect-only', '-q',
                   '--selection-catalog', str(catalog_path)]
        with (output / 'collect.stdout.log').open('xb') as stdout, (output / 'collect.stderr.log').open('xb') as stderr:
            native = subprocess.run(collect, cwd=root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, check=False)
        save(output / 'collection-native.json', dict(command=collect, native_exit=native.returncode,
             stdout_hash=file_hash(output / 'collect.stdout.log'), stderr_hash=file_hash(output / 'collect.stderr.log')))
        if native.returncode != 0:
            result['exit_code'] = native.returncode
            raise SelectionError('full backend collection failed')
        groups = partition(read(catalog_path), max_tests)
        plan = dict(schema_version=1, snapshot=before, environment=environment, groups=groups,
                    max_tests=max_tests, catalog_hash=file_hash(catalog_path),
                    collection_evidence={name: file_hash(output / name) for name in
                        ('collection-native.json', 'collect.stdout.log', 'collect.stderr.log')})
        plan['plan_hash'] = digest(plan)
        save(output / 'backend-plan.json', plan)
        result['plan_hash'] = plan['plan_hash']
        for index, ids in enumerate(groups, 1):
            if snapshot(root) != before or runtime_environment(root) != environment:
                raise SelectionError('backend source/environment changed during full gate')
            name = f'group-{index:03d}'
            save(output / (name + '.ids.json'), ids)
            argv = command(ids, output, name)
            print(f'Full backend group {index}/{len(groups)}: {len(ids)} frozen nodes', flush=True)
            with (output / (name + '.stdout.log')).open('xb') as stdout, (output / (name + '.stderr.log')).open('xb') as stderr:
                native = subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, check=False)
            row: dict[str, Any] = dict(name=name, command=argv, native_exit=native.returncode)
            result['groups'].append(row)
            save(output / 'backend-gate.json', result)
            if native.returncode != 0:
                result['exit_code'] = native.returncode
                raise SelectionError('backend group failed; remaining groups were not executed')
            row['counts'] = reconcile(read(output / (name + '.report.json')), ids)
            row['evidence'] = {suffix: file_hash(output / (name + '.' + suffix))
                               for suffix in ('ids.json', 'report.json', 'stdout.log', 'stderr.log')}
            for key in result['counts']:
                result['counts'][key] += row['counts'][key]
            save(output / 'backend-gate.json', result)
            print(f'Full backend completed {sum(result["counts"].values())} nodes: {result["counts"]}', flush=True)
        result.update(passed=True, exit_code=0)
        save(output / 'backend-gate.json', result)
        verify(root, output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['passed'] = False
        if result['exit_code'] == 0:
            result['exit_code'] = 2
        result['reason'] = str(error)
        save(output / 'backend-gate.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        value = verify(root, args.output.resolve()) if args.verify else run(root, args.output)
        if value['passed']:
            print('BACKEND_FULL_VERIFIED ' + file_hash(args.output.resolve() / 'backend-gate.json'), flush=True)
        else:
            print('Full backend gate failed; original evidence: ' + str(args.output.resolve()), file=sys.stderr)
        raise SystemExit(value['exit_code'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
