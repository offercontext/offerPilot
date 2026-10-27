"""Validate the real installed workflow profile independently of history fixtures."""
from pathlib import Path
import shutil

import pytest

from scripts.companion_ai_controller.tests import workflow_assets as assets


def test_actual_installed_profile_matches_exact_reviewed_bytes():
    assert assets.installed_profile(assets.ROOT) in {'development', 'control'}


@pytest.mark.parametrize('authenticated', [False, True])
def test_sanitized_control_profile_can_run_same_regression_assets(tmp_path, authenticated):
    folder = tmp_path / '.github/workflows'
    folder.mkdir(parents=True)
    relative = Path('scripts/companion_ai_controller/templates')
    shutil.copytree(assets.ROOT / relative, tmp_path / relative)
    templates = tmp_path / relative
    name = 'companion-ai-controller-actions-read.yml' if authenticated else 'companion-ai-controller.yml'
    shutil.copyfile(templates / name, folder / 'companion-ai-controller.yml')
    shutil.copyfile(templates / 'companion-ai-controller-review-dry-run.yml',
                    folder / 'companion-ai-controller-dry-run.yml')
    assert assets.installed_profile(tmp_path) == 'control'
    # Historical tests still exercise exact old workflow contracts, not a
    # reconstructed or relabeled production installation.
    assert assets.historical_workflow('legacy').is_file()
    assert assets.historical_workflow('development').is_file()


@pytest.mark.parametrize('mutation', ['extra', 'nested', 'changed_production', 'changed_dry', 'missing_dry', 'symlink'])
def test_control_profile_rejects_mutation(tmp_path, mutation):
    folder = tmp_path / '.github/workflows'
    folder.mkdir(parents=True)
    relative = Path('scripts/companion_ai_controller/templates')
    shutil.copytree(assets.ROOT / relative, tmp_path / relative)
    templates = tmp_path / relative
    production = folder / 'companion-ai-controller.yml'
    dry = folder / 'companion-ai-controller-dry-run.yml'
    shutil.copyfile(templates / 'companion-ai-controller.yml', production)
    shutil.copyfile(templates / 'companion-ai-controller-review-dry-run.yml', dry)
    if mutation == 'extra':
        (folder / 'extra.yml').write_text('on: push')
    elif mutation == 'nested':
        (folder / 'nested').mkdir()
    elif mutation == 'changed_production':
        production.write_text(production.read_text() + '\n# altered\n')
    elif mutation == 'changed_dry':
        dry.write_text('on: push')
    elif mutation == 'missing_dry':
        dry.unlink()
    else:
        production.unlink()
        production.symlink_to(templates / 'companion-ai-controller.yml')
    with pytest.raises(AssertionError):
        assets.installed_profile(tmp_path)


def test_historical_asset_tampering_is_not_accepted(tmp_path, monkeypatch):
    relative = Path('scripts/companion_ai_controller/evidence')
    (tmp_path / relative).mkdir(parents=True)
    for filename, _ in assets.ASSETS.values():
        (tmp_path / relative / filename).write_bytes(b'altered workflow')
    monkeypatch.setattr(assets, 'ROOT', tmp_path)
    for name in assets.ASSETS:
        with pytest.raises(AssertionError):
            assets.historical_workflow(name)
