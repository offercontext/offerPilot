import subprocess
import sys
from pathlib import Path

from offerpilot.evaluation.evidence.observer import backup, next_epoch, read
from tests.evaluation.test_commit_observer import observed_db  # noqa: F401


def test_independent_process_dml_survives_exit_before_export(observed_db: Path) -> None:  # noqa: F811
    script = """
import sqlite3, sys, os
c = sqlite3.connect(sys.argv[1])
c.execute("INSERT INTO applications (company_name,position_name,notes,status,source) "
          "VALUES ('process','QA','','applied','test')")
c.execute("UPDATE applications SET notes='committed' WHERE id=1")
c.execute("DELETE FROM applications WHERE company_name='process'")
c.commit()
os._exit(23)
"""
    process = subprocess.run([sys.executable, "-c", script, str(observed_db)], timeout=15)
    assert process.returncode == 23
    evidence = read(observed_db)
    assert [r["operation"] for r in evidence["deltas"]] == ["INSERT", "UPDATE", "DELETE"]
    assert evidence["snapshot"]["applications"][0]["notes"] == "committed"  # O06/O07
    assert next_epoch(observed_db) == 2
    sealed = observed_db.with_name("sealed.db")
    backup(observed_db, sealed)
    assert read(sealed)["deltas"] == evidence["deltas"]
