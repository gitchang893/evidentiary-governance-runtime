from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import Field

from eg_runtime.approval_package import (
    ApprovalPackageVerificationStatus,
    create_approval_artifact_package,
    verify_approval_artifact_package,
)
from eg_runtime.approval_workflow import (
    ApprovalResolutionStatus,
    ApprovalWorkflow,
    PendingApproval,
)
from eg_runtime.evidence import EvidenceRecorder
from eg_runtime.models import (
    Disposition,
    FrozenModel,
)
from eg_runtime.runtime import GovernanceMiddleware
from eg_runtime.scenarios import load_scenario
from eg_runtime.tools import (
    SendMessageTool,
    ToolRegistry,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


class ApprovalWorkflowSimulationError(RuntimeError):
    """Raised when the approval workflow simulation fails."""


class ApprovalWorkflowRunRecord(FrozenModel):
    """Result of one persisted approval workflow."""

    workflow_id: str = Field(min_length=1)
    approval_request_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    resolution_status: ApprovalResolutionStatus
    package_relative_path: str = Field(min_length=1)
    package_valid: bool
    package_issues: list[str] = Field(
        default_factory=list
    )

    original_disposition: Disposition
    original_executed: bool

    resumed_disposition: Disposition | None = None
    resumed_executed: bool
    side_effect_count: int = Field(ge=0)


class ApprovalWorkflowSimulationSummary(FrozenModel):
    """Summary of the persisted approval workflow experiment."""

    simulation_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    generated_at: datetime
    workflow_count: int = Field(ge=0)
    approved_workflows: int = Field(ge=0)
    rejected_workflows: int = Field(ge=0)
    valid_packages: int = Field(ge=0)
    package_validity_rate: float = Field(
        ge=0.0,
        le=1.0,
    )
    approved_resumed_executions: int = Field(ge=0)
    rejected_resumed_executions: int = Field(ge=0)
    records: list[ApprovalWorkflowRunRecord]


class IncrementingClock:
    """Return deterministic timestamps."""

    def __init__(
        self,
        start: datetime,
    ) -> None:
        self._current = start

    def __call__(self) -> datetime:
        value = self._current
        self._current += timedelta(
            milliseconds=1
        )
        return value


def _default_scenario_root() -> Path:
    """Return the canonical scenario directory."""

    return (
        Path(__file__).resolve().parents[2]
        / "scenarios"
        / "canonical"
    )


def _prepare_output(
    output_dir: Path,
    *,
    overwrite: bool,
) -> None:
    """Create a clean simulation output directory."""

    if output_dir.exists():
        if not overwrite:
            raise ApprovalWorkflowSimulationError(
                "output directory already exists: "
                f"{output_dir}"
            )

        if output_dir.is_symlink():
            output_dir.unlink()
        elif output_dir.is_dir():
            shutil.rmtree(output_dir)
        else:
            output_dir.unlink()

    output_dir.mkdir(
        parents=True,
        exist_ok=False,
    )


def _create_original_escalation(
    *,
    unsafe: Any,
    staging_dir: Path,
    workflow_id: str,
) -> tuple[PendingApproval, Path]:
    """Run and record the initial escalation."""

    original_path = (
        staging_dir
        / f"{workflow_id}-original.jsonl"
    )

    messages: list[dict[str, Any]] = []

    result = GovernanceMiddleware(
        ToolRegistry(
            [
                SendMessageTool(messages),
            ]
        ),
        clock=IncrementingClock(BASE_TIME),
    ).process(
        context=unsafe.context,
        proposal=unsafe.proposal,
        norms=unsafe.norms,
        recorder=EvidenceRecorder(
            original_path,
            f"run-{workflow_id}-original",
        ),
        run_metadata={
            "experiment": (
                "persistent_approval_workflow"
            ),
            "workflow_id": workflow_id,
            "phase": "original",
        },
    )

    if (
        result.decision.disposition
        is not Disposition.ESCALATE
    ):
        raise ApprovalWorkflowSimulationError(
            "original run did not escalate"
        )

    if result.execution.executed:
        raise ApprovalWorkflowSimulationError(
            "original escalated action was executed"
        )

    if messages:
        raise ApprovalWorkflowSimulationError(
            "original escalation produced a side effect"
        )

    pending = ApprovalWorkflow().create_pending(
        context=unsafe.context,
        proposal=unsafe.proposal,
        decision=result.decision,
        created_at=BASE_TIME + timedelta(
            minutes=1
        ),
    )

    return pending, original_path


def _run_approved_workflow(
    *,
    output_dir: Path,
    staging_dir: Path,
    unsafe: Any,
    benign: Any,
) -> ApprovalWorkflowRunRecord:
    """Create an approved workflow and persisted package."""

    workflow_id = "approval-approved"

    pending, original_path = (
        _create_original_escalation(
            unsafe=unsafe,
            staging_dir=staging_dir,
            workflow_id=workflow_id,
        )
    )

    approval = benign.context.approval

    if approval is None:
        raise ApprovalWorkflowSimulationError(
            "benign scenario contains no approval"
        )

    resolution = ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=True,
        approval=approval,
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
        reason="External communication approved.",
    )

    if not resolution.can_resume:
        raise ApprovalWorkflowSimulationError(
            "approved resolution cannot resume"
        )

    if resolution.resumed_context is None:
        raise ApprovalWorkflowSimulationError(
            "approved resolution lacks resumed context"
        )

    resumed_path = (
        staging_dir
        / f"{workflow_id}-resumed.jsonl"
    )

    messages: list[dict[str, Any]] = []

    resumed_result = GovernanceMiddleware(
        ToolRegistry(
            [
                SendMessageTool(messages),
            ]
        ),
        clock=IncrementingClock(
            BASE_TIME + timedelta(minutes=6)
        ),
    ).process(
        context=resolution.resumed_context,
        proposal=unsafe.proposal,
        norms=unsafe.norms,
        recorder=EvidenceRecorder(
            resumed_path,
            f"run-{workflow_id}-resumed",
        ),
        run_metadata={
            "workflow": "approval_resume",
            "approval_request_id": (
                resolution.approval_request_id
            ),
            "experiment": (
                "persistent_approval_workflow"
            ),
            "workflow_id": workflow_id,
            "phase": "resumed",
        },
    )

    if (
        resumed_result.decision.disposition
        is not Disposition.ALLOW
    ):
        raise ApprovalWorkflowSimulationError(
            "approved proposal was not allowed"
        )

    if not resumed_result.execution.executed:
        raise ApprovalWorkflowSimulationError(
            "approved proposal was not executed"
        )

    package_dir = (
        output_dir
        / pending.approval_request_id
        / resolution.status.value
    )

    create_approval_artifact_package(
        output_dir=package_dir,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=resumed_path,
        created_at=BASE_TIME + timedelta(
            minutes=10
        ),
    )

    verification = (
        verify_approval_artifact_package(
            package_dir
        )
    )

    return ApprovalWorkflowRunRecord(
        workflow_id=workflow_id,
        approval_request_id=(
            pending.approval_request_id
        ),
        resolution_status=resolution.status,
        package_relative_path=(
            package_dir
            .relative_to(output_dir)
            .as_posix()
        ),
        package_valid=(
            verification.status
            is ApprovalPackageVerificationStatus.VALID
        ),
        package_issues=verification.issues,
        original_disposition=Disposition.ESCALATE,
        original_executed=False,
        resumed_disposition=(
            resumed_result.decision.disposition
        ),
        resumed_executed=(
            resumed_result.execution.executed
        ),
        side_effect_count=len(messages),
    )


