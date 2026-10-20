import subprocess
import sys
import json
import os
import py_compile
import pytest

from scripts.evaluation_subject.process_tree import ProcessTree
from offerpilot.evaluation.runner import process_environment
from tests.evaluation.test_contracts import ROOT


def test_credential_environment_is_not_inherited(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-test-key")
    env = process_environment(ROOT, ROOT / ".codex")
    assert "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
    assert "OFFERPILOT_DATA" in env


def test_subject_environment_disables_new_bytecode_without_parent_flags(tmp_path, monkeypatch):
    checkout = tmp_path / "readonly-subject"
    source = checkout / "src"
    source.mkdir(parents=True)
    module = source / "read_only_product.py"
    module.write_text("VALUE = 42\n", encoding="utf-8")
    before = {p.relative_to(checkout).as_posix(): p.read_bytes() for p in checkout.rglob("*") if p.is_file()}
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    result = subprocess.run(
        [sys.executable, "-c", "import sys,read_only_product; assert read_only_product.VALUE==42; print(sys.dont_write_bytecode)"],
        cwd=checkout, env=process_environment(checkout, tmp_path / "data"),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and result.stdout.strip() == "True", result.stderr
    assert not list(checkout.rglob("*.pyc")) and not (source / "__pycache__").exists()
    assert {p.relative_to(checkout).as_posix(): p.read_bytes() for p in checkout.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("entry", ["subject", "metadata"])
def test_source_loading_ignores_valid_stale_checkout_bytecode(tmp_path, monkeypatch, entry):
    from offerpilot.evaluation import runner
    from offerpilot.evaluation.contracts import VariantConfig

    checkout = tmp_path / "subject"
    source = checkout / "src"
    source.mkdir(parents=True)
    module = source / "read_only_product.py"
    module.write_bytes(b"VALUE = 41\n")
    stamp = module.stat()
    py_compile.compile(str(module), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    module.write_bytes(b"VALUE = 42\n")
    os.utime(module, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    before = {p.relative_to(checkout).as_posix(): p.read_bytes()
              for p in checkout.rglob("*") if p.is_file()}
    env = process_environment(checkout, tmp_path / "data")
    negative = dict(env)
    negative.pop("PYTHONPYCACHEPREFIX")
    code = "import read_only_product; print(read_only_product.VALUE)"
    assert subprocess.check_output([sys.executable, "-c", code], cwd=checkout,
                                   env=negative, timeout=30).strip() == b"41"
    if entry == "subject":
        assert subprocess.check_output([sys.executable, "-c", code], cwd=checkout,
                                       env=env, timeout=30).strip() == b"42"
    else:
        native = subprocess.check_output

        def verify_source(argv, **kwargs):
            actual = list(argv)
            actual[-1] = (f"import sys; sys.path.insert(0,{str(source)!r}); "
                          "import read_only_product; assert read_only_product.VALUE==42; "
                          + actual[-1])
            return native(actual, **kwargs)

        monkeypatch.setattr(runner.subprocess, "check_output", verify_source)
        assert runner.target_environment(VariantConfig(
            variant_id="candidate", checkout=str(checkout), interpreter=sys.executable))["python"]
    assert {p.relative_to(checkout).as_posix(): p.read_bytes()
            for p in checkout.rglob("*") if p.is_file()} == before


def test_process_tree_timeout_kills_owned_children(tmp_path) -> None:
    child = tmp_path / "child.py"
    child.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    argv = [sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen([sys.executable,sys.argv[1]]); time.sleep(60)", str(child)]
    tree = ProcessTree(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        try:
            tree.wait(0.5)
        except subprocess.TimeoutExpired:
            tree.kill()
        assert tree.wait(10) != 0
    finally:
        tree.close()


@pytest.mark.parametrize("operation", ["tcp", "udp", "dns", "native_child", "wrong_database"])
def test_subject_blocks_external_network_and_native_tools(tmp_path, operation) -> None:
    script = tmp_path / "guard.py"
    output = tmp_path / "attempts.json"
    script.write_text(
        "import sys,json,socket,subprocess,sqlite3\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(ROOT)!r})\n"
        "from scripts.evaluation_subject.bootstrap import network_guard\n"
        "class Recorder:\n"
        f" config={{'data_dir':{str(tmp_path / 'private')!r}}}\n"
        " def attempt(self,purpose,**metadata):\n"
        f"  Path({str(output)!r}).write_text(json.dumps([purpose,metadata]))\n"
        "network_guard(Recorder(),12345)\n"
        f"operation={operation!r}\n"
        "try:\n"
        " if operation=='tcp': socket.socket().connect(('198.51.100.1',443))\n"
        " elif operation=='udp': socket.socket(type=socket.SOCK_DGRAM).sendto(b'x',('198.51.100.1',53))\n"
        " elif operation=='dns': socket.getaddrinfo('synthetic.invalid',443)\n"
        f" elif operation=='wrong_database': sqlite3.connect({str(tmp_path / 'wrong.db')!r})\n"
        " else: subprocess.Popen([sys.executable,'-c','raise SystemExit(99)'])\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('external operation allowed')\n", encoding="utf-8")
    assert subprocess.run([sys.executable, str(script)], timeout=10, capture_output=True).returncode == 0
    purpose, metadata = json.loads(output.read_bytes())
    assert purpose == ("storage" if operation == "wrong_database" else "network") and metadata["blocked"] is True
    assert not (tmp_path / "wrong.db").exists()
