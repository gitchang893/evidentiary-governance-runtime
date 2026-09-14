from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
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
from eg_runtime.tools import (
    ToolError,
    ToolRegistry,
)


class RuntimeProcessingError(RuntimeError):
    """Raised when a governance run cannot be completed."""


class RuntimeResult(FrozenModel):
    """Combined governance, execution, and evidence outcome."""

    run_id: str = Field(min_length=1)
    decision: DecisionRecord
    execution: ToolExecutionRecord
    operational_outcome: str = Field(min_length=1)
    evidence_event_count: int = Field(ge=1)
    evidence_final_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class GovernanceMiddleware:
    """Mediate every tool capability through norm evaluation."""

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

    def process(
        self,
        *,
        context: GovernanceContext,
        proposal: ActionProposal,
        norms: Iterable[RuntimeNorm],
        recorder: EvidenceRecorder,
        run_metadata: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        """Evaluate one proposal and control its capability invocation."""

        norm_list = list(norms)

        start_payload: dict[str, Any] = {
            "task_id": context.task_id,
            "proposal_id": proposal.proposal_id,
            "policy_version": context.policy_version,
        }

        if run_metadata:
            start_payload["metadata"] = run_metadata

        recorder.start(
            start_payload,
            timestamp=self._now(),
        )

        recorder.append(
            "context_loaded",
            {
                "context": context.model_dump(mode="json"),
            },
            timestamp=self._now(),
        )
        
#        recorder.append(
#            "context_loaded",
#            {
#                "context": context.model_dump(mode="json"),
#            },
#            timestamp=self._now(),
#        )

        recorder.append(
            "proposal_created",
            {
                "proposal": proposal.model_dump(mode="json"),
            },
            timestamp=self._now(),
        )

        applicable_norms = self._evaluator.select_applicable_norms(
            norm_list,
            context,
            proposal,
        )

        recorder.append(
            "norms_selected",
            {
                "norm_ids": [
                    norm.norm_id
                    for norm in applicable_norms
                ],
                "norm_versions": {
                    norm.norm_id: norm.version
                    for norm in applicable_norms
                },
            },
            timestamp=self._now(),
        )

        evaluation_time = self._now()

        decision = self._evaluator.evaluate(
            norm_list,
            context,
            proposal,
            evaluated_at=evaluation_time,
        )

        recorder.append(
            "governance_decision",
            {
                "decision": decision.model_dump(mode="json"),
            },
            timestamp=self._now(),
        )

        if decision.disposition is Disposition.ALLOW:
            execution, operational_outcome = self._execute_allowed(
                proposal=proposal,
                decision=decision,
                recorder=recorder,
            )

        elif decision.disposition is Disposition.BLOCK:
            execution, operational_outcome = self._record_blocked(
                proposal=proposal,
                decision=decision,
                recorder=recorder,
            )

        elif decision.disposition is Disposition.ESCALATE:
            execution, operational_outcome = self._record_escalated(
                context=context,
                proposal=proposal,
                decision=decision,
                recorder=recorder,
            )

        else:
            raise RuntimeProcessingError(
                f"unsupported disposition: {decision.disposition}"
            )

        recorder.complete(
            {
                "decision_id": decision.decision_id,
                "execution_id": execution.execution_id,
                "operational_outcome": operational_outcome,
                "executed": execution.executed,
                "side_effect": execution.side_effect,
            },
            timestamp=self._now(),
        )

        return RuntimeResult(
            run_id=recorder.run_id,
            decision=decision,
            execution=execution,
            operational_outcome=operational_outcome,
            evidence_event_count=recorder.event_count,
            evidence_final_hash=recorder.final_hash,
        )

    def _execute_allowed(
        self,
        *,
        proposal: ActionProposal,
        decision: DecisionRecord,
        recorder: EvidenceRecorder,
    ) -> tuple[ToolExecutionRecord, str]:
        """Invoke an allowed capability and record its outcome."""

        started_at = self._now()

        recorder.append(
            "tool_invoked",
            {
                "decision_id": decision.decision_id,
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
                    "decision_id": decision.decision_id,
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
                "decision_id": decision.decision_id,
                "execution": execution.model_dump(mode="json"),
            },
            timestamp=completed_at,
        )

        return execution, "executed"

    def _record_blocked(
        self,
        *,
        proposal: ActionProposal,
        decision: DecisionRecord,
        recorder: EvidenceRecorder,
    ) -> tuple[ToolExecutionRecord, str]:
        """Record explicit non-execution after a block disposition."""

        timestamp = self._now()

        execution = ToolExecutionRecord(
            execution_id=self._execution_id(
                recorder.run_id,
                proposal.proposal_id,
            ),
            proposal_id=proposal.proposal_id,
            tool=proposal.tool,
            executed=False,
            side_effect=False,
            arguments=proposal.arguments,
            result={
                "status": "blocked",
            },
            error=None,
            started_at=timestamp,
            completed_at=timestamp,
        )

        recorder.append(
            "tool_blocked",
            {
                "decision_id": decision.decision_id,
                "execution": execution.model_dump(mode="json"),
                "reasons": decision.reasons,
            },
            timestamp=timestamp,
        )

        return execution, "blocked"

    def _record_escalated(
        self,
        *,
        context: GovernanceContext,
        proposal: ActionProposal,
        decision: DecisionRecord,
        recorder: EvidenceRecorder,
    ) -> tuple[ToolExecutionRecord, str]:
        """Record a pending human-review path without invoking the tool."""

        timestamp = self._now()

        execution = ToolExecutionRecord(
            execution_id=self._execution_id(
                recorder.run_id,
                proposal.proposal_id,
            ),
            proposal_id=proposal.proposal_id,
            tool=proposal.tool,
            executed=False,
            side_effect=False,
            arguments=proposal.arguments,
            result={
                "status": "pending_human_review",
            },
            error=None,
            started_at=timestamp,
            completed_at=timestamp,
        )

        approval_request = {
            "request_id": (
                f"{recorder.run_id}:approval:"
                f"{proposal.proposal_id}"
            ),
            "subject": context.actor,
            "action": proposal.tool,
            "object_id": self._proposal_object_id(proposal),
            "proposal_id": proposal.proposal_id,
            "decision_id": decision.decision_id,
            "constraints": proposal.arguments,
            "status": "pending",
        }

        recorder.append(
            "approval_required",
            {
                "approval_request": approval_request,
                "execution": execution.model_dump(mode="json"),
                "reasons": decision.reasons,
            },
            timestamp=timestamp,
        )

        return execution, "escalated"

    def _now(self) -> datetime:
        """Return a validated timezone-aware timestamp."""

        value = self._clock()

        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeProcessingError(
                "runtime clock must return timezone-aware timestamps"
            )

        return value

    @staticmethod
    def _execution_id(
        run_id: str,
        proposal_id: str,
    ) -> str:
        """Create a stable execution identifier."""

        return f"{run_id}:execution:{proposal_id}"

    @staticmethod
    def _proposal_object_id(
        proposal: ActionProposal,
    ) -> str | None:
        """Extract the principal object identifier from a proposal."""

        for field_name in (
            "record_id",
            "document_id",
            "report_id",
            "object_id",
            "request_id",
        ):
            value: Any = proposal.arguments.get(field_name)

            if value is not None:
                return str(value)

        return None
