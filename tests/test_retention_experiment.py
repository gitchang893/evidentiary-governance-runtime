from __future__ import annotations

import csv
import json
from pathlib import Path

from eg_runtime.approval_integrity_experiment import (
    run_approval_integrity_experiment,
)
from eg_runtime.artifact_footprint import (
    build_artifact_footprint,
)
from eg_runtime.retention_experiment import (
    RetentionClaim,
    RetentionPolicy,
    run_retention_experiment,
)


REGISTRY_KEY = (
    b"retention-registry-test-key-2026"
)

WITNESS_KEY = (
    b"retention-witness-test-key-2026"
)


def create_experiment(
    tmp_path: Path,
):
    artifact_root = (
        tmp_path / "approval-integrity"
    )

    run_approval_integrity_experiment(
        artifact_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    inventory = build_artifact_footprint(
        artifact_root,
        root_label="approval-integrity",
        secret_values=[
            REGISTRY_KEY,
            WITNESS_KEY,
        ],
    )

    output = tmp_path / "retention"

    summary = run_retention_experiment(
        inventory,
        output,
    )

    return output, inventory, summary


def policy_result(
    summary,
    policy: RetentionPolicy,
):
    return summary.policies[
        policy.value
    ]


def test_full_policy_retains_complete_evidence(
    tmp_path: Path,
) -> None:
    _, inventory, summary = create_experiment(
        tmp_path
    )

    full = policy_result(
        summary,
        RetentionPolicy.FULL,
    )

    assert (
        full.retained_artifact_count
        == inventory.artifact_count
    )

    assert full.removed_artifact_count == 0

    assert full.retained_bytes == inventory.total_bytes
    assert full.removed_bytes == 0
    assert full.storage_reduction_ratio == 0.0

    assert (
        full.retained_restricted_artifact_count
        == inventory.restricted_artifact_count
    )

    assert (
        full.retained_restricted_bytes
        == inventory.restricted_bytes
    )

    assert full.claim_coverage == 1.0

    assert all(
        full.claim_support.values()
    )


def test_baseline_policy_removes_attack_copies(
    tmp_path: Path,
) -> None:
    _, inventory, summary = create_experiment(
        tmp_path
    )

    baseline = policy_result(
        summary,
        RetentionPolicy.BASELINE_ONLY,
    )

    assert (
        baseline.retained_artifact_count
        < inventory.artifact_count
    )

    assert baseline.removed_artifact_count > 0
    assert baseline.storage_reduction_ratio > 0.0

    assert all(
        (
            path == "summary.json"
            or path.startswith("baseline/")
        )
        for path in baseline.retained_paths
    )

    assert not any(
        path.startswith("attacks/")
        for path in baseline.retained_paths
    )

    assert (
        baseline.retained_restricted_artifact_count
        > 0
    )

    assert (
        baseline.claim_support[
            RetentionClaim.ATTACK_FORENSICS.value
        ]
        is False
    )


def test_research_minimal_removes_restricted_artifacts(
    tmp_path: Path,
) -> None:
    _, inventory, summary = create_experiment(
        tmp_path
    )

    minimal = policy_result(
        summary,
        RetentionPolicy.RESEARCH_MINIMAL,
    )

    assert (
        inventory.restricted_artifact_count
        > 0
    )

    assert (
        minimal.retained_restricted_artifact_count
        == 0
    )

    assert minimal.retained_restricted_bytes == 0

    assert (
        minimal.restricted_byte_reduction_ratio
        == 1.0
    )

    assert minimal.storage_reduction_ratio > 0.0

    assert (
        minimal.claim_support[
            RetentionClaim.PACKAGE_INTEGRITY.value
        ]
        is True
    )

    assert (
        minimal.claim_support[
            RetentionClaim.REGISTRY_INTEGRITY.value
        ]
        is True
    )

    assert (
        minimal.claim_support[
            RetentionClaim.ROLLBACK_WITNESS.value
        ]
        is True
    )

    assert (
        minimal.claim_support[
            RetentionClaim.RUNTIME_REPLAY.value
        ]
        is False
    )

    assert (
        minimal.claim_support[
            RetentionClaim
            .HUMAN_RESOLUTION_DETAIL
            .value
        ]
        is False
    )

    assert minimal.supported_claim_count == 5
    assert minimal.total_claim_count == 8
    assert minimal.claim_coverage == 5 / 8


def test_retention_outputs_record_policy_manifests(
    tmp_path: Path,
) -> None:
    output, _, summary = create_experiment(
        tmp_path
    )

    assert (output / "summary.json").exists()
    assert (output / "policies.csv").exists()

    for policy in RetentionPolicy:
        path = (
            output
            / policy.value
            / "retention_manifest.json"
        )

        assert path.exists()

        stored = json.loads(
            path.read_text(encoding="utf-8")
        )

        assert stored["policy"] == policy.value

        assert (
            stored["retained_artifact_count"]
            == summary.policies[
                policy.value
            ].retained_artifact_count
        )

    with (
        output / "policies.csv"
    ).open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(
            csv.DictReader(handle)
        )

    assert len(rows) == 3


def test_retention_experiment_is_reproducible(
    tmp_path: Path,
) -> None:
    first_artifacts = tmp_path / "first-artifacts"
    second_artifacts = tmp_path / "second-artifacts"

    run_approval_integrity_experiment(
        first_artifacts,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    run_approval_integrity_experiment(
        second_artifacts,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    first_inventory = build_artifact_footprint(
        first_artifacts,
        root_label="approval-integrity",
        secret_values=[
            REGISTRY_KEY,
            WITNESS_KEY,
        ],
    )

    second_inventory = build_artifact_footprint(
        second_artifacts,
        root_label="approval-integrity",
        secret_values=[
            REGISTRY_KEY,
            WITNESS_KEY,
        ],
    )

    first_output = tmp_path / "first-retention"
    second_output = tmp_path / "second-retention"

    first = run_retention_experiment(
        first_inventory,
        first_output,
    )

    second = run_retention_experiment(
        second_inventory,
        second_output,
    )

    assert (
        first.model_dump(mode="json")
        == second.model_dump(mode="json")
    )

    assert (
        (first_output / "summary.json").read_bytes()
        == (second_output / "summary.json").read_bytes()
    )

    assert (
        (first_output / "policies.csv").read_bytes()
        == (second_output / "policies.csv").read_bytes()
    )