def _run_rejected_workflow(
    *,
    output_dir: Path,
    staging_dir: Path,
    unsafe: Any,
) -> ApprovalWorkflowRunRecord:
    """Create a rejected workflow and persisted package."""

    workflow_id = "approval-rejected"

    pending, original_path = (
        _create_original_escalation(
            unsafe=unsafe,
            staging_dir=staging_dir,
            workflow_id=workflow_id,
        )
    )

    resolution = ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=False,
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
        reason="External communication rejected.",
    )

    if resolution.can_resume:
        raise ApprovalWorkflowSimulationError(
            "rejected resolution can resume"
        )

    package_dir = (
        output_dir
        / pending.approval_request_id
        / resolution.status.value
    )

    create_approval_artifact_package(
        output_dir=package_dir,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        created_at=BASE_TIME + timedelta(
            minutes=10
        ),
    )

    verification = (
        verify_approval_artifact_package(
            package_dir
        )
    )

    return ApprovalWorkflowRunRecord(
        workflow_id=workflow_id,
        approval_request_id=(
            pending.approval_request_id
        ),
        resolution_status=resolution.status,
        package_relative_path=(
            package_dir
            .relative_to(output_dir)
            .as_posix()
        ),
        package_valid=(
            verification.status
            is ApprovalPackageVerificationStatus.VALID
        ),
        package_issues=verification.issues,
        original_disposition=Disposition.ESCALATE,
        original_executed=False,
        resumed_disposition=None,
        resumed_executed=False,
        side_effect_count=0,
    )


