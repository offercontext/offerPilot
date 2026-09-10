"""Current source checks for display, driven by the existing binding metadata."""

from __future__ import annotations

import hmac
import json
from typing import cast
from sqlalchemy import select

from offerpilot.ai.tool_authority.fingerprint import authorization_scope_fingerprint
from offerpilot.ai.tool_authority.policy import (
    BINDING_POLICY_FINGERPRINT,
    BINDING_POLICY_VERSION,
    CAPABILITY_POLICY_VERSION,
    CAPABILITY_PROFILE_FINGERPRINT,
    PROFILE_ID,
    decide_binding,
)
from offerpilot.ai.tool_authority.visibility import AuthorityApplicationVisibilityQuery
from offerpilot.ai.tool_runtime.metadata import (
    FrozenJSONObject, ToolMetadataBundleV1, WriteOperationMetadataV1, freeze_json,
)
from offerpilot.ai.tool_runtime.policy_types import UndoPolicy
from offerpilot.ai.write_operations import (
    TerminalPayload, WriteOperationRepository, compensation_operation_id,
    compensation_request_fingerprint, payload_from_operation,
)
from offerpilot.pilot_runtime.compensation import compensation_source_is_current
from offerpilot.models import (
    ApplicationEvent, ApplicationJDVersion, ApplicationMaterialKit, ApplicationSubmissionSnapshot,
    Conversation, InterviewNote, JDAnalysis, Offer, Resume, WriteOperation,
)


def pending_agent_source_is_current(
    conversation: Conversation,
    operation: WriteOperation,
    *,
    repository: WriteOperationRepository,
    metadata_bundle: ToolMetadataBundleV1,
) -> bool:
    if operation.adapter_kind == 'legacy_deterministic':
        return _legacy_sources_current(conversation, repository)
    if operation.adapter_kind != 'typed':
        return False
    expected = authorization_scope_fingerprint(
        repository.key,
        conversation_id=conversation.id,
        conversation_scope_revision=conversation.scope_revision,
        context_type=conversation.context_type,
        context_ref=conversation.context_ref if conversation.context_type == 'application' else None,
        mode=conversation.mode, capability_profile_id=PROFILE_ID,
        capability_policy_version=CAPABILITY_POLICY_VERSION,
        binding_policy_version=BINDING_POLICY_VERSION,
        capability_profile_fingerprint=CAPABILITY_PROFILE_FINGERPRINT,
        binding_policy_fingerprint=BINDING_POLICY_FINGERPRINT,
    )
    if not hmac.compare_digest(expected, operation.authorization_scope_fingerprint or ''):
        return False
    lease = metadata_bundle.open_segment_lease()
    try:
        handle = lease.resolve(operation.tool_name)
        if handle is None:
            return False
        spec = lease.require_spec(handle)
        args = spec.decoder(json.loads(conversation.pending_args))
        resolutions: list[object] = []
        with repository.session_factory() as session:
            scope_bound = conversation.context_type == 'application'
            bound_ids = [int(conversation.context_ref)] if scope_bound else []
            if bound_ids and AuthorityApplicationVisibilityQuery().execute_on_session(session, bound_ids[0]) is None:
                return False
            for descriptor in spec.metadata.binding.resolver_descriptors:
                raw = args.get(descriptor.arg_path)
                state = 'resolved'
                identity = raw
                if raw is None and descriptor.presence == 'optional':
                    state, identity = 'omitted', None
                elif type(raw) is not int or raw <= 0:
                    return False
                elif descriptor.resolver_id == 'application_identity_arg':
                    if AuthorityApplicationVisibilityQuery().execute_on_session(session, raw) is None:
                        return False
                elif descriptor.resolver_id == 'resume_identity_arg':
                    resume = session.get(Resume, raw)
                    if resume is None or resume.deleted_at is not None:
                        return False
                else:
                    # Source relations, not a second tool-capability catalog.
                    source_models = {
                        'application_event_parent': ApplicationEvent,
                        'note_application_parent': InterviewNote,
                        'offer_application_parent': Offer,
                        'jd_analysis_application_parent': JDAnalysis,
                    }
                    source_model = source_models.get(descriptor.resolver_id)
                    if source_model is None:
                        return False
                    row = session.get(source_model, raw)
                    if row is None or getattr(row, 'deleted_at', None) is not None:
                        return False
                    identity = getattr(row, 'application_id', None)
                    if identity is None or AuthorityApplicationVisibilityQuery().execute_on_session(session, identity) is None:
                        return False
                resolutions.append({
                    'entity_kind': descriptor.entity_kind, 'state': state,
                    'identity': identity, 'presence': descriptor.presence,
                })
        return decide_binding(
            spec.metadata.binding.contract.kind, scope_bound=scope_bound,
            bound_identities=bound_ids, resolutions=resolutions,
        ).allowed
    finally:
        lease.close()


