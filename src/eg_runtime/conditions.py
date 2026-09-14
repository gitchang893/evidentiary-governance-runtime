from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from eg_runtime.evaluator import NormEvaluator
from eg_runtime.evidence import EvidenceRecorder
from eg_runtime.models import (
    ActionProposal,
    DecisionRecord,
    Disposition,
    FrozenModel,
    GovernanceContext,
    RuntimeNorm,
    ToolExecutionRecord,
    utc_now,
)
from eg_runtime.runtime import GovernanceMiddleware
from eg_runtime.tools import ToolError, ToolRegistry


class GovernanceCondition(StrEnum):
    """Comparative governance conditions used in the evaluation."""

    C0_LOGGING = "c0_logging"
    C1_POST_HOC = "c1_post_hoc"
    C2_ACTION_LEVEL = "c2_action_level"
    C3_ORGANIZATIONAL = "c3_organizational"


class DecisionPhase(StrEnum):
    """Point at which governance evaluation occurs."""

    NONE = "none"
    PRE_EXECUTION = "pre_execution"
    POST_EXECUTION = "post_execution"


ACTION_LEVEL_PREDICATES = frozenset(
    {
        "tool_authorized",
        "role_authorized",
        "approval_present",
        "data_classification_allowed",
        "data_access_scope_allowed",
    }
)


class ConditionRunResult(FrozenModel):
    """Outcome of replaying one proposal under one condition."""

    run_id: str = Field(min_length=1)
    condition: GovernanceCondition
    decision_phase: DecisionPhase
    decision: DecisionRecord | None
    execution: ToolExecutionRecord
    operational_outcome: str = Field(min_length=1)
    violation_detected: bool
    execution_prevented: bool
    evidence_event_count: int = Field(ge=1)
    evidence_final_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


def select_action_level_norms(
    norms: Iterable[RuntimeNorm],
) -> list[RuntimeNorm]:
    """Select norms available to the action-level baseline."""

    selected: list[RuntimeNorm] = []

    for norm in norms:
        predicate_type = norm.predicate.get("type")

        if predicate_type in ACTION_LEVEL_PREDICATES:
            selected.append(norm)

    return selected


