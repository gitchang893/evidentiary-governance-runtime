from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from eg_runtime.models import (
    ActionProposal,
    Approval,
    DecisionRecord,
    Disposition,
    FrozenModel,
    GovernanceContext,
)


class ApprovalWorkflowError(RuntimeError):
    """Raised when an approval workflow cannot be created."""


class ApprovalResolutionStatus(StrEnum):
    """Outcome of a human approval resolution."""

    APPROVED = "approved"
    REJECTED = "rejected"
    INVALID = "invalid"


class PendingApproval(FrozenModel):
    """An escalated proposal awaiting human resolution."""

    approval_request_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    context: GovernanceContext
    proposal: ActionProposal
    decision: DecisionRecord
    created_at: datetime


class ApprovalResolution(FrozenModel):
    """Result of resolving one pending approval."""

    approval_request_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    status: ApprovalResolutionStatus
    resolver: str = Field(min_length=1)
    resolved_at: datetime
    reason: str = Field(min_length=1)
    validation_errors: list[str] = Field(
        default_factory=list
    )
    approval: Approval | None = None
    resumed_context: GovernanceContext | None = None

    @property
    def can_resume(self) -> bool:
        """Return whether the proposal may be evaluated again."""

        return (
            self.status
            is ApprovalResolutionStatus.APPROVED
            and self.approval is not None
            and self.resumed_context is not None
        )


def _canonical_json(
    value: Any,
) -> str:
    """Serialize a JSON-compatible value deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _request_id(
    context: GovernanceContext,
    proposal: ActionProposal,
    decision: DecisionRecord,
) -> str:
    """Create a deterministic approval-request identifier."""

    content = {
        "task_id": context.task_id,
        "actor": context.actor,
        "proposal": proposal.model_dump(mode="json"),
        "decision_id": decision.decision_id,
    }

    return hashlib.sha256(
        _canonical_json(content).encode("utf-8")
    ).hexdigest()


def _require_aware_datetime(
    value: datetime,
    field_name: str,
) -> None:
    """Require a timezone-aware workflow timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalWorkflowError(
            f"{field_name} must be timezone-aware"
        )


def _proposal_object_id(
    proposal: ActionProposal,
) -> str | None:
    """Extract the object bound to a proposed tool action."""

    candidate_keys = (
        "object_id",
        "record_id",
        "recipient",
        "document_id",
        "report_id",
    )

    for key in candidate_keys:
        value = proposal.arguments.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _constraint_value(
    arguments: dict[str, Any],
    key: str,
) -> Any:
    """Read a constraint value from direct or update arguments."""

    if key in arguments:
        return arguments[key]

    updates = arguments.get("updates")

    if isinstance(updates, dict) and key in updates:
        return updates[key]

    return None


def _validate_approval(
    pending: PendingApproval,
    approval: Approval,
    resolved_at: datetime,
) -> list[str]:
    """Validate approval identity, scope, and validity period."""

    errors: list[str] = []

    if approval.subject != pending.context.actor:
        errors.append("approval_subject_mismatch")

    if approval.action != pending.proposal.tool:
        errors.append("approval_action_mismatch")

    expected_object = _proposal_object_id(
        pending.proposal
    )

    if (
        expected_object is not None
        and approval.object_id != expected_object
    ):
        errors.append("approval_object_mismatch")

    if approval.issued_at > resolved_at:
        errors.append("approval_not_yet_valid")

    if approval.expires_at <= resolved_at:
        errors.append("approval_expired")

    for key, expected_value in approval.constraints.items():
        observed_value = _constraint_value(
            pending.proposal.arguments,
            key,
        )

        if observed_value != expected_value:
            errors.append(
                f"approval_constraint_mismatch:{key}"
            )

    return errors


class ApprovalWorkflow:
    """Create and resolve human approval requests."""

    def create_pending(
        self,
        *,
        context: GovernanceContext,
        proposal: ActionProposal,
        decision: DecisionRecord,
        created_at: datetime,
    ) -> PendingApproval:
        """Create a pending request from an escalation decision."""

        _require_aware_datetime(
            created_at,
            "created_at",
        )

        if decision.disposition is not Disposition.ESCALATE:
            raise ApprovalWorkflowError(
                "pending approval requires "
                "an escalate decision"
            )

        return PendingApproval(
            approval_request_id=_request_id(
                context,
                proposal,
                decision,
            ),
            context=context,
            proposal=proposal,
            decision=decision,
            created_at=created_at,
        )

    def resolve(
        self,
        pending: PendingApproval,
        *,
        resolver: str,
        approved: bool,
        resolved_at: datetime,
        approval: Approval | None = None,
        reason: str | None = None,
    ) -> ApprovalResolution:
        """Resolve a pending request and build a resumed context."""

        _require_aware_datetime(
            resolved_at,
            "resolved_at",
        )

        normalized_resolver = resolver.strip()

        if not normalized_resolver:
            raise ApprovalWorkflowError(
                "resolver must be a non-empty string"
            )

        normalized_reason = (
            reason.strip()
            if isinstance(reason, str)
            else ""
        )

        if not approved:
            return ApprovalResolution(
                approval_request_id=(
                    pending.approval_request_id
                ),
                status=ApprovalResolutionStatus.REJECTED,
                resolver=normalized_resolver,
                resolved_at=resolved_at,
                reason=(
                    normalized_reason
                    or "human reviewer rejected the proposal"
                ),
                validation_errors=[],
                approval=None,
                resumed_context=None,
            )

        if approval is None:
            return ApprovalResolution(
                approval_request_id=(
                    pending.approval_request_id
                ),
                status=ApprovalResolutionStatus.INVALID,
                resolver=normalized_resolver,
                resolved_at=resolved_at,
                reason=(
                    normalized_reason
                    or "approved resolution lacks approval data"
                ),
                validation_errors=[
                    "approval_missing",
                ],
                approval=None,
                resumed_context=None,
            )

        validation_errors = _validate_approval(
            pending,
            approval,
            resolved_at,
        )

        if validation_errors:
            return ApprovalResolution(
                approval_request_id=(
                    pending.approval_request_id
                ),
                status=ApprovalResolutionStatus.INVALID,
                resolver=normalized_resolver,
                resolved_at=resolved_at,
                reason=(
                    normalized_reason
                    or "approval validation failed"
                ),
                validation_errors=validation_errors,
                approval=approval,
                resumed_context=None,
            )

        history_entry = {
            "event": "approval_resolved",
            "approval_request_id": (
                pending.approval_request_id
            ),
            "approval_id": approval.approval_id,
            "resolver": normalized_resolver,
            "resolved_at": resolved_at.isoformat(),
            "approved": True,
        }

        resumed_context = pending.context.model_copy(
            update={
                "approval": approval,
                "history": [
                    *pending.context.history,
                    history_entry,
                ],
            }
        )

        return ApprovalResolution(
            approval_request_id=(
                pending.approval_request_id
            ),
            status=ApprovalResolutionStatus.APPROVED,
            resolver=normalized_resolver,
            resolved_at=resolved_at,
            reason=(
                normalized_reason
                or "human reviewer approved the proposal"
            ),
            validation_errors=[],
            approval=approval,
            resumed_context=resumed_context,
        )
