from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable

from eg_runtime.models import (
    ActionProposal,
    DecisionRecord,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
    utc_now,
)
from eg_runtime.predicates import PredicateEngine


class NormEvaluationError(RuntimeError):
    """Raised when norm selection or conflict resolution fails."""


_DISPOSITION_RANK = {
    Disposition.ALLOW: 0,
    Disposition.ESCALATE: 1,
    Disposition.BLOCK: 2,
}

_PRESERVATION_RANK = {
    Preservation.MINIMAL: 0,
    Preservation.FULL: 1,
    Preservation.INCIDENT: 2,
}


def _require_string_list(
    value: Any,
    field_name: str,
) -> list[str]:
    """Validate a scope or trigger value containing strings."""

    if not isinstance(value, list):
        raise NormEvaluationError(
            f"{field_name} must be a list of strings"
        )

    if not all(isinstance(item, str) for item in value):
        raise NormEvaluationError(
            f"{field_name} must contain only strings"
        )

    return value


def _proposal_resource_id(
    proposal: ActionProposal,
) -> str | None:
    """Extract the principal resource identifier from a proposal."""

    for field_name in (
        "record_id",
        "document_id",
        "report_id",
        "object_id",
        "request_id",
    ):
        value = proposal.arguments.get(field_name)

        if value is not None:
            return str(value)

    return None


def _scope_matches(
    norm: RuntimeNorm,
    context: GovernanceContext,
    proposal: ActionProposal,
) -> bool:
    """Determine whether a norm scope covers the current step."""

    observations: dict[str, str | None] = {
        "actors": context.actor,
        "roles": context.role,
        "tools": proposal.tool,
        "purposes": context.authorized_purpose,
        "resources": _proposal_resource_id(proposal),
        "policy_versions": context.policy_version,
        "data_classifications": str(
            proposal.arguments.get(
                "data_classification",
                context.data_context.get("classification", "public"),
            )
        ),
    }

    for scope_name, observed in observations.items():
        raw_allowed = norm.scope.get(scope_name)

        if raw_allowed is None:
            continue

        allowed = _require_string_list(
            raw_allowed,
            f"scope.{scope_name}",
        )

        if observed not in allowed:
            return False

    return True


def _trigger_matches(
    norm: RuntimeNorm,
    proposal: ActionProposal,
    trigger_event: str,
) -> bool:
    """Determine whether a norm trigger matches the runtime event."""

    configured_event = norm.trigger.get("event")

    if configured_event is not None:
        if not isinstance(configured_event, str):
            raise NormEvaluationError(
                f"norm {norm.norm_id} has an invalid trigger event"
            )

        if configured_event != trigger_event:
            return False

    raw_tools = norm.trigger.get("tools")

    if raw_tools is not None:
        tools = _require_string_list(
            raw_tools,
            "trigger.tools",
        )

        if proposal.tool not in tools:
            return False

    return True


def _specificity(norm: RuntimeNorm) -> int:
    """Measure the number of populated scope and trigger dimensions."""

    populated_scope = sum(
        1
        for value in norm.scope.values()
        if value not in (None, "", [], {})
    )

    populated_trigger = sum(
        1
        for value in norm.trigger.values()
        if value not in (None, "", [], {})
    )

    return populated_scope + populated_trigger


def _ordered_norms(
    norms: Iterable[RuntimeNorm],
) -> list[RuntimeNorm]:
    """Order norms by specificity, priority, version, and identifier."""

    ordered = sorted(
        norms,
        key=lambda norm: norm.norm_id,
    )

    ordered = sorted(
        ordered,
        key=lambda norm: norm.version,
        reverse=True,
    )

    ordered = sorted(
        ordered,
        key=lambda norm: norm.priority,
        reverse=True,
    )

    ordered = sorted(
        ordered,
        key=_specificity,
        reverse=True,
    )

    return ordered


def _make_decision_id(
    applicable_norms: list[RuntimeNorm],
    context: GovernanceContext,
    proposal: ActionProposal,
    evaluated_at: datetime,
) -> str:
    """Generate a reproducible decision identifier for fixed inputs."""

    payload = {
        "norms": [
            {
                "norm_id": norm.norm_id,
                "version": norm.version,
            }
            for norm in applicable_norms
        ],
        "context": context.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "evaluated_at": evaluated_at.isoformat(),
    }

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    digest = hashlib.sha256(canonical).hexdigest()

    return f"decision-{digest[:16]}"


