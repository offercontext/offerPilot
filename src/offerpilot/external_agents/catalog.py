"""Explicit, opt-in external resource grants; additions never widen old grants."""

BUSINESS_PROPOSALS = {
    "propose_application_create": "create_application",
    "propose_application_update": "update_application",
    "propose_application_delete": "delete_application",
    "propose_resume_create": "create_resume",
    "propose_resume_update": "update_resume",
    "propose_resume_copy": "copy_resume",
    "propose_resume_delete": "delete_resume",
    "propose_note_create": "create_note",
    "propose_note_update": "update_note",
    "propose_note_delete": "delete_note",
}
ACTIONS = ("create_interview", "reschedule_interview", *BUSINESS_PROPOSALS.values())
TOOL_CAPABILITIES = {
    "propose_application_create": "applications.create",
    "propose_application_update": "applications.write",
    "propose_application_delete": "applications.write",
    "list_resumes": "resumes.read",
    "get_resume": "resumes.read",
    "propose_resume_create": "resumes.write",
    "propose_resume_update": "resumes.write",
    "propose_resume_copy": "resumes.write",
    "propose_resume_delete": "resumes.write",
    "list_notes": "notes.read",
    "get_note": "notes.read",
    "propose_note_create": "notes.write",
    "propose_note_update": "notes.write",
    "propose_note_delete": "notes.write",
    "search_knowledge": "knowledge.read",
}
CAPABILITIES = frozenset((*TOOL_CAPABILITIES.values(), "notes.unbound"))


def permitted(tool: str, grant: dict[str, object]) -> bool:
    if tool in {"propose_interview", "propose_reschedule"}:
        return grant.get("allow_scheduling") is True
    if tool in {
        "list_applications",
        "get_application",
        "list_events",
        "get_event",
        "get_request",
        "cancel_request",
    }:
        return True
    required = TOOL_CAPABILITIES.get(tool)
    capabilities = grant.get("capabilities", [])
    return required is not None and isinstance(capabilities, list) and required in capabilities