class ComparativeConditionRunner:
    """Replay a fixed proposal under C0, C1, C2, or C3."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        evaluator: NormEvaluator | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._tool_registry = tool_registry
        self._evaluator = (
            evaluator
            if evaluator is not None
            else NormEvaluator()
        )
        self._clock = clock

    def run(
        self,
        *,
        condition: GovernanceCondition,
        context: GovernanceContext,
        proposal: ActionProposal,
        norms: Iterable[RuntimeNorm],
        recorder: EvidenceRecorder,
        run_metadata: dict[str, Any] | None = None,
    ) -> ConditionRunResult:
        """Execute one comparative condition."""

        norm_list = list(norms)

        if condition is GovernanceCondition.C0_LOGGING:
            return self._run_without_precontrol(
                condition=condition,
                context=context,
                proposal=proposal,
                norms=norm_list,
                recorder=recorder,
                run_metadata=run_metadata,
                post_hoc=False,
            )

        if condition is GovernanceCondition.C1_POST_HOC:
            return self._run_without_precontrol(
                condition=condition,
                context=context,
                proposal=proposal,
                norms=norm_list,
                recorder=recorder,
                run_metadata=run_metadata,
                post_hoc=True,
            )

        if condition is GovernanceCondition.C2_ACTION_LEVEL:
            return self._run_pre_execution(
                condition=condition,
                context=context,
                proposal=proposal,
                norms=select_action_level_norms(norm_list),
                recorder=recorder,
                run_metadata=run_metadata,
            )

        if condition is GovernanceCondition.C3_ORGANIZATIONAL:
            return self._run_pre_execution(
                condition=condition,
                context=context,
                proposal=proposal,
                norms=norm_list,
                recorder=recorder,
                run_metadata=run_metadata,
            )

        raise ValueError(
            f"unsupported governance condition: {condition}"
        )

    def _run_pre_execution(
        self,
        *,
        condition: GovernanceCondition,
        context: GovernanceContext,
        proposal: ActionProposal,
        norms: list[RuntimeNorm],
        recorder: EvidenceRecorder,
        run_metadata: dict[str, Any] | None,
    ) -> ConditionRunResult:
        """Run C2 or C3 through the capability-mediating middleware."""

        metadata = dict(run_metadata or {})
        metadata.update(
            {
                "condition": condition.value,
                "decision_phase": (
                    DecisionPhase.PRE_EXECUTION.value
                ),
            }
        )

        middleware = GovernanceMiddleware(
            self._tool_registry,
            evaluator=self._evaluator,
            clock=self._clock,
        )

        runtime_result = middleware.process(
            context=context,
            proposal=proposal,
            norms=norms,
            recorder=recorder,
            run_metadata=metadata,
        )

        violation_detected = (
            runtime_result.decision.disposition
            is not Disposition.ALLOW
        )

        execution_prevented = (
            violation_detected
            and runtime_result.execution.executed is False
        )

        return ConditionRunResult(
            run_id=runtime_result.run_id,
            condition=condition,
            decision_phase=DecisionPhase.PRE_EXECUTION,
            decision=runtime_result.decision,
            execution=runtime_result.execution,
            operational_outcome=runtime_result.operational_outcome,
            violation_detected=violation_detected,
            execution_prevented=execution_prevented,
            evidence_event_count=(
                runtime_result.evidence_event_count
            ),
            evidence_final_hash=(
                runtime_result.evidence_final_hash
            ),
        )

    def _run_without_precontrol(
        self,
        *,
        condition: GovernanceCondition,
        context: GovernanceContext,
        proposal: ActionProposal,
        norms: list[RuntimeNorm],
        recorder: EvidenceRecorder,
        run_metadata: dict[str, Any] | None,
        post_hoc: bool,
    ) -> ConditionRunResult:
        """Run C0 or C1 without pre-execution capability mediation."""

        decision_phase_value = (
            DecisionPhase.POST_EXECUTION.value
            if post_hoc
            else DecisionPhase.NONE.value
        )

        metadata = dict(run_metadata or {})
        metadata.update(
            {
                "condition": condition.value,
                "decision_phase": decision_phase_value,
            }
        )

        recorder.start(
            {
                "task_id": context.task_id,
                "proposal_id": proposal.proposal_id,
                "metadata": metadata,
            },
            timestamp=self._now(),
        )

        if post_hoc:
            recorder.append(
                "context_logged",
                {
                    "context": context.model_dump(mode="json"),
                },
                timestamp=self._now(),
            )

        recorder.append(
            "proposal_logged",
            {
                "proposal": proposal.model_dump(mode="json"),
            },
            timestamp=self._now(),
        )

        execution, operational_outcome = self._execute_proposal(
            proposal=proposal,
            recorder=recorder,
        )

        decision: DecisionRecord | None = None
        violation_detected = False
        decision_phase = DecisionPhase.NONE

        if post_hoc:
            decision = self._evaluator.evaluate(
                norms,
                context,
                proposal,
                evaluated_at=self._now(),
            )

            violation_detected = (
                decision.disposition is not Disposition.ALLOW
            )

            decision_phase = DecisionPhase.POST_EXECUTION

            recorder.append(
                "post_hoc_governance_decision",
                {
                    "decision": decision.model_dump(mode="json"),
                    "execution_already_occurred": execution.executed,
                },
                timestamp=self._now(),
            )

        recorder.complete(
            {
                "operational_outcome": operational_outcome,
                "executed": execution.executed,
                "side_effect": execution.side_effect,
                "violation_detected": violation_detected,
            },
            timestamp=self._now(),
        )

        return ConditionRunResult(
            run_id=recorder.run_id,
            condition=condition,
            decision_phase=decision_phase,
            decision=decision,
            execution=execution,
            operational_outcome=operational_outcome,
            violation_detected=violation_detected,
            execution_prevented=False,
            evidence_event_count=recorder.event_count,
            evidence_final_hash=recorder.final_hash,
        )

    def _execute_proposal(
        self,
        *,
        proposal: ActionProposal,
        recorder: EvidenceRecorder,
    ) -> tuple[ToolExecutionRecord, str]:
        """Execute a proposal without pre-execution governance."""

        started_at = self._now()

        recorder.append(
            "tool_invoked",
            {
                "proposal_id": proposal.proposal_id,
                "tool": proposal.tool,
                "arguments": proposal.arguments,
            },
            timestamp=started_at,
        )

        try:
            outcome = self._tool_registry.execute(
                proposal.tool,
                proposal.arguments,
            )

        except ToolError as exc:
            completed_at = self._now()

            execution = ToolExecutionRecord(
                execution_id=self._execution_id(
                    recorder.run_id,
                    proposal.proposal_id,
                ),
                proposal_id=proposal.proposal_id,
                tool=proposal.tool,
                executed=True,
                side_effect=False,
                arguments=proposal.arguments,
                result={},
                error=str(exc),
                started_at=started_at,
                completed_at=completed_at,
            )

            recorder.append(
                "tool_failed",
                {
                    "execution": execution.model_dump(mode="json"),
                },
                timestamp=completed_at,
            )

            return execution, "execution_error"

        completed_at = self._now()

        execution = ToolExecutionRecord(
            execution_id=self._execution_id(
                recorder.run_id,
                proposal.proposal_id,
            ),
            proposal_id=proposal.proposal_id,
            tool=proposal.tool,
            executed=True,
            side_effect=outcome.side_effect,
            arguments=proposal.arguments,
            result={
                "status": outcome.status,
                "output": outcome.output,
            },
            error=None,
            started_at=started_at,
            completed_at=completed_at,
        )

        recorder.append(
            "tool_completed",
            {
                "execution": execution.model_dump(mode="json"),
            },
            timestamp=completed_at,
        )

        return execution, "executed"

    def _now(self) -> datetime:
        """Return a timezone-aware runtime timestamp."""

        value = self._clock()

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "condition-runner clock must be timezone-aware"
            )

        return value

    @staticmethod
    def _execution_id(
        run_id: str,
        proposal_id: str,
    ) -> str:
        """Create a stable execution identifier."""

        return f"{run_id}:execution:{proposal_id}"