class NormEvaluator:
    """Select applicable norms and derive a runtime decision."""

    def __init__(
        self,
        predicate_engine: PredicateEngine | None = None,
        *,
        default_disposition: Disposition = Disposition.ALLOW,
        default_preservation: Preservation = Preservation.MINIMAL,
    ) -> None:
        self._predicate_engine = (
            predicate_engine
            if predicate_engine is not None
            else PredicateEngine()
        )

        self._default_disposition = default_disposition
        self._default_preservation = default_preservation

    def select_applicable_norms(
        self,
        norms: Iterable[RuntimeNorm],
        context: GovernanceContext,
        proposal: ActionProposal,
        *,
        trigger_event: str = "tool_proposed",
    ) -> list[RuntimeNorm]:
        """Select enabled norms whose scope and trigger match."""

        applicable = [
            norm
            for norm in norms
            if norm.enabled
            and _trigger_matches(
                norm,
                proposal,
                trigger_event,
            )
            and _scope_matches(
                norm,
                context,
                proposal,
            )
        ]

        return _ordered_norms(applicable)

    def evaluate(
        self,
        norms: Iterable[RuntimeNorm],
        context: GovernanceContext,
        proposal: ActionProposal,
        *,
        evaluated_at: datetime | None = None,
        trigger_event: str = "tool_proposed",
    ) -> DecisionRecord:
        """Evaluate applicable norms and resolve the final outcome."""

        evaluation_time = evaluated_at or utc_now()

        if (
            evaluation_time.tzinfo is None
            or evaluation_time.utcoffset() is None
        ):
            raise NormEvaluationError(
                "evaluated_at must be timezone-aware"
            )

        applicable = self.select_applicable_norms(
            norms,
            context,
            proposal,
            trigger_event=trigger_event,
        )

        predicate_results = [
            self._predicate_engine.evaluate(
                norm,
                context,
                proposal,
                evaluation_time,
            )
            for norm in applicable
        ]

        failed = [
            (norm, result)
            for norm, result in zip(
                applicable,
                predicate_results,
                strict=True,
            )
            if result.satisfied is False
        ]

        if not failed:
            reasons = [
                (
                    "All applicable predicates were satisfied."
                    if applicable
                    else (
                        "No runtime norm applied; "
                        "the default disposition was used."
                    )
                )
            ]

            return DecisionRecord(
                decision_id=_make_decision_id(
                    applicable,
                    context,
                    proposal,
                    evaluation_time,
                ),
                disposition=self._default_disposition,
                preservation=self._default_preservation,
                applicable_norms=[
                    norm.norm_id
                    for norm in applicable
                ],
                predicate_results=predicate_results,
                reasons=reasons,
                evaluated_at=evaluation_time,
            )

        strongest_disposition_rank = max(
            _DISPOSITION_RANK[norm.disposition]
            for norm, _ in failed
        )

        governing_norm = next(
            norm
            for norm, _ in failed
            if _DISPOSITION_RANK[norm.disposition]
            == strongest_disposition_rank
        )

        strongest_preservation = max(
            (
                norm.preservation
                for norm, _ in failed
            ),
            key=lambda value: _PRESERVATION_RANK[value],
        )

        reasons = [
            f"{norm.norm_id}: {result.reason}"
            for norm, result in failed
        ]

        reasons.append(
            (
                f"Final disposition {governing_norm.disposition.value} "
                f"was selected under "
                f"block > escalate > allow; "
                f"governing norm: {governing_norm.norm_id}."
            )
        )

        reasons.append(
            (
                f"Preservation directive "
                f"{strongest_preservation.value} "
                f"was selected independently."
            )
        )

        return DecisionRecord(
            decision_id=_make_decision_id(
                applicable,
                context,
                proposal,
                evaluation_time,
            ),
            disposition=governing_norm.disposition,
            preservation=strongest_preservation,
            applicable_norms=[
                norm.norm_id
                for norm in applicable
            ],
            predicate_results=predicate_results,
            reasons=reasons,
            evaluated_at=evaluation_time,
        )


def evaluate(
    norms: Iterable[RuntimeNorm],
    context: GovernanceContext,
    proposal: ActionProposal,
    *,
    evaluated_at: datetime | None = None,
    trigger_event: str = "tool_proposed",
) -> DecisionRecord:
    """Evaluate norms through the default deterministic evaluator."""

    evaluator = NormEvaluator()

    return evaluator.evaluate(
        norms,
        context,
        proposal,
        evaluated_at=evaluated_at,
        trigger_event=trigger_event,
    )
