"""Regenerate a synthetic ZIP using the first historical ZIP export implementation."""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

COMMIT = "d0cd40688f9ec4802c760d7ae8e868516ca3ba9f"
ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sources = {name: subprocess.check_output(
        ["git", "show", f"{COMMIT}:src/offerpilot/{name}.py"], cwd=ROOT,
    ) for name in ("db", "models", "api")}
    export = next(node for node in ast.parse(sources["api"]).body
                  if isinstance(node, ast.FunctionDef) and node.name == "_build_backup_archive")
    with tempfile.TemporaryDirectory(prefix="offerpilot-legacy-fixture-") as directory:
        temporary = Path(directory)
        package = temporary / "offerpilot"
        package.mkdir()
        (package / "__init__.py").write_text("")
        for name in ("db", "models"):
            (package / (name + ".py")).write_bytes(sources[name])
        runner = temporary / "generate.py"
        runner.write_text(
            "from pathlib import Path\nfrom io import BytesIO\nimport zipfile,sqlite3\n"
            "from offerpilot.db import init_database\n" + ast.unparse(export) + "\n"
            "root=Path(__file__).parent/'synthetic'\n"
            "sessions=init_database(root/'data.db')\nsessions.kw['bind'].dispose()\n"
            "db=sqlite3.connect(root/'data.db')\n"
            "db.execute(\"INSERT INTO applications(company_name,position_name) VALUES ('Synthetic legacy','Test role')\")\n"
            "db.commit()\ndb.close()\n"
            "(root/'synthetic-note.txt').write_text('Synthetic fixture; no user data',encoding='utf-8')\n"
            "(root.parent/'backup.zip').write_bytes(_build_backup_archive(root))\n", encoding="utf-8",
        )
        subprocess.run([sys.executable, str(runner)], cwd=temporary, check=True)
        archive = (temporary / "backup.zip").read_bytes()
    payload = {
        "source_commit": COMMIT,
        "generation": "Original db/models modules and AST-extracted original _build_backup_archive; synthetic rows only",
        "source_sha256": {name: hashlib.sha256(value).hexdigest() for name, value in sources.items()},
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "archive_base64": base64.b64encode(archive).decode("ascii"),
    }
    (ROOT / "tests/fixtures/backups/legacy-d0cd4068.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
