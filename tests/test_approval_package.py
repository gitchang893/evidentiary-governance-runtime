from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from eg_runtime.approval_package import (
    ApprovalArtifactKind,
    ApprovalPackageVerificationStatus,
    create_approval_artifact_package,
    verify_approval_artifact_package,
)
from eg_runtime.approval_workflow import (
    ApprovalResolutionStatus,
    ApprovalWorkflow,
)
from eg_runtime.evidence import EvidenceRecorder
from eg_runtime.models import Disposition
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

SCENARIO_ROOT = (
    Path(__file__).resolve().parents[1]
    / "scenarios"
    / "canonical"
)


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


def create_original_escalation(
    tmp_path: Path,
):
    unsafe = load_scenario(
        SCENARIO_ROOT
        / "approval_requirement_unsafe.yaml"
    )

    messages: list[dict[str, Any]] = []
    evidence_path = (
        tmp_path / "source-original.jsonl"
    )

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
            evidence_path,
            "run-package-original",
        ),
        run_metadata={
            "experiment": "approval_package",
            "phase": "original",
        },
    )

    assert (
        result.decision.disposition
        is Disposition.ESCALATE
    )

    assert result.execution.executed is False
    assert messages == []

    pending = ApprovalWorkflow().create_pending(
        context=unsafe.context,
        proposal=unsafe.proposal,
        decision=result.decision,
        created_at=BASE_TIME + timedelta(
            minutes=1
        ),
    )

    return unsafe, pending, evidence_path


def approve(
    pending,
):
    benign = load_scenario(
        SCENARIO_ROOT
        / "approval_requirement_benign.yaml"
    )

    assert benign.context.approval is not None

    return ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=True,
        approval=benign.context.approval,
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
        reason="External communication approved.",
    )


def reject(
    pending,
):
    return ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=False,
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
        reason="External communication rejected.",
    )


def create_resumed_run(
    tmp_path: Path,
    unsafe,
    resolution,
) -> Path:
    assert resolution.resumed_context is not None

    messages: list[dict[str, Any]] = []

    evidence_path = (
        tmp_path / "source-resumed.jsonl"
    )

    result = GovernanceMiddleware(
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
            evidence_path,
            "run-package-resumed",
        ),
        run_metadata={
            "workflow": "approval_resume",
            "approval_request_id": (
                resolution.approval_request_id
            ),
            "experiment": "approval_package",
            "phase": "resumed",
        },
    )

    assert (
        result.decision.disposition
        is Disposition.ALLOW
    )

    assert result.execution.executed is True
    assert len(messages) == 1

    return evidence_path


def test_approved_package_is_valid_and_self_contained(
    tmp_path: Path,
) -> None:
    unsafe, pending, original_path = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    resumed_path = create_resumed_run(
        tmp_path,
        unsafe,
        resolution,
    )

    package_dir = tmp_path / "approved-package"

    manifest = create_approval_artifact_package(
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

    assert (
        verification.status
        is ApprovalPackageVerificationStatus.VALID
    )

    assert verification.issues == []
    assert verification.manifest_hash_valid is True

    assert (
        manifest.resolution_status
        is ApprovalResolutionStatus.APPROVED
    )

    assert {
        entry.kind
        for entry in manifest.artifacts
    } == {
        ApprovalArtifactKind.ORIGINAL_EVIDENCE,
        ApprovalArtifactKind.PENDING_REQUEST,
        ApprovalArtifactKind.RESOLUTION,
        ApprovalArtifactKind.APPROVAL_AUDIT,
        ApprovalArtifactKind.RESUMED_EVIDENCE,
    }

    original_path.unlink()
    resumed_path.unlink()

    second_verification = (
        verify_approval_artifact_package(
            package_dir
        )
    )

    assert (
        second_verification.status
        is ApprovalPackageVerificationStatus.VALID
    )


def test_rejected_package_has_no_resumed_evidence(
    tmp_path: Path,
) -> None:
    _, pending, original_path = (
        create_original_escalation(tmp_path)
    )

    resolution = reject(pending)

    package_dir = tmp_path / "rejected-package"

    manifest = create_approval_artifact_package(
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

    assert (
        verification.status
        is ApprovalPackageVerificationStatus.VALID
    )

    assert (
        manifest.resolution_status
        is ApprovalResolutionStatus.REJECTED
    )

    assert (
        ApprovalArtifactKind.RESUMED_EVIDENCE
        not in {
            entry.kind
            for entry in manifest.artifacts
        }
    )

    assert not (
        package_dir
        / "evidence"
        / "resumed.jsonl"
    ).exists()


def test_resolution_tampering_is_detected(
    tmp_path: Path,
) -> None:
    _, pending, original_path = (
        create_original_escalation(tmp_path)
    )

    resolution = reject(pending)

    package_dir = tmp_path / "tampered-package"

    create_approval_artifact_package(
        output_dir=package_dir,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        created_at=BASE_TIME + timedelta(
            minutes=10
        ),
    )

    resolution_path = (
        package_dir
        / "workflow"
        / "resolution.json"
    )

    raw = json.loads(
        resolution_path.read_text(
            encoding="utf-8"
        )
    )

    raw["reason"] = "Altered resolution reason."

    resolution_path.write_text(
        json.dumps(
            raw,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    verification = (
        verify_approval_artifact_package(
            package_dir
        )
    )

    assert (
        verification.status
        is ApprovalPackageVerificationStatus.INVALID
    )

    assert (
        "artifact_hash_mismatch:"
        "workflow/resolution.json"
        in verification.issues
    )


def test_missing_original_evidence_is_detected(
    tmp_path: Path,
) -> None:
    _, pending, original_path = (
        create_original_escalation(tmp_path)
    )

    resolution = reject(pending)

    package_dir = tmp_path / "missing-package"

    create_approval_artifact_package(
        output_dir=package_dir,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        created_at=BASE_TIME + timedelta(
            minutes=10
        ),
    )

    (
        package_dir
        / "evidence"
        / "original.jsonl"
    ).unlink()

    verification = (
        verify_approval_artifact_package(
            package_dir
        )
    )

    assert (
        verification.status
        is ApprovalPackageVerificationStatus.INVALID
    )

    assert (
        "artifact_missing:evidence/original.jsonl"
        in verification.issues
    )


def test_manifest_tampering_is_detected(
    tmp_path: Path,
) -> None:
    _, pending, original_path = (
        create_original_escalation(tmp_path)
    )

    resolution = reject(pending)

    package_dir = tmp_path / "manifest-package"

    create_approval_artifact_package(
        output_dir=package_dir,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        created_at=BASE_TIME + timedelta(
            minutes=10
        ),
    )

    manifest_path = package_dir / "manifest.json"

    raw = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    raw["approval_request_id"] = "a" * 64

    manifest_path.write_text(
        json.dumps(
            raw,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    verification = (
        verify_approval_artifact_package(
            package_dir
        )
    )

    assert (
        verification.status
        is ApprovalPackageVerificationStatus.INVALID
    )

    assert verification.manifest_hash_valid is False

    assert (
        "manifest_hash_mismatch"
        in verification.issues
    )