def _ratio(
    numerator: int,
    denominator: int,
) -> float:
    """Return a safe ratio."""

    if denominator == 0:
        return 0.0

    return numerator / denominator


def run_approval_workflow_simulation(
    output_dir: str | Path,
    *,
    scenario_root: str | Path | None = None,
    overwrite: bool = False,
) -> ApprovalWorkflowSimulationSummary:
    """Run and persist approved and rejected workflows."""

    output = Path(output_dir)

    scenarios = (
        Path(scenario_root)
        if scenario_root is not None
        else _default_scenario_root()
    )

    unsafe = load_scenario(
        scenarios
        / "approval_requirement_unsafe.yaml"
    )

    benign = load_scenario(
        scenarios
        / "approval_requirement_benign.yaml"
    )

    _prepare_output(
        output,
        overwrite=overwrite,
    )

    try:
        with TemporaryDirectory(
            prefix="eg-approval-workflow-"
        ) as temporary:
            staging = Path(temporary)

            records = [
                _run_approved_workflow(
                    output_dir=output,
                    staging_dir=staging,
                    unsafe=unsafe,
                    benign=benign,
                ),
                _run_rejected_workflow(
                    output_dir=output,
                    staging_dir=staging,
                    unsafe=unsafe,
                ),
            ]
    except Exception:
        shutil.rmtree(
            output,
            ignore_errors=True,
        )
        raise

    valid_packages = sum(
        record.package_valid
        for record in records
    )

    approved = [
        record
        for record in records
        if (
            record.resolution_status
            is ApprovalResolutionStatus.APPROVED
        )
    ]

    rejected = [
        record
        for record in records
        if (
            record.resolution_status
            is ApprovalResolutionStatus.REJECTED
        )
    ]

    summary = ApprovalWorkflowSimulationSummary(
        generated_at=BASE_TIME + timedelta(
            minutes=10
        ),
        workflow_count=len(records),
        approved_workflows=len(approved),
        rejected_workflows=len(rejected),
        valid_packages=valid_packages,
        package_validity_rate=_ratio(
            valid_packages,
            len(records),
        ),
        approved_resumed_executions=sum(
            record.resumed_executed
            for record in approved
        ),
        rejected_resumed_executions=sum(
            record.resumed_executed
            for record in rejected
        ),
        records=records,
    )

    (
        output / "summary.json"
    ).write_text(
        json.dumps(
            summary.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return summary


def main() -> None:
    """Run the approval workflow simulation CLI."""

    parser = argparse.ArgumentParser(
        description=(
            "Run persistent approval workflow "
            "experiments."
        )
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--scenarios",
        type=Path,
        default=_default_scenario_root(),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    arguments = parser.parse_args()

    summary = run_approval_workflow_simulation(
        arguments.output,
        scenario_root=arguments.scenarios,
        overwrite=arguments.overwrite,
    )

    print(
        f"completed {summary.workflow_count} "
        "approval workflows"
    )

    print(
        "package_validity_rate="
        f"{summary.package_validity_rate}"
    )

    print(
        "approved_resumed_executions="
        f"{summary.approved_resumed_executions}"
    )

    print(
        "rejected_resumed_executions="
        f"{summary.rejected_resumed_executions}"
    )


if __name__ == "__main__":
    main()
