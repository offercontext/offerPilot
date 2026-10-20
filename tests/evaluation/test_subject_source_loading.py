"""A valid dependency cache may be used; bound product source must never use stale pyc."""
import os
import json
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import time

import pytest
from offerpilot.evaluation.contracts import VariantConfig
from offerpilot.evaluation import runner
from scripts.evaluation_subject.process_tree import ProcessTree


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_subject_uses_valid_dependency_cache_but_loads_bound_source(tmp_path, monkeypatch):
    checkout = tmp_path / 'product'
    source = checkout / 'src'
    source.mkdir(parents=True)
    product = source / 'source_policy_product.py'
    product.write_bytes(b'VALUE = 41\n')
    stamp = product.stat()
    py_compile.compile(str(product), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    product.write_bytes(b'VALUE = 42\n')
    os.utime(product, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    dependency_root = tmp_path / 'dependencies'
    dependency_root.mkdir()
    dependency = dependency_root / 'source_policy_dependency.py'
    dependency.write_text('VALUE = 19\n', encoding='utf-8')
    py_compile.compile(str(dependency), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    bridge = tmp_path / 'bridge.py'
    bridge.write_text('''import sys,json
from importlib.machinery import SourceFileLoader
config=json.loads(sys.stdin.readline())
key=dict(schema_version=1,trial_key=config['trial_key'],epoch=config['epoch'])
seq=0
def emit(kind,**payload):
 global seq
 seq+=1
 print(json.dumps(key|dict(seq=seq,type=kind,**payload)),flush=True)
try:
 compiled=[]
 native=SourceFileLoader.source_to_code
 def observed(loader,data,path,*,_optimize=-1):
  if path.endswith('source_policy_dependency.py'):
   raise AssertionError('valid dependency cache was discarded')
  compiled.append(path)
  return native(loader,data,path,_optimize=_optimize)
 SourceFileLoader.source_to_code=observed
 sys.path.insert(0,config['dependencies'])
 sys.path.insert(0,config['checkout']+'/src')
 import source_policy_dependency,source_policy_product
 emit('ready',product_value=source_policy_product.VALUE,
      dependency_value=source_policy_dependency.VALUE,
      product_source_compiled=any(p.endswith('source_policy_product.py') for p in compiled))
 for line in sys.stdin:
  if json.loads(line).get('command')=='request_shutdown':
   emit('shutdown_complete')
   break
except BaseException as exc:
 emit('error',error_type=type(exc).__name__,message=str(exc))
''', encoding='utf-8')
    protected_before = tree_bytes(checkout), tree_bytes(dependency_root)
    monkeypatch.setattr(runner, 'BRIDGE', bridge)
    monkeypatch.setattr(runner, 'process_tree', ProcessTree)
    private = tmp_path / 'private'
    private.mkdir()
    config = dict(epoch=1, checkout=str(checkout), dependencies=str(dependency_root),
                  trial_key=dict(run_id='cache-regression', execution_item_id='E06.legacy_saved'),
                  limits=dict(shutdown_seconds=5, model_calls=0, tool_attempts=0))
    subject = runner.Subject(VariantConfig(variant_id='candidate', checkout=str(checkout),
                                          interpreter=sys.executable),
                             config, private, [], time.monotonic()+30)
    try:
        ready = subject.wait('ready', 10)
        assert ready['product_value'] == 42
        assert ready['dependency_value'] == 19 and ready['product_source_compiled']
        subject.stop()
        assert not subject.errors and subject.owned_tree_exit_proven
    finally:
        subject.stop(crash=True)
    assert (tree_bytes(checkout), tree_bytes(dependency_root)) == protected_before


def copied_launcher(tmp_path):
    evaluator = tmp_path / 'evaluator'
    scripts = evaluator / 'scripts/evaluation_subject'
    scripts.mkdir(parents=True)
    (evaluator / 'src').mkdir()
    launcher = scripts / 'source_launcher.py'
    shutil.copyfile(runner.ROOT / 'scripts/evaluation_subject/source_launcher.py', launcher)
    checkout = tmp_path / 'product'
    (checkout / 'src').mkdir(parents=True)
    bridge = scripts / 'probe.py'
    env = runner.process_environment(checkout, tmp_path / 'private')
    argv = [sys.executable, '-I', '-B', '-X', 'utf8', '-X',
            'pycache_prefix=' + env['PYTHONPYCACHEPREFIX'], str(launcher),
            json.dumps(dict(checkout=str(checkout), evaluator=str(evaluator), bridge=str(bridge)))]
    return evaluator, checkout, bridge, env, argv


def valid_stale(path: Path, old: bytes, new: bytes):
    assert len(old) == len(new)
    path.write_bytes(old)
    stamp = path.stat()
    py_compile.compile(str(path), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    path.write_bytes(new)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))


def directory_alias(alias: Path, target: Path, temporary_root: Path):
    assert alias.absolute().is_relative_to(temporary_root.resolve())
    assert target.resolve(strict=True).is_relative_to(temporary_root.resolve())
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            raise
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('mode', ['source', 'sourceless'])
@pytest.mark.parametrize('direction', ['outgoing', 'incoming'])
def test_bound_lexical_symlink_cannot_use_external_stale_cache(tmp_path, mode, direction):
    _, checkout, bridge, env, argv = copied_launcher(tmp_path)
    target = tmp_path/'external-module' if direction == 'outgoing' else checkout/'src/actual-module'
    target.mkdir()
    alias = checkout/'src/linked-module' if direction == 'outgoing' else tmp_path/'incoming-alias'
    directory_alias(alias, target, tmp_path)
    module = alias/'alias_fixture.py'
    if mode == 'source':
        valid_stale(module, b'VALUE = 41\n', b'VALUE = 42\n')
    else:
        module.write_text('VALUE=41\n', encoding='utf-8')
        py_compile.compile(str(module), cfile=str(module.with_suffix('.pyc')), doraise=True)
        module.unlink()
    bridge.write_text(f"import sys\nsys.path.insert(0,{str(alias)!r})\nimport alias_fixture\nprint(alias_fixture.VALUE)\n", encoding='utf-8')
    before = tree_bytes(tmp_path)
    negative = subprocess.check_output([sys.executable, '-I', '-B', '-c',
        f'import sys;sys.path.insert(0,{str(alias)!r});import alias_fixture;print(alias_fixture.VALUE)'], timeout=10)
    assert negative.strip() == b'41'
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)
    if mode == 'source':
        assert result.returncode == 0 and result.stdout.strip() == '42', result.stderr
    else:
        assert result.returncode != 0 and 'requires source' in result.stderr
    assert tree_bytes(tmp_path) == before


def test_launcher_protects_distinct_product_and_evaluator_trees(tmp_path):
    evaluator, checkout, bridge, env, argv = copied_launcher(tmp_path)
    modules = [checkout/'src/product_fixture.py', evaluator/'src/evaluator_fixture.py',
               evaluator/'scripts/bridge_fixture.py']
    for module in modules:
        valid_stale(module, b'VALUE = 41\n', b'VALUE = 42\n')
    bridge.write_text(f'''import sys,json
sys.path[:0]=[{str(evaluator/'src')!r},{str(evaluator/'scripts')!r}]
import product_fixture,evaluator_fixture,bridge_fixture
from fixed_evaluation_source_launcher import policy_proof
print(json.dumps(dict(values=[m.VALUE for m in [product_fixture,evaluator_fixture,bridge_fixture]],policy=policy_proof())))
''', encoding='utf-8')
    before = tree_bytes(tmp_path)
    # The timestamp/size cache really is accepted by ordinary isolated Python.
    negative = subprocess.check_output([sys.executable, '-I', '-B', '-c',
        f'import sys;sys.path.insert(0,{str(checkout/"src")!r});import product_fixture;print(product_fixture.VALUE)'], timeout=10)
    assert negative.strip() == b'41'
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed['values'] == [42, 42, 42]
    assert observed['policy']['protected_roots'] == sorted({str(checkout/'src'), str(evaluator/'src'), str(evaluator/'scripts')})
    assert observed['policy']['installed_before_project_imports']
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize('fault', ['existing_prefix', 'no_write_guard', 'preloaded_project',
                                  'changed_loader', 'sourceless_project'])
def test_launcher_fails_closed_before_subject_action(tmp_path, fault):
    evaluator, checkout, bridge, env, argv = copied_launcher(tmp_path)
    marker = tmp_path/'subject-action'
    bridge.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n", encoding='utf-8')
    if fault == 'existing_prefix':
        Path(env['PYTHONPYCACHEPREFIX']).mkdir(parents=True)
    elif fault == 'no_write_guard':
        argv.remove('-B')
    elif fault == 'preloaded_project':
        launcher, arguments = argv[-2:]
        argv[-2:] = ['-c', f"import sys,runpy;sys.modules['offerpilot']=object();sys.argv=[{launcher!r},{arguments!r}];runpy.run_path({launcher!r},run_name='__main__')"]
    elif fault == 'changed_loader':
        bridge.write_text("from importlib.machinery import SourceFileLoader\n"
                          "SourceFileLoader.get_code=lambda *a:None\n"
                          "from fixed_evaluation_source_launcher import policy_proof\npolicy_proof()\n"
                          + bridge.read_text(encoding='utf-8'), encoding='utf-8')
    else:
        module = checkout/'src/source_missing.py'
        module.write_text('VALUE=9\n', encoding='utf-8')
        py_compile.compile(str(module), cfile=str(module.with_suffix('.pyc')), doraise=True)
        module.unlink()
        bridge.write_text('import source_missing\n' + bridge.read_text(encoding='utf-8'), encoding='utf-8')
    before = tree_bytes(tmp_path)
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and not marker.exists(), result.stderr
    after = tree_bytes(tmp_path)
    assert all(after.get(name) == value for name, value in before.items())
    # Omitting -B can write stdlib startup caches before this file is reached;
    # it must still fail before executing or writing either protected tree.
    assert not set(after)-set(before) or (fault == 'no_write_guard' and all(
        name.startswith('private/source-only-') for name in set(after)-set(before)))
