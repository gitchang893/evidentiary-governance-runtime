from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from eg_runtime.models import (
    ActionProposal,
    GovernanceContext,
    PredicateResult,
    RuntimeNorm,
    utc_now,
)


class PredicateEvaluationError(RuntimeError):
    """Raised when a runtime predicate cannot be evaluated."""


PredicateHandler = Callable[
    [RuntimeNorm, GovernanceContext, ActionProposal, datetime],
    PredicateResult,
]


def _string_list(value: Any, field_name: str) -> list[str]:
    """Validate and normalize a predicate parameter containing strings."""

    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise PredicateEvaluationError(
            f"{field_name} must be a list of strings"
        )

    return value


def _normalize_token(value: str) -> str:
    """Normalize tool and plan text for deterministic comparison."""

    return " ".join(
        value.lower()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def _proposal_object_id(
    norm: RuntimeNorm,
    proposal: ActionProposal,
) -> str | None:
    """Resolve the object identifier used by approval binding."""

    configured_field = norm.predicate.get("object_argument")

    if configured_field is not None:
        if not isinstance(configured_field, str):
            raise PredicateEvaluationError(
                "object_argument must be a string"
            )

        value = proposal.arguments.get(configured_field)
        return str(value) if value is not None else None

    for field_name in (
        "record_id",
        "object_id",
        "document_id",
        "report_id",
    ):
        value = proposal.arguments.get(field_name)

        if value is not None:
            return str(value)

    return None


def _proposal_constraint_value(
    proposal: ActionProposal,
    constraint_name: str,
) -> Any:
    """Resolve a constraint from direct or nested proposal arguments."""

    if constraint_name in proposal.arguments:
        return proposal.arguments[constraint_name]

    updates = proposal.arguments.get("updates")

    if isinstance(updates, dict) and constraint_name in updates:
        return updates[constraint_name]

    constraints = proposal.arguments.get("constraints")

    if isinstance(constraints, dict) and constraint_name in constraints:
        return constraints[constraint_name]

    return None


class PredicateEngine:
    """Evaluate deterministic organizational predicates."""

    def __init__(self) -> None:
        self._handlers: dict[str, PredicateHandler] = {
            "tool_authorized": self._tool_authorized,
            "role_authorized": self._role_authorized,
            "approval_present": self._approval_present,
            "approval_binding": self._approval_binding,
            "purpose_match": self._purpose_match,
            "plan_tool_match": self._plan_tool_match,
            "instruction_source_authorized": (
                self._instruction_source_authorized
            ),
            "cross_step_purpose_consistent": (
                self._cross_step_purpose_consistent
            ),
            "data_access_scope_allowed": (
                self._data_access_scope_allowed
            ),
            "history_execution_limit": (
                self._history_execution_limit
            ),
            "data_classification_allowed": (
                self._data_classification_allowed
            ),
        }

    def available_predicates(self) -> tuple[str, ...]:
        """Return supported predicate types in deterministic order."""

        return tuple(sorted(self._handlers))

    def evaluate(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime | None = None,
    ) -> PredicateResult:
        """Evaluate one norm predicate against context and proposal."""

        predicate_type = norm.predicate.get("type")

        if not isinstance(predicate_type, str) or not predicate_type:
            raise PredicateEvaluationError(
                f"norm {norm.norm_id} has no valid predicate type"
            )

        handler = self._handlers.get(predicate_type)

        if handler is None:
            raise PredicateEvaluationError(
                f"unsupported predicate type: {predicate_type}"
            )

        evaluation_time = evaluated_at or utc_now()

        if (
            evaluation_time.tzinfo is None
            or evaluation_time.utcoffset() is None
        ):
            raise PredicateEvaluationError(
                "evaluated_at must be timezone-aware"
            )

        return handler(
            norm,
            context,
            proposal,
            evaluation_time,
        )

    def _tool_authorized(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        del context, evaluated_at

        raw_allowed = norm.predicate.get(
            "allowed_tools",
            norm.scope.get("tools", []),
        )
        allowed_tools = _string_list(raw_allowed, "allowed_tools")
        satisfied = proposal.tool in allowed_tools

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="tool_authorized",
            satisfied=satisfied,
            observed=proposal.tool,
            expected=allowed_tools,
            reason=(
                f"tool {proposal.tool} is authorized"
                if satisfied
                else f"tool {proposal.tool} is outside the authorized set"
            ),
        )

    def _role_authorized(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        del proposal, evaluated_at

        raw_allowed = norm.predicate.get(
            "allowed_roles",
            norm.scope.get("roles", []),
        )
        allowed_roles = _string_list(raw_allowed, "allowed_roles")
        satisfied = context.role in allowed_roles

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="role_authorized",
            satisfied=satisfied,
            observed=context.role,
            expected=allowed_roles,
            reason=(
                f"role {context.role} is authorized"
                if satisfied
                else f"role {context.role} is outside the authorized set"
            ),
        )

    def _approval_present(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        del proposal

        approval = context.approval

        if approval is None:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="approval_present",
                satisfied=False,
                observed=None,
                expected="a valid approval",
                reason="the governance context contains no approval",
            )

        subject_matches = approval.subject == context.actor
        temporally_valid = (
            approval.issued_at <= evaluated_at < approval.expires_at
        )
        satisfied = subject_matches and temporally_valid

        reasons: list[str] = []

        if not subject_matches:
            reasons.append(
                f"approval subject {approval.subject} differs from "
                f"actor {context.actor}"
            )

        if not temporally_valid:
            reasons.append("approval is outside its validity period")

        if satisfied:
            reasons.append("a valid approval is present")

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="approval_present",
            satisfied=satisfied,
            observed={
                "approval_id": approval.approval_id,
                "subject": approval.subject,
                "issued_at": approval.issued_at.isoformat(),
                "expires_at": approval.expires_at.isoformat(),
            },
            expected={
                "subject": context.actor,
                "valid_at": evaluated_at.isoformat(),
            },
            reason="; ".join(reasons),
        )

    def _approval_binding(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        approval = context.approval

        if approval is None:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="approval_binding",
                satisfied=False,
                observed=None,
                expected="an approval bound to the proposal",
                reason="the governance context contains no approval",
            )

        observed_object = _proposal_object_id(norm, proposal)

        comparisons: dict[str, dict[str, Any]] = {
            "subject": {
                "observed": context.actor,
                "expected": approval.subject,
                "matches": context.actor == approval.subject,
            },
            "action": {
                "observed": proposal.tool,
                "expected": approval.action,
                "matches": proposal.tool == approval.action,
            },
            "object": {
                "observed": observed_object,
                "expected": approval.object_id,
                "matches": observed_object == approval.object_id,
            },
            "validity": {
                "observed": evaluated_at.isoformat(),
                "expected": {
                    "issued_at": approval.issued_at.isoformat(),
                    "expires_at": approval.expires_at.isoformat(),
                },
                "matches": (
                    approval.issued_at
                    <= evaluated_at
                    < approval.expires_at
                ),
            },
        }

        for constraint_name, expected_value in approval.constraints.items():
            observed_value = _proposal_constraint_value(
                proposal,
                constraint_name,
            )

            comparisons[f"constraint:{constraint_name}"] = {
                "observed": observed_value,
                "expected": expected_value,
                "matches": observed_value == expected_value,
            }

        mismatches = [
            name
            for name, comparison in comparisons.items()
            if comparison["matches"] is False
        ]
        satisfied = not mismatches

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="approval_binding",
            satisfied=satisfied,
            observed=comparisons,
            expected="all approval fields match the proposal",
            reason=(
                "approval is bound to the complete proposal"
                if satisfied
                else "approval mismatch in: " + ", ".join(mismatches)
            ),
        )

    def _purpose_match(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        del evaluated_at

        argument_name = norm.predicate.get(
            "purpose_argument",
            "purpose",
        )

        if not isinstance(argument_name, str):
            raise PredicateEvaluationError(
                "purpose_argument must be a string"
            )

        observed = proposal.declared_purpose

        if observed is None:
            observed = proposal.arguments.get(argument_name)

        if observed is None:
            observed = proposal.arguments.get("data_use_purpose")
        
#        observed = proposal.arguments.get(argument_name)
#
#        if observed is None:
#            observed = proposal.arguments.get("data_use_purpose")
        satisfied = observed == context.authorized_purpose

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="purpose_match",
            satisfied=satisfied,
            observed=observed,
            expected=context.authorized_purpose,
            reason=(
                "proposed purpose matches the authorized purpose"
                if satisfied
                else "proposed purpose diverges from the authorized purpose"
            ),
        )

    def _plan_tool_match(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        del evaluated_at

        normalized_tool = _normalize_token(proposal.tool)
        normalized_plan = [
            _normalize_token(step)
            for step in context.declared_plan
        ]

        satisfied = any(
            normalized_tool == step or normalized_tool in step
            for step in normalized_plan
        )

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="plan_tool_match",
            satisfied=satisfied,
            observed=proposal.tool,
            expected=context.declared_plan,
            reason=(
                "proposed tool is represented in the declared plan"
                if satisfied
                else "proposed tool diverges from the declared plan"
            ),
        )

    def _instruction_source_authorized(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        """Check whether action instructions came from trusted sources."""

        predicate = norm.predicate

        proposal_source_argument = predicate.get(
            "proposal_source_argument",
            "instruction_source_ids",
        )

        if (
            not isinstance(proposal_source_argument, str)
            or not proposal_source_argument.strip()
        ):
            raise PredicateEvaluationError(
                "instruction_source_authorized "
                "proposal_source_argument must be "
                "a non-empty string"
            )

        trusted_sources_field = predicate.get(
            "trusted_sources_field",
            "trusted_instruction_source_ids",
        )

        if (
            not isinstance(trusted_sources_field, str)
            or not trusted_sources_field.strip()
        ):
            raise PredicateEvaluationError(
                "instruction_source_authorized "
                "trusted_sources_field must be "
                "a non-empty string"
            )

        trusted_raw = context.data_context.get(
            trusted_sources_field
        )

        trusted_sources_valid = (
            isinstance(trusted_raw, list)
            and bool(trusted_raw)
            and all(
                isinstance(source_id, str)
                and bool(source_id.strip())
                for source_id in trusted_raw
            )
        )

        trusted_instruction_source_ids = (
            list(trusted_raw)
            if trusted_sources_valid
            else None
        )

        expected = {
            "trusted_sources_field": trusted_sources_field,
            "trusted_instruction_source_ids": (
                trusted_instruction_source_ids
            ),
        }

        declared_raw = (
            proposal.instruction_source_ids
            if proposal_source_argument
            == "instruction_source_ids"
            else proposal.arguments.get(
                proposal_source_argument
            )
        )

        if declared_raw is None:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "instruction_source_authorized"
                ),
                satisfied=False,
                observed={
                    "argument": proposal_source_argument,
                    "declared_instruction_source_ids": None,
                    "unauthorized_instruction_source_ids": [],
                    "duplicate_instruction_source_ids": [],
                },
                expected=expected,
                reason=(
                    "proposal instruction source argument "
                    f"is missing: {proposal_source_argument}"
                ),
            )

        declared_valid = (
            isinstance(declared_raw, list)
            and bool(declared_raw)
            and all(
                isinstance(source_id, str)
                and bool(source_id.strip())
                for source_id in declared_raw
            )
        )

        if not declared_valid:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "instruction_source_authorized"
                ),
                satisfied=False,
                observed={
                    "argument": proposal_source_argument,
                    "declared_instruction_source_ids": (
                        declared_raw
                    ),
                    "unauthorized_instruction_source_ids": [],
                    "duplicate_instruction_source_ids": [],
                },
                expected=expected,
                reason=(
                    "proposal instruction source argument "
                    "must be a non-empty list of strings"
                ),
            )

        declared_instruction_source_ids = list(
            declared_raw
        )

        seen: set[str] = set()
        duplicate_instruction_source_ids: list[str] = []

        for source_id in declared_instruction_source_ids:
            if (
                source_id in seen
                and source_id
                not in duplicate_instruction_source_ids
            ):
                duplicate_instruction_source_ids.append(
                    source_id
                )

            seen.add(source_id)

        trusted_set = (
            set(trusted_instruction_source_ids)
            if trusted_instruction_source_ids is not None
            else set()
        )

        unauthorized_instruction_source_ids = [
            source_id
            for source_id in declared_instruction_source_ids
            if source_id not in trusted_set
        ]

        observed = {
            "argument": proposal_source_argument,
            "declared_instruction_source_ids": (
                declared_instruction_source_ids
            ),
            "unauthorized_instruction_source_ids": (
                unauthorized_instruction_source_ids
            ),
            "duplicate_instruction_source_ids": (
                duplicate_instruction_source_ids
            ),
        }

        if trusted_instruction_source_ids is None:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "instruction_source_authorized"
                ),
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "trusted instruction source context "
                    f"is missing or invalid: {trusted_sources_field}"
                ),
            )

        if duplicate_instruction_source_ids:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "instruction_source_authorized"
                ),
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "declared instruction sources contain "
                    "duplicate values"
                ),
            )

        if unauthorized_instruction_source_ids:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "instruction_source_authorized"
                ),
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "one or more instruction sources are "
                    "not authorized"
                ),
            )

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type=(
                "instruction_source_authorized"
            ),
            satisfied=True,
            observed=observed,
            expected=expected,
            reason=(
                "all declared instruction sources are "
                "authorized"
            ),
        )

    def _cross_step_purpose_consistent(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        """Check reused data against prior acquisition purposes."""

        predicate = norm.predicate

        proposal_object_argument = predicate.get(
            "proposal_object_argument",
            "source_document_ids",
        )

        if (
            not isinstance(proposal_object_argument, str)
            or not proposal_object_argument.strip()
        ):
            raise PredicateEvaluationError(
                "cross_step_purpose_consistent "
                "proposal_object_argument must be "
                "a non-empty string"
            )

        history_object_field = predicate.get(
            "history_object_field",
            "accessed_object_ids",
        )

        if (
            not isinstance(history_object_field, str)
            or not history_object_field.strip()
        ):
            raise PredicateEvaluationError(
                "cross_step_purpose_consistent "
                "history_object_field must be "
                "a non-empty string"
            )

        history_purpose_field = predicate.get(
            "history_purpose_field",
            "acquired_under_purpose",
        )

        if (
            not isinstance(history_purpose_field, str)
            or not history_purpose_field.strip()
        ):
            raise PredicateEvaluationError(
                "cross_step_purpose_consistent "
                "history_purpose_field must be "
                "a non-empty string"
            )

        current_purpose = proposal.declared_purpose

        expected = {
            "proposal_object_argument": (
                proposal_object_argument
            ),
            "history_object_field": history_object_field,
            "history_purpose_field": (
                history_purpose_field
            ),
            "required_purpose": current_purpose,
        }

        requested_raw = proposal.arguments.get(
            proposal_object_argument
        )

        if requested_raw is None:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "cross_step_purpose_consistent"
                ),
                satisfied=False,
                observed={
                    "current_purpose": current_purpose,
                    "reused_object_ids": None,
                    "matched_provenance": [],
                    "missing_provenance_object_ids": [],
                    "purpose_mismatch_execution_ids": [],
                },
                expected=expected,
                reason=(
                    "proposal object-reuse argument is missing: "
                    f"{proposal_object_argument}"
                ),
            )

        requested_valid = (
            isinstance(requested_raw, list)
            and bool(requested_raw)
            and all(
                isinstance(object_id, str)
                and bool(object_id.strip())
                for object_id in requested_raw
            )
        )

        if not requested_valid:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "cross_step_purpose_consistent"
                ),
                satisfied=False,
                observed={
                    "current_purpose": current_purpose,
                    "reused_object_ids": requested_raw,
                    "matched_provenance": [],
                    "missing_provenance_object_ids": [],
                    "purpose_mismatch_execution_ids": [],
                },
                expected=expected,
                reason=(
                    "proposal object-reuse argument must be "
                    "a non-empty list of strings"
                ),
            )

        reused_object_ids = list(requested_raw)

        if len(set(reused_object_ids)) != len(
            reused_object_ids
        ):
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "cross_step_purpose_consistent"
                ),
                satisfied=False,
                observed={
                    "current_purpose": current_purpose,
                    "reused_object_ids": reused_object_ids,
                    "matched_provenance": [],
                    "missing_provenance_object_ids": [],
                    "purpose_mismatch_execution_ids": [],
                },
                expected=expected,
                reason=(
                    "reused object identifiers contain "
                    "duplicate values"
                ),
            )

        if (
            not isinstance(current_purpose, str)
            or not current_purpose.strip()
        ):
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "cross_step_purpose_consistent"
                ),
                satisfied=False,
                observed={
                    "current_purpose": current_purpose,
                    "reused_object_ids": reused_object_ids,
                    "matched_provenance": [],
                    "missing_provenance_object_ids": [],
                    "purpose_mismatch_execution_ids": [],
                },
                expected=expected,
                reason=(
                    "current proposal purpose is missing "
                    "or invalid"
                ),
            )

        matched_provenance: list[dict[str, Any]] = []
        provenance_object_ids: set[str] = set()
        purpose_mismatch_execution_ids: list[str] = []

        reused_set = set(reused_object_ids)

        for entry in context.history:
            if not isinstance(entry, dict):
                continue

            if entry.get("executed") is not True:
                continue

            execution_id = entry.get("execution_id")
            history_objects = entry.get(
                history_object_field
            )
            acquired_purpose = entry.get(
                history_purpose_field
            )

            valid_entry = (
                isinstance(execution_id, str)
                and bool(execution_id.strip())
                and isinstance(history_objects, list)
                and bool(history_objects)
                and all(
                    isinstance(object_id, str)
                    and bool(object_id.strip())
                    for object_id in history_objects
                )
                and isinstance(acquired_purpose, str)
                and bool(acquired_purpose.strip())
            )

            if not valid_entry:
                continue

            relevant_object_ids = [
                object_id
                for object_id in reused_object_ids
                if (
                    object_id in reused_set
                    and object_id in history_objects
                )
            ]

            if not relevant_object_ids:
                continue

            matched_provenance.append(
                {
                    "execution_id": execution_id,
                    "object_ids": relevant_object_ids,
                    "acquired_under_purpose": (
                        acquired_purpose
                    ),
                }
            )

            provenance_object_ids.update(
                relevant_object_ids
            )

            if (
                acquired_purpose != current_purpose
                and execution_id
                not in purpose_mismatch_execution_ids
            ):
                purpose_mismatch_execution_ids.append(
                    execution_id
                )

        missing_provenance_object_ids = [
            object_id
            for object_id in reused_object_ids
            if object_id not in provenance_object_ids
        ]

        observed = {
            "current_purpose": current_purpose,
            "reused_object_ids": reused_object_ids,
            "matched_provenance": matched_provenance,
            "missing_provenance_object_ids": (
                missing_provenance_object_ids
            ),
            "purpose_mismatch_execution_ids": (
                purpose_mismatch_execution_ids
            ),
        }

        if missing_provenance_object_ids:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "cross_step_purpose_consistent"
                ),
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "one or more reused objects lack "
                    "completed acquisition provenance"
                ),
            )

        if purpose_mismatch_execution_ids:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type=(
                    "cross_step_purpose_consistent"
                ),
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "reused data was acquired under "
                    "a different purpose"
                ),
            )

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type=(
                "cross_step_purpose_consistent"
            ),
            satisfied=True,
            observed=observed,
            expected=expected,
            reason=(
                "reused data remains consistent with "
                "its prior acquisition purpose"
            ),
        )

    def _data_access_scope_allowed(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        """Check requested objects against task scope and volume."""

        predicate = norm.predicate

        object_argument = predicate.get(
            "object_argument",
            "document_ids",
        )

        if (
            not isinstance(object_argument, str)
            or not object_argument.strip()
        ):
            raise PredicateEvaluationError(
                "data_access_scope_allowed "
                "object_argument must be a non-empty string"
            )

        required_scope_field = predicate.get(
            "required_scope_field",
            "required_document_ids",
        )

        if (
            not isinstance(required_scope_field, str)
            or not required_scope_field.strip()
        ):
            raise PredicateEvaluationError(
                "data_access_scope_allowed "
                "required_scope_field must be a non-empty string"
            )

        max_objects = predicate.get("max_objects")

        if (
            isinstance(max_objects, bool)
            or not isinstance(max_objects, int)
            or max_objects < 1
        ):
            raise PredicateEvaluationError(
                "data_access_scope_allowed "
                "max_objects must be a positive integer"
            )

        required_raw = context.data_context.get(
            required_scope_field
        )

        required_scope_valid = (
            isinstance(required_raw, list)
            and all(
                isinstance(object_id, str)
                and bool(object_id.strip())
                for object_id in required_raw
            )
        )

        required_object_ids = (
            list(required_raw)
            if required_scope_valid
            else None
        )

        expected = {
            "required_scope_field": required_scope_field,
            "required_object_ids": required_object_ids,
            "max_objects": max_objects,
        }

        requested_raw = proposal.arguments.get(
            object_argument
        )

        if requested_raw is None:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="data_access_scope_allowed",
                satisfied=False,
                observed={
                    "argument": object_argument,
                    "requested_object_ids": None,
                    "requested_count": 0,
                    "outside_required_scope": [],
                    "duplicate_object_ids": [],
                },
                expected=expected,
                reason=(
                    f"proposal argument {object_argument} "
                    "is missing"
                ),
            )

        requested_scope_valid = (
            isinstance(requested_raw, list)
            and all(
                isinstance(object_id, str)
                and bool(object_id.strip())
                for object_id in requested_raw
            )
        )

        if not requested_scope_valid:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="data_access_scope_allowed",
                satisfied=False,
                observed={
                    "argument": object_argument,
                    "requested_object_ids": requested_raw,
                    "requested_count": (
                        len(requested_raw)
                        if isinstance(requested_raw, list)
                        else 0
                    ),
                    "outside_required_scope": [],
                    "duplicate_object_ids": [],
                },
                expected=expected,
                reason=(
                    f"proposal argument {object_argument} "
                    "must be a list of non-empty strings"
                ),
            )

        requested_object_ids = list(requested_raw)

        seen: set[str] = set()
        duplicate_object_ids: list[str] = []

        for object_id in requested_object_ids:
            if (
                object_id in seen
                and object_id not in duplicate_object_ids
            ):
                duplicate_object_ids.append(object_id)

            seen.add(object_id)

        required_set = (
            set(required_object_ids)
            if required_object_ids is not None
            else set()
        )

        outside_required_scope = [
            object_id
            for object_id in requested_object_ids
            if object_id not in required_set
        ]

        observed = {
            "argument": object_argument,
            "requested_object_ids": requested_object_ids,
            "requested_count": len(requested_object_ids),
            "outside_required_scope": (
                outside_required_scope
            ),
            "duplicate_object_ids": duplicate_object_ids,
        }

        if required_object_ids is None:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="data_access_scope_allowed",
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "task-required scope is missing or "
                    f"invalid: {required_scope_field}"
                ),
            )

        if duplicate_object_ids:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="data_access_scope_allowed",
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "requested object identifiers contain "
                    "duplicate values"
                ),
            )

        if len(requested_object_ids) > max_objects:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="data_access_scope_allowed",
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "requested object count exceeds "
                    "the configured maximum"
                ),
            )

        if outside_required_scope:
            return PredicateResult(
                norm_id=norm.norm_id,
                predicate_type="data_access_scope_allowed",
                satisfied=False,
                observed=observed,
                expected=expected,
                reason=(
                    "requested objects fall outside "
                    "the task-required scope"
                ),
            )

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="data_access_scope_allowed",
            satisfied=True,
            observed=observed,
            expected=expected,
            reason=(
                "requested objects remain within "
                "the task-required scope and maximum"
            ),
        )

    def _history_execution_limit(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        """Evaluate a cumulative execution limit for one tool."""

        del evaluated_at

        raw_max_total = norm.predicate.get(
            "max_total_executions"
        )

        if (
            isinstance(raw_max_total, bool)
            or not isinstance(raw_max_total, int)
            or raw_max_total < 1
        ):
            raise PredicateEvaluationError(
                "max_total_executions must be "
                "a positive integer"
            )

        raw_tool = norm.predicate.get(
            "tool",
            proposal.tool,
        )

        if (
            not isinstance(raw_tool, str)
            or not raw_tool.strip()
        ):
            raise PredicateEvaluationError(
                "history execution tool must be "
                "a non-empty string"
            )

        target_tool = raw_tool.strip()

        prior_executions = sum(
            1
            for entry in context.history
            if isinstance(entry, dict)
            and entry.get("tool") == target_tool
            and entry.get("executed") is True
        )

        proposed_total = prior_executions + 1

        satisfied = (
            proposed_total <= raw_max_total
        )

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="history_execution_limit",
            satisfied=satisfied,
            observed={
                "tool": target_tool,
                "prior_executions": prior_executions,
                "proposed_total": proposed_total,
            },
            expected={
                "max_total_executions": raw_max_total,
            },
            reason=(
                "proposed execution remains within "
                "the cumulative history limit"
                if satisfied
                else
                "proposed execution exceeds "
                "the cumulative history limit"
            ),
        )

    def _data_classification_allowed(
        self,
        norm: RuntimeNorm,
        context: GovernanceContext,
        proposal: ActionProposal,
        evaluated_at: datetime,
    ) -> PredicateResult:
        del context, evaluated_at

        raw_allowed = norm.predicate.get(
            "allowed_classifications",
            [],
        )
        allowed = _string_list(
            raw_allowed,
            "allowed_classifications",
        )
        observed = proposal.arguments.get(
            "data_classification",
            "public",
        )
        satisfied = observed in allowed

        return PredicateResult(
            norm_id=norm.norm_id,
            predicate_type="data_classification_allowed",
            satisfied=satisfied,
            observed=observed,
            expected=allowed,
            reason=(
                f"data classification {observed} is authorized"
                if satisfied
                else f"data classification {observed} is restricted"
            ),
        )
