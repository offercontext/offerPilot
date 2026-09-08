"""Read-only public presentation for Product Actions.

The Product Action ledger is the source of truth for this projection.  This
module deliberately has no confirmation, execution, or compensation calls;
all reads go through the existing integrity checked repositories and handlers.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, cast

from sqlalchemy import select

from offerpilot.ai.write_operations import payload_from_operation
from offerpilot.models import WriteOperation, WriteOperationTransition
from offerpilot.presentation_contracts import (
    ActionPresentationV1,
    build_action_presentation,
    presentation_fingerprint,
)
from offerpilot.product_actions.compensation import (
    product_action_compensation_input_fingerprint,
    product_action_compensation_operation_id,
    product_action_compensation_request_fingerprint,
)
from offerpilot.product_actions.contracts import (
    ProductActionContractError,
    ProductActionIntegrityError,
    decode_product_action_route_payload,
    materialize_frozen_json,
    require_product_action_uuid,
)
from offerpilot.product_actions.coordinator import ProductActionCoordinator
from offerpilot.product_actions.repository import ProductActionBundleV1, ProductActionProposalRepository
from offerpilot.ai.tool_runtime.contracts import JSONValue
from offerpilot.review_readiness.repository import (
    ReadinessSignalRepository,
    ReviewReadinessReadNotFound,
    ReviewReadinessReadUnavailable,
)


ProductActionEvidence = Literal["verified", "incomplete", "unavailable"]
SourceCurrent = Literal["current", "stale", "unknown"]
CompensationState = Literal["absent", "running", "undone", "conflict", "unknown"]


@dataclass(frozen=True, slots=True)
class _SourceCheck:
    state: SourceCurrent
    evidence: ProductActionEvidence


@dataclass(frozen=True, slots=True)
class _CompensationRead:
    state: CompensationState


_COPY: dict[str, tuple[str, str, str, str]] = {
    # title, source label, pending summary, stale summary
    "save_review_readiness_signal": (
        "保存准备重点",
        "准备重点",
        "确认后将其保存为下次准备重点。",
        "来源已变化，请刷新后重试。",
    ),
    "confirm_interview_story": (
        "保存经历素材",
        "经历素材",
        "确认后将其保存到经历素材。",
        "来源已变化，请刷新后重试。",
    ),
}


def _same_text(left: object, right: object) -> bool:
    return (
        type(left) is str
        and type(right) is str
        and hmac.compare_digest(left, right)
    )


def _route_payload(bundle: ProductActionBundleV1) -> Mapping[str, object]:
    raw = bundle.route.route_payload_json
    if raw is None:
        raise ProductActionIntegrityError("product_action_route_missing")
    try:
        decoded = decode_product_action_route_payload(
            raw.encode("utf-8"),
            action_name=bundle.route.action_name,
            request_origin=bundle.route.request_origin,
        )
        materialized = materialize_frozen_json(decoded.payload)
    except (ProductActionContractError, UnicodeEncodeError) as exc:
        raise ProductActionIntegrityError("product_action_route_payload") from exc
    if type(materialized) is not dict:
        raise ProductActionIntegrityError("product_action_route_payload")
    return cast(Mapping[str, object], materialized)


def _source_revision(bundle: ProductActionBundleV1) -> str:
    """Return a stable, opaque identity for the persisted source binding."""

    return presentation_fingerprint(
        {
            "source_kind": bundle.route.source_kind,
            "source_id": bundle.route.source_id,
            "source_revision": bundle.route.source_revision,
            "route_binding_fingerprint": bundle.route.route_binding_fingerprint,
        }
    )


def _candidate_matches_route(payload: Mapping[str, object], candidate: object) -> bool:
    # The repository owns the exact CandidateProjection type.  Keep this
    # comparison structural so the display module cannot accidentally expose
    # evidence or the candidate body.
    values = {
        "proposal_id": payload.get("proposal_id"),
        "focus_id": payload.get("focus_id"),
        "source_note_revision": payload.get("expected_note_revision"),
        "source_note_fingerprint": payload.get("expected_source_fingerprint"),
        "source_proposal_hash": payload.get("expected_proposal_hash"),
        "candidate_fingerprint": payload.get("expected_candidate_fingerprint"),
    }
    return all(
        getattr(candidate, field, object()) == expected
        if field in {"proposal_id", "focus_id", "source_note_revision"}
        else _same_text(getattr(candidate, field, None), expected)
        for field, expected in values.items()
    )


class ProductActionPresentationBuilder:
    """Build one safe ``ActionPresentationV1`` from a Product Action bundle."""

    def __init__(
        self,
        proposal_repository: ProductActionProposalRepository,
        coordinator: ProductActionCoordinator,
        *,
        readiness_repository: ReadinessSignalRepository | object | None = None,
        stories_repository: object | None = None,
        current_undo_operation_id: str | None = None,
        compensation_coordinator: object | None = None,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not callable(getattr(proposal_repository, "load_bundle", None)):
            raise TypeError("Product Action presentation requires a proposal repository")
        if not callable(getattr(coordinator, "verify_terminal_projection", None)):
            raise TypeError("Product Action presentation requires a coordinator")
        if current_undo_operation_id is not None and type(current_undo_operation_id) is not str:
            raise TypeError("Product Action presentation Undo owner is invalid")
        self._proposal_repository = proposal_repository
        self._coordinator = coordinator
        self._readiness_repository = readiness_repository
        self._stories_repository = stories_repository
        self._current_undo_operation_id = current_undo_operation_id
        self._compensation_coordinator = compensation_coordinator
        self._session_factory = session_factory

    def __call__(self, operation_id: str) -> ActionPresentationV1:
        return self.build(operation_id)

    def build(self, operation_id: str) -> ActionPresentationV1:
        try:
            normalized = require_product_action_uuid(operation_id, "operation_id")
            bundle = self._proposal_repository.load_bundle(normalized)
        except ProductActionContractError as exc:
            raise ProductActionIntegrityError("product_action_bundle_absent") from exc
        if type(bundle) is not ProductActionBundleV1:
            raise ProductActionIntegrityError("product_action_bundle_shape")
        if bundle.operation.id != normalized:
            raise ProductActionIntegrityError("product_action_operation_identity")
        if bundle.classification == "exact_proposed":
            return self._build_proposed(bundle)
        if bundle.classification == "exact_terminal":
            return self._build_terminal(bundle)
        raise ProductActionIntegrityError("product_action_bundle_classification")

    def _base_values(
        self,
        bundle: ProductActionBundleV1,
        *,
        summary: str,
        decision: str,
        execution: str,
        evidence: ProductActionEvidence,
        undo: str,
        available_actions: list[str],
    ) -> ActionPresentationV1:
        copy = _COPY.get(bundle.operation.tool_name)
        if copy is None:
            raise ProductActionIntegrityError("product_action_action_name")
        return build_action_presentation(
            source_kind="product_action",
            operation_id=bundle.operation.id,
            source_revision=_source_revision(bundle),
            title=copy[0],
            target=None,
            summary=summary,
            source_label=copy[1],
            decision=decision,
            execution=execution,
            evidence=evidence,
            undo=undo,
            available_actions=available_actions,
        )

    def _build_proposed(self, bundle: ProductActionBundleV1) -> ActionPresentationV1:
        payload = _route_payload(bundle)
        source = self._proposed_source_check(bundle, payload)
        copy = _COPY[bundle.operation.tool_name]
        actions = ["approve", "modify", "reject"] if source.state == "current" else ["reject", "refresh"]
        summary = copy[2] if source.state == "current" else copy[3]
        return self._base_values(
            bundle,
            summary=summary,
            decision="undecided",
            execution="not_started",
            evidence=source.evidence,
            undo="unsupported",
            available_actions=actions,
        )

    def _build_terminal(self, bundle: ProductActionBundleV1) -> ActionPresentationV1:
        evidence: ProductActionEvidence
        # ``verify_terminal_projection`` checks the closed result and transport
        # codec.  It returns only bounded, provider-free data and is also the
        # source for the exact P0 visible receipt.
        try:
            state = self._coordinator.get_state(bundle.operation.id)
            if state.operation_id != bundle.operation.id or state.status != bundle.operation.status:
                raise ProductActionIntegrityError('product_action_terminal_state')
            projection = self._coordinator.verify_terminal_projection(bundle)
        except ProductActionIntegrityError:
            raise
        except Exception as exc:
            raise ProductActionIntegrityError("product_action_terminal_projection") from exc
        status = bundle.operation.status
        if status == "rejected":
            decision = self._verified_terminal_decision(bundle)
            execution = "not_started"
            evidence = "verified" if decision == "rejected" else "incomplete"
            undo = "unsupported"
            actions = [] if decision == "rejected" else ["refresh"]
        elif status in {"committed", "failed"}:
            decision = self._verified_terminal_decision(bundle)
            execution = cast(Literal["committed", "failed"], status)
            evidence = "verified" if decision != "unknown" else "incomplete"
            if status == "committed":
                undo = self._undo_state(bundle, projection.undo)
                actions = ["undo"] if undo == "available" else (["refresh"] if undo in {"unknown", "conflict"} else [])
            else:
                undo = "unsupported"
                actions = ["refresh"] if decision == "unknown" else []
        else:
            raise ProductActionIntegrityError("product_action_terminal_status")
        return self._base_values(
            bundle,
            # P0 visible_result is a trusted receipt and must be reused byte for
            # byte.  Do not synthesize a second explanation from result_json.
            summary=projection.visible_result,
            decision=decision,
            execution=execution,
            evidence=evidence,
            undo=undo,
            available_actions=actions,
        )

    def _proposed_source_check(
        self,
        bundle: ProductActionBundleV1,
        payload: Mapping[str, object],
    ) -> _SourceCheck:
        if bundle.operation.tool_name == "save_review_readiness_signal":
            repository = self._readiness_repository
            projector = getattr(repository, "project_candidates", None)
            if not callable(projector):
                return _SourceCheck("unknown", "incomplete")
            note_id = payload.get("note_id")
            proposal_id = payload.get("proposal_id")
            if type(note_id) is not int or type(proposal_id) is not int:
                return _SourceCheck("unknown", "incomplete")
            try:
                projection = projector(note_id=note_id, proposal_id=proposal_id)
            except ReviewReadinessReadNotFound:
                return _SourceCheck("stale", "unavailable")
            except ReviewReadinessReadUnavailable:
                return _SourceCheck("unknown", "incomplete")
            except Exception:
                # Display reads must fail closed without exposing database or
                # provider exception text in the DTO.
                return _SourceCheck("unknown", "incomplete")
            if getattr(projection, "state", None) != "ready":
                return _SourceCheck("stale", "unavailable")
            candidates = getattr(projection, "candidates", ())
            candidate = next(
                (item for item in candidates if getattr(item, "focus_id", None) == payload.get("focus_id")),
                None,
            )
            if candidate is None:
                return _SourceCheck("stale", "unavailable")
            if _candidate_matches_route(payload, candidate):
                return _SourceCheck("current", "verified")
            return _SourceCheck("stale", "unavailable")

        if bundle.operation.tool_name == "confirm_interview_story":
            repository = self._stories_repository
            checker = getattr(repository, "product_action_source_is_current", None)
            if not callable(checker):
                return _SourceCheck("unknown", "incomplete")
            attempt_id = payload.get("attempt_id")
            if type(attempt_id) is not int:
                return _SourceCheck("unknown", "incomplete")
            try:
                current = checker(attempt_id=attempt_id, operation_id=bundle.operation.id)
            except Exception:
                return _SourceCheck("unknown", "incomplete")
            return _SourceCheck("current", "verified") if current is True else _SourceCheck("stale", "unavailable")
        raise ProductActionIntegrityError("product_action_action_name")

    def _verified_terminal_decision(self, bundle: ProductActionBundleV1) -> str:
        # A coordinator may expose this helper in newer deployments.  Keep the
        # local fallback for old records while retaining the same conservative
        # rule: only a keyed fingerprint proves approve versus modify.
        for name in ("verified_terminal_decision", "project_terminal_decision"):
            helper = getattr(self._coordinator, name, None)
            if callable(helper):
                try:
                    value = helper(bundle)
                except Exception:
                    return "unknown"
                if value in {"approved", "modified", "rejected", "unknown"}:
                    return cast(str, value)
                if isinstance(value, Mapping) and value.get("decision") in {"approved", "modified", "rejected", "unknown"}:
                    return cast(str, value["decision"])
                return "unknown"
        request_fingerprint = getattr(self._coordinator, "_request_fingerprint", None)
        persisted = bundle.operation.operation_request_fingerprint
        if bundle.operation.status == "rejected":
            if not callable(request_fingerprint) or type(persisted) is not str:
                return "unknown"
            try:
                request = request_fingerprint(
                    bundle,
                    SimpleNamespace(decision="reject", edited_payload=None, confirmation_token=""),
                    None,
                )
            except Exception:
                return "unknown"
            return "rejected" if type(request) is str and hmac.compare_digest(request, persisted) else "unknown"
        handler = getattr(self._coordinator, "_handlers", {}).get(bundle.operation.tool_name)
        effective = getattr(handler, "persisted_terminal_effective_payload_sha256", None)
        if not callable(effective) or not callable(request_fingerprint):
            return "unknown"
        try:
            effective_hash = effective(bundle.operation.id)
            if type(persisted) is not str:
                return "unknown"
            matches: list[str] = []
            for value in ("approve", "modify"):
                request = request_fingerprint(
                    bundle,
                    SimpleNamespace(decision=value, edited_payload=None, confirmation_token=""),
                    effective_hash,
                )
                if type(request) is str and hmac.compare_digest(request, persisted):
                    matches.append(value)
            if matches == ["approve"]:
                return "approved"
            if matches == ["modify"]:
                return "modified"
        except Exception:
            return "unknown"
        return "unknown"

    def _terminal_source_current(self, bundle: ProductActionBundleV1, result: Mapping[str, object]) -> SourceCurrent:
        if bundle.operation.tool_name == "save_review_readiness_signal":
            loader = getattr(self._readiness_repository, "load_by_operation", None)
            if not callable(loader):
                return "unknown"
            try:
                aggregate = loader(bundle.operation.id)
            except Exception:
                return "unknown"
            if aggregate is None:
                return "stale"
            expected = (
                getattr(aggregate, "write_operation_id", None) == bundle.operation.id
                and getattr(aggregate, "disposition", None) == "active"
                and getattr(aggregate, "signal_id", None) == result.get("signal_id")
                and getattr(aggregate, "current_version_id", None) == result.get("signal_version_id")
                and getattr(aggregate, "signal_revision", None) == result.get("signal_revision")
            )
            return "current" if expected else "stale"

        if bundle.operation.tool_name == "confirm_interview_story":
            attempt_loader = getattr(self._stories_repository, "get_attempt", None)
            story_loader = getattr(self._stories_repository, "get_story", None)
            if not callable(attempt_loader) or not callable(story_loader):
                return "unknown"
            try:
                attempt = attempt_loader(bundle.route.source_id)
                story = story_loader(result.get("story_id"))
            except Exception:
                return "unknown"
            if not isinstance(attempt, Mapping) or not isinstance(story, Mapping):
                return "stale"
            expected = (
                attempt.get("attempt_status") == "confirmed"
                and attempt.get("product_action_operation_id") == bundle.operation.id
                and attempt.get("confirmed_story_id") == result.get("story_id")
                and attempt.get("confirmed_story_version_id") == result.get("story_version_id")
                and story.get("status") == "active"
                and story.get("current_version_id") == result.get("story_version_id")
                and story.get("story_revision") == result.get("story_revision")
            )
            return "current" if expected else "stale"
        return "unknown"

    def _undo_state(self, bundle: ProductActionBundleV1, undo_payload: object) -> str:
        if not isinstance(undo_payload, Mapping):
            return "unsupported"
        if (
            self._current_undo_operation_id is not None
            and self._current_undo_operation_id != bundle.operation.id
        ):
            return "unknown"
        compensation = self._read_compensation(bundle, undo_payload)
        if compensation.state == "undone":
            return "undone"
        if compensation.state == "running":
            return "running"
        if compensation.state in {"conflict", "unknown"}:
            return compensation.state
        source = self._terminal_source_current(bundle, self._terminal_result(bundle))
        if source == "current":
            return "available"
        if source == "stale":
            return "conflict"
        return "unknown"

    @staticmethod
    def _terminal_result(bundle: ProductActionBundleV1) -> Mapping[str, object]:
        raw = bundle.operation.result_json
        if raw is None:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}

    def _read_compensation(
        self,
        bundle: ProductActionBundleV1,
        undo_payload: Mapping[str, object],
    ) -> _CompensationRead:
        factory = self._session_factory
        if factory is None and self._compensation_coordinator is not None:
            candidate = getattr(self._compensation_coordinator, "_session_factory", None)
            if callable(candidate):
                factory = candidate
        if factory is None:
            # Absence can only be established from the same read snapshot as
            # the parent.  Without a session owner, an unknown compensation
            # must never be presented as an Undo capability.
            return _CompensationRead("unknown")
        try:
            compensation_id = product_action_compensation_operation_id(
                bundle.operation.id,
                f"undo:{bundle.operation.tool_name}",
            )
        except Exception:
            return _CompensationRead("unknown")
        try:
            with factory() as session:
                operation = session.get(WriteOperation, compensation_id)
                if operation is None:
                    return _CompensationRead("absent")
                if (
                    operation.operation_role != "compensation"
                    or operation.adapter_kind != "compensation"
                    or operation.tool_name != f"undo:{bundle.operation.tool_name}"
                    or operation.parent_operation_id != bundle.operation.id
                ):
                    return _CompensationRead("unknown")
                parent_digest = bundle.operation.terminal_payload_sha256
                if type(parent_digest) is not str:
                    return _CompensationRead("unknown")
                profiles = getattr(self._compensation_coordinator, "_key_profiles", None)
                if profiles is None:
                    profiles = getattr(self._coordinator, "_key_profiles", None)
                resolver = getattr(profiles, "resolve", None)
                if not callable(resolver):
                    return _CompensationRead("unknown")
                key = resolver(operation.fingerprint_key_id)
                expected_request = product_action_compensation_request_fingerprint(
                    key,
                    operation_id=compensation_id,
                    parent_operation_id=bundle.operation.id,
                    parent_action_name=bundle.operation.tool_name,
                    parent_terminal_payload_sha256=parent_digest,
                    compensation_kind=operation.tool_name,
                )
                if (
                    type(operation.operation_request_fingerprint) is not str
                    or not hmac.compare_digest(
                        operation.operation_request_fingerprint,
                        expected_request,
                    )
                ):
                    return _CompensationRead("unknown")
                transitions = tuple(
                    (item.seq, item.state)
                    for item in session.scalars(
                        select(WriteOperationTransition)
                        .where(WriteOperationTransition.operation_id == compensation_id)
                        .order_by(WriteOperationTransition.seq, WriteOperationTransition.id)
                    )
                )
                if operation.status == "proposed":
                    return _CompensationRead("running") if transitions == ((1, "proposed"),) else _CompensationRead("unknown")
                if operation.status not in {"committed", "failed"}:
                    return _CompensationRead("unknown")
                terminal_at = operation.committed_at if operation.status == "committed" else operation.failed_at
                if (
                    operation.approved_at is None
                    or operation.claimed_at is None
                    or terminal_at is None
                    or operation.delivered_at != terminal_at
                    or operation.result_contract != "compensation_json_v1"
                    or operation.result_json is None
                    or operation.visible_result is None
                    or operation.transport_json is None
                    or operation.undo_json is not None
                    or operation.delivery_status != "not_applicable"
                    or operation.delivery_outcome != "none"
                    or operation.delivery_message_count != 0
                    or operation.delivery_generation != 0
                    or operation.failure_category
                    != ("stale_state" if operation.status == "failed" else None)
                    or operation.failed_at is not None
                    and operation.status == "committed"
                    or operation.committed_at is not None
                    and operation.status == "failed"
                ):
                    return _CompensationRead("unknown")
                expected_input = product_action_compensation_input_fingerprint(
                    key,
                    operation_request_fingerprint=operation.operation_request_fingerprint,
                    parent_terminal_payload_sha256=parent_digest,
                    validated_undo_json=cast(dict[str, JSONValue], dict(undo_payload)),
                )
                if (
                    type(operation.input_fingerprint) is not str
                    or not hmac.compare_digest(operation.input_fingerprint, expected_input)
                ):
                    return _CompensationRead("unknown")
                if operation.status == "committed" and transitions != ((1, "proposed"), (2, "approved"), (3, "claimed"), (4, "committed")):
                    return _CompensationRead("unknown")
                if operation.status == "failed" and transitions != ((1, "proposed"), (2, "approved"), (3, "claimed"), (4, "failed")):
                    return _CompensationRead("unknown")
                parent = session.get(WriteOperation, bundle.operation.id)
                if parent is None or operation.parent_terminal_payload_sha256 != parent.terminal_payload_sha256:
                    return _CompensationRead("unknown")
                payload = payload_from_operation(operation, key=key)
                transport = json.loads(payload.transport_json)
                result = json.loads(payload.result_json)
                if (
                    not isinstance(result, Mapping)
                    or not isinstance(transport, Mapping)
                    or transport
                    != {
                        "schema_version": 1,
                        "operation_id": compensation_id,
                        "compensation_kind": operation.tool_name,
                        "status": operation.status,
                        "result": result,
                    }
                ):
                    return _CompensationRead("unknown")
                return _CompensationRead("undone" if operation.status == "committed" else "conflict")
        except Exception:
            return _CompensationRead("unknown")


def build_product_action_presentation(
    operation_id: str,
    *,
    proposal_repository: ProductActionProposalRepository,
    coordinator: ProductActionCoordinator,
    readiness_repository: object | None = None,
    stories_repository: object | None = None,
    current_undo_operation_id: str | None = None,
    compensation_coordinator: object | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> ActionPresentationV1:
    """Convenience function used by the API read route."""

    return ProductActionPresentationBuilder(
        proposal_repository,
        coordinator,
        readiness_repository=readiness_repository,
        stories_repository=stories_repository,
        current_undo_operation_id=current_undo_operation_id,
        compensation_coordinator=compensation_coordinator,
        session_factory=session_factory,
    ).build(operation_id)


# Short aliases are kept at the module boundary so API composition can use
# either the domain noun or the generic presentation verb without duplicating
# the implementation.
project_product_action = build_product_action_presentation
ProductActionPresentation = ActionPresentationV1


__all__ = [
    "ProductActionPresentation",
    "ProductActionPresentationBuilder",
    "build_product_action_presentation",
    "project_product_action",
]
