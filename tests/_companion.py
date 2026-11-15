"""Explicit opt-in for tests exercising an already enabled virtual companion."""
from uuid import uuid4

from offerpilot.companion.contracts import CompanionPolicy, CompanionSettingsCommand
from offerpilot.companion.repository import CompanionRepository


def enable_companion(repository: CompanionRepository) -> None:
    snapshot = repository.read()
    repository.update_settings(CompanionSettingsCommand(
        identity=snapshot['identity'], expected_revision=snapshot['revision'], command_id=uuid4(),
        settings=CompanionPolicy(enabled=True)), now=0)
