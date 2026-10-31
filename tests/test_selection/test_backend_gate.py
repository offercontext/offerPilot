from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from scripts.test_selection import backend_gate
from scripts.test_selection.core import SelectionError
from .test_selection import miniature


def test_backend_partition_preserves_every_node_and_keeps_files_together():
    catalog = [dict(id=f'tests/test_{file}.py::test_{number}', file=f'tests/test_{file}.py')
               for file, count in [('a', 3), ('b', 2), ('c', 6)] for number in range(count)]
    chunks = backend_gate.partition(catalog, 4)
    assert [len(ids) for ids in chunks] == [3, 2, 6]
    assert {node for ids in chunks for node in ids} == {row['id'] for row in catalog}


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'extra', 'failed', 'teardown', 'boolean_exit'])
def test_backend_zero_exit_cannot_hide_missing_or_failed_execution(mutation):
    report = dict(exit_code=0, reports=[dict(id='tests/test_a.py::test_a', when='call', outcome='passed')])
    if mutation == 'missing':
        report['reports'] = []
    elif mutation == 'duplicate':
        report['reports'] *= 2
    elif mutation == 'extra':
        report['reports'][0]['id'] = 'tests/test_a.py::test_other'
    elif mutation == 'failed':
        report['reports'][0]['outcome'] = 'failed'
    elif mutation == 'teardown':
        report['reports'].append(dict(id='tests/test_a.py::test_a', when='teardown', outcome='failed'))
    else:
        report['exit_code'] = False
    with pytest.raises(SelectionError):
        backend_gate.reconcile(report, ['tests/test_a.py::test_a'])


def test_backend_setup_skip_is_explicitly_counted_without_becoming_a_pass():
    report = dict(exit_code=0, reports=[dict(id='tests/test_a.py::test_a', when='setup',
                                          outcome='skipped', reason='platform requirement')])
    assert backend_gate.reconcile(report, ['tests/test_a.py::test_a']) == dict(passed=0, skipped=1)


def test_real_isolated_backend_run_verifies_full_coverage_and_refuses_tampered_report(tmp_path: Path):
    miniature(tmp_path)
    (tmp_path / 'tests/test_second.py').write_text('def test_second():\n    assert True\n', encoding='utf-8')
    output = tmp_path / 'artifacts/backend-full'
    result = backend_gate.run(tmp_path, output, max_tests=1)
    assert result['exit_code'] == 0 and result['passed'] is True
    assert result['counts'] == dict(passed=2, skipped=0)
    assert len(result['groups']) == 2
    assert backend_gate.verify(tmp_path, output)['passed'] is True
    for name in ('group-001.report.json', 'collection-native.json', 'collect.stdout.log', 'group-002.stdout.log'):
        evidence = output / name
        original = evidence.read_bytes()
        evidence.write_bytes(b'{}')
        with pytest.raises(SelectionError):
            backend_gate.verify(tmp_path, output)
        evidence.write_bytes(original)
    with pytest.raises(FileExistsError):
        backend_gate.run(tmp_path, output, max_tests=1)


def test_real_backend_failure_stops_remaining_groups_and_never_verifies(tmp_path: Path):
    miniature(tmp_path)
    (tmp_path / 'tests/test_a.py').write_text('def test_failure():\n    assert False\n', encoding='utf-8')
    output = tmp_path / 'artifacts/backend-failed'
    result = backend_gate.run(tmp_path, output, max_tests=1)
    assert result['exit_code'] == 1 and result['passed'] is False
    assert not (output / 'group-002.report.json').exists()
    with pytest.raises(SelectionError):
        backend_gate.verify(tmp_path, output)


def test_native_zero_without_backend_execution_report_is_incomplete(tmp_path, monkeypatch):
    miniature(tmp_path)
    original = subprocess.run

    def missing_report(argv, **kwargs):
        if '--selection-report' in argv:
            return subprocess.CompletedProcess(argv, 0)
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, 'run', missing_report)
    output = tmp_path / 'artifacts/no-report'
    result = backend_gate.run(tmp_path, output)
    assert result['passed'] is False and result['exit_code'] == 2
    with pytest.raises(SelectionError):
        backend_gate.verify(tmp_path, output)


def test_full_backend_refuses_inherited_pytest_filters_before_any_execution(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k reduced_subset')
    output = tmp_path / 'not-created'
    with pytest.raises(SelectionError, match='PYTEST_ADDOPTS'):
        backend_gate.run(tmp_path, output)
    assert not output.exists()