def _legacy_sources_current(conversation: Conversation, repository: WriteOperationRepository) -> bool:
    """Check source relations; the exact Legacy presentation port validates its args and identity."""
    args = json.loads(conversation.pending_args)
    application_id = args.get('application_id')
    if type(application_id) is not int or application_id <= 0:
        return False
    if conversation.context_type == 'application' and conversation.context_ref != str(application_id):
        return False
    with repository.session_factory() as session:
        if AuthorityApplicationVisibilityQuery().execute_on_session(session, application_id) is None:
            return False
        if 'jd_text' in args:
            current_id = session.scalar(select(ApplicationJDVersion.id).where(
                ApplicationJDVersion.application_id == application_id,
            ).order_by(ApplicationJDVersion.version_number.desc()).limit(1))
            if current_id != args.get('expected_current_version_id'):
                return False
        if 'resume_id' in args:
            resume_id = args['resume_id']
            if type(resume_id) is not int or resume_id <= 0:
                return False
            resume = session.get(Resume, resume_id)
            if resume is None or resume.deleted_at is not None:
                return False
        for field, model in (
            ('jd_version_id', ApplicationJDVersion), ('material_kit_id', ApplicationMaterialKit),
            ('submission_snapshot_id', ApplicationSubmissionSnapshot), ('application_event_id', ApplicationEvent),
        ):
            identity = args.get(field)
            if identity is None:
                continue
            if type(identity) is not int or identity <= 0:
                return False
            source = session.get(model, identity)
            if source is None or getattr(source, 'application_id', None) != application_id:
                return False
        return True


def agent_undo_state(
    conversation: Conversation,
    operation: WriteOperation,
    payload: TerminalPayload,
    *,
    repository: WriteOperationRepository,
    metadata_bundle: ToolMetadataBundleV1,
) -> str:
    if not payload.undo_json:
        return 'unsupported'
    lease = metadata_bundle.open_segment_lease()
    try:
        handle = lease.resolve(operation.tool_name)
        if handle is None:
            return 'unknown'
        metadata = lease.require_spec(handle).metadata.operation
        if type(metadata) is not WriteOperationMetadataV1 or metadata.undo_policy is not UndoPolicy.REQUIRED:
            return 'unsupported'
        kind = metadata.compensation_kind
        if kind is None:
            return 'unknown'
        compensation_id = compensation_operation_id(operation.id, kind.value)
        with repository.session_factory() as session:
            compensation = session.get(WriteOperation, compensation_id)
            if compensation is not None:
                expected_request = compensation_request_fingerprint(
                    repository.key, operation_id=compensation_id, parent_operation_id=operation.id,
                    compensation_kind=kind.value, conversation_id=conversation.id,
                )
                if (
                    compensation.operation_role != 'compensation'
                    or compensation.adapter_kind != 'compensation'
                    or compensation.conversation_id != conversation.id
                    or compensation.tool_name != kind.value
                    or compensation.parent_operation_id != operation.id
                    or compensation.parent_terminal_payload_sha256 != payload.digest
                    or compensation.fingerprint_key_id != repository.key.key_id
                    or not hmac.compare_digest(compensation.operation_request_fingerprint or '', expected_request)
                ):
                    return 'unknown'
                if compensation.status == 'proposed':
                    return 'running'
                compensated = payload_from_operation(compensation, key=repository.key)
                if compensated.status == 'committed':
                    return 'undone'
                return 'conflict' if compensated.failure_category in {'conflict', 'stale_state'} else 'unknown'
            if conversation.last_write_operation_id != operation.id:
                return 'unknown'
            undo = json.loads(payload.undo_json)
            if undo != conversation.last_write_undo:
                return 'unknown'
            if compensation_source_is_current(session, cast(FrozenJSONObject, freeze_json(undo)), kind):
                return 'available'
            return 'conflict'
    finally:
        lease.close()
