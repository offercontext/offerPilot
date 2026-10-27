"""Pinned historical workflow regressions, separate from installed-profile tests.

These assets are never active workflows and confer no production authority.
"""
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[3]
ASSETS = {'legacy': ('legacy-workflow.yml.txt', '27620f8f229991e05c871ac6ea5a5090212a575e5036b57ecf00e4f7a170581b'), 'development': ('development-workflow.yml.txt', 'aaab1087a94be373de24175f807d43be0fa5f48837593406c1a9ed5466d738f8')}


def historical_workflow(name: str) -> Path:
    filename, digest = ASSETS[name]
    path = ROOT / 'scripts/companion_ai_controller/evidence' / filename
    assert path.is_file() and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    return path


def installed_profile(root: Path) -> str:
    folder = root / '.github/workflows'
    assert folder.is_dir() and not folder.is_symlink()
    production = folder / 'companion-ai-controller.yml'
    dry = folder / 'companion-ai-controller-dry-run.yml'
    assert dry.is_file() and not dry.is_symlink()
    if not production.exists():
        legacy = folder / 'companion-real-ai.yml'
        assert legacy.is_file() and not legacy.is_symlink()
        assert legacy.read_bytes() == historical_workflow('legacy').read_bytes()
        assert dry.read_bytes() == historical_workflow('development').read_bytes()
        return 'development'
    assert production.is_file() and not production.is_symlink()
    assert set(folder.iterdir()) == {production, dry}
    templates = root / 'scripts/companion_ai_controller/templates'
    assert production.read_bytes() in {
        (templates / 'companion-ai-controller.yml').read_bytes(),
        (templates / 'companion-ai-controller-actions-read.yml').read_bytes()}
    assert dry.read_bytes() == (templates / 'companion-ai-controller-review-dry-run.yml').read_bytes()
    return 'control'
