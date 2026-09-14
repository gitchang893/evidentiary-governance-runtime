from __future__ import annotations

import json
from pathlib import Path

from eg_runtime.approval_package import (
    ApprovalArtifactKind,
    ApprovalPackageVerificationStatus,
    verify_approval_artifact_package,
)
from eg_runtime.approval_simulation import (
    run_approval_workflow_simulation,
)
from eg_runtime.approval_workflow import (
    ApprovalResolutionStatus,
)
from eg_runtime.models import Disposition


def find_record(
    summary,
    status: ApprovalResolutionStatus,
):
    matches = [
        record
        for record in summary.records
        if record.resolution_status is status
    ]

    assert len(matches) == 1

    return matches[0]


def package_path(
    output_dir: Path,
    record,
) -> Path:
    return (
        output_dir
        / record.package_relative_path
    )


def test_simulation_persists_approved_and_rejected_packages(
    tmp_path: Path,
) -> None:
    output = tmp_path / "approval-workflows"

    summary = run_approval_workflow_simulation(
        output
    )

    assert summary.workflow_count == 2
    assert summary.approved_workflows == 1
    assert summary.rejected_workflows == 1
    assert summary.valid_packages == 2
    assert summary.package_validity_rate == 1.0

    for record in summary.records:
        path = package_path(
            output,
            record,
        )

        assert path.exists()
        assert (path / "manifest.json").exists()

        verification = (
            verify_approval_artifact_package(
                path
            )
        )

        assert (
            verification.status
            is ApprovalPackageVerificationStatus.VALID
        )


def test_package_shapes_follow_resolution_status(
    tmp_path: Path,
) -> None:
    output = tmp_path / "approval-workflows"

    summary = run_approval_workflow_simulation(
        output
    )

    approved = find_record(
        summary,
        ApprovalResolutionStatus.APPROVED,
    )

    rejected = find_record(
        summary,
        ApprovalResolutionStatus.REJECTED,
    )

    approved_manifest = json.loads(
        (
            package_path(output, approved)
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )

    rejected_manifest = json.loads(
        (
            package_path(output, rejected)
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )

    approved_kinds = {
        entry["kind"]
        for entry in approved_manifest["artifacts"]
    }

    rejected_kinds = {
        entry["kind"]
        for entry in rejected_manifest["artifacts"]
    }

    assert (
        ApprovalArtifactKind.RESUMED_EVIDENCE.value
        in approved_kinds
    )

    assert (
        ApprovalArtifactKind.RESUMED_EVIDENCE.value
        not in rejected_kinds
    )

    assert (
        package_path(output, approved)
        / "evidence"
        / "resumed.jsonl"
    ).exists()

    assert not (
        package_path(output, rejected)
        / "evidence"
        / "resumed.jsonl"
    ).exists()


def test_summary_records_runtime_outcomes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "approval-workflows"

    summary = run_approval_workflow_simulation(
        output
    )

    approved = find_record(
        summary,
        ApprovalResolutionStatus.APPROVED,
    )

    rejected = find_record(
        summary,
        ApprovalResolutionStatus.REJECTED,
    )

    assert (
        approved.original_disposition
        is Disposition.ESCALATE
    )

    assert approved.original_executed is False

    assert (
        approved.resumed_disposition
        is Disposition.ALLOW
    )

    assert approved.resumed_executed is True
    assert approved.side_effect_count == 1

    assert (
        rejected.original_disposition
        is Disposition.ESCALATE
    )

    assert rejected.original_executed is False
    assert rejected.resumed_disposition is None
    assert rejected.resumed_executed is False
    assert rejected.side_effect_count == 0

    assert summary.approved_resumed_executions == 1
    assert summary.rejected_resumed_executions == 0

    stored = json.loads(
        (
            output / "summary.json"
        ).read_text(encoding="utf-8")
    )

    assert stored["workflow_count"] == 2
    assert stored["package_validity_rate"] == 1.0


def test_simulation_outputs_are_reproducible(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_summary = (
        run_approval_workflow_simulation(first)
    )

    second_summary = (
        run_approval_workflow_simulation(second)
    )

    assert (
        (first / "summary.json").read_bytes()
        == (second / "summary.json").read_bytes()
    )

    for first_record in first_summary.records:
        second_record = find_record(
            second_summary,
            first_record.resolution_status,
        )

        first_manifest = (
            package_path(first, first_record)
            / "manifest.json"
        )

        second_manifest = (
            package_path(second, second_record)
            / "manifest.json"
        )

        assert (
            first_manifest.read_bytes()
            == second_manifest.read_bytes()
        )


def test_persisted_resolution_tampering_is_detected(
    tmp_path: Path,
) -> None:
    output = tmp_path / "approval-workflows"

    summary = run_approval_workflow_simulation(
        output
    )

    approved = find_record(
        summary,
        ApprovalResolutionStatus.APPROVED,
    )

    approved_package = package_path(
        output,
        approved,
    )

    resolution_path = (
        approved_package
        / "workflow"
        / "resolution.json"
    )

    raw = json.loads(
        resolution_path.read_text(
            encoding="utf-8"
        )
    )

    raw["reason"] = "Altered persisted decision."

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
            approved_package
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
