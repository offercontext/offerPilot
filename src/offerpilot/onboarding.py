from __future__ import annotations

from offerpilot.config import Config
from offerpilot.repositories.applications import ApplicationsRepository
from offerpilot.repositories.chat import ChatRepository
from offerpilot.repositories.resumes import ResumesRepository


def onboarding_payload(
    config: Config,
    applications: ApplicationsRepository,
    resumes: ResumesRepository,
    chat: ChatRepository,
) -> dict[str, object]:
    steps = {
        "configure_ai": any(
            provider.enabled and bool(provider.api_key)
            for provider in config.provider_profiles()
        ),
        "create_primary_resume": resumes.count_active_masters() > 0,
        "create_first_application": bool(applications.list()),
        "send_first_pilot_message": chat.has_user_message(),
    }
    completed_count = sum(1 for completed in steps.values() if completed)
    return {
        "steps": steps,
        "completed_count": completed_count,
        # Recording an application is the first useful outcome. AI, a resume and
        # a Pilot conversation are optional enhancements, not admission gates.
        "is_complete": steps["create_first_application"],
        "force_open": config.onboarding_force_open,
        "dismissed": config.onboarding_dismissed,
    }
