from __future__ import annotations

import csv
import json
from pathlib import Path

from eg_runtime.approval_integrity_experiment import (
    run_approval_integrity_experiment,
)
from eg_runtime.artifact_footprint import (
    ArtifactKind,
    ArtifactSensitivity,
    build_artifact_footprint,
)


REGISTRY_KEY = (
    b"footprint-registry-test-key-2026"
)

WITNESS_KEY = (
    b"footprint-witness-test-key-2026"
)


def create_footprint(
    tmp_path: Path,
):
    root = tmp_path / "approval-integrity"

    run_approval_integrity_experiment(
        root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    summary = build_artifact_footprint(
        root,
        secret_values=[
            REGISTRY_KEY,
            WITNESS_KEY,
        ],
    )

    return root, summary


def find_entry(
    summary,
    suffix: str,
):
    matches = [
        entry
        for entry in summary.entries
        if entry.relative_path.endswith(suffix)
    ]

    assert matches

    return matches[0]


def test_inventory_covers_all_persisted_artifacts(
    tmp_path: Path,
) -> None:
    root, summary = create_footprint(
        tmp_path
    )

    excluded = {
        (
            root
            / "artifact_inventory.json"
        ).resolve(),
        (
            root
            / "artifact_inventory.csv"
        ).resolve(),
    }

    expected_files = [
        path
        for path in root.rglob("*")
        if (
            path.is_file()
            and path.resolve() not in excluded
        )
    ]

    assert (
        summary.artifact_count
        == len(expected_files)
    )

    assert summary.total_bytes > 0

    assert (
        root
        / "artifact_inventory.json"
    ).exists()

    assert (
        root
        / "artifact_inventory.csv"
    ).exists()

    stored = json.loads(
        (
            root
            / "artifact_inventory.json"
        ).read_text(encoding="utf-8")
    )

    assert (
        stored["artifact_count"]
        == summary.artifact_count
    )

    with (
        root / "artifact_inventory.csv"
    ).open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(
            csv.DictReader(handle)
        )

    assert len(rows) == summary.artifact_count


def test_sensitive_record_locations_are_classified(
    tmp_path: Path,
) -> None:
    _, summary = create_footprint(
        tmp_path
    )

    original = find_entry(
        summary,
        "evidence/original.jsonl",
    )

    pending = find_entry(
        summary,
        "workflow/pending.json",
    )

    resolution = find_entry(
        summary,
        "workflow/resolution.json",
    )

    audit = find_entry(
        summary,
        "audit/approval_audit.json",
    )

    registry = find_entry(
        summary,
        "approval-registry/registry.jsonl",
    )

    witness = find_entry(
        summary,
        "approval-witness/receipt.json",
    )

    assert (
        original.kind
        is ArtifactKind.RUNTIME_EVIDENCE
    )

    assert (
        original.sensitivity
        is ArtifactSensitivity.RESTRICTED
    )

    assert (
        pending.kind
        is ArtifactKind.PENDING_APPROVAL
    )

    assert (
        pending.sensitivity
        is ArtifactSensitivity.RESTRICTED
    )

    assert (
        resolution.kind
        is ArtifactKind.HUMAN_RESOLUTION
    )

    assert (
        resolution.sensitivity
        is ArtifactSensitivity.RESTRICTED
    )

    assert (
        audit.kind
        is ArtifactKind.APPROVAL_AUDIT
    )

    assert (
        audit.sensitivity
        is ArtifactSensitivity.CONFIDENTIAL
    )

    assert registry.kind is ArtifactKind.REGISTRY

    assert (
        witness.kind
        is ArtifactKind.WITNESS_RECEIPT
    )


def test_attack_matrix_creates_measurable_duplication(
    tmp_path: Path,
) -> None:
    _, summary = create_footprint(
        tmp_path
    )

    assert summary.unique_content_count > 0

    assert (
        summary.unique_content_count
        < summary.artifact_count
    )

    assert summary.duplicate_artifact_count > 0
    assert summary.duplicated_bytes > 0
    assert summary.duplication_ratio > 0.0

    duplicate_entries = [
        entry
        for entry in summary.entries
        if entry.duplicate_copy
    ]

    assert len(duplicate_entries) == (
        summary.duplicate_artifact_count
    )

    assert any(
        entry.duplicate_group_size > 1
        for entry in summary.entries
    )


def test_secret_keys_are_not_persisted(
    tmp_path: Path,
) -> None:
    _, summary = create_footprint(
        tmp_path
    )

    assert summary.secret_free is True
    assert summary.secret_finding_count == 0
    assert summary.secret_findings == []


def test_footprint_outputs_are_reproducible(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    run_approval_integrity_experiment(
        first_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    run_approval_integrity_experiment(
        second_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    first = build_artifact_footprint(
        first_root,
        root_label="approval-integrity",
        secret_values=[
            REGISTRY_KEY,
            WITNESS_KEY,
        ],
    )

    second = build_artifact_footprint(
        second_root,
        root_label="approval-integrity",
        secret_values=[
            REGISTRY_KEY,
            WITNESS_KEY,
        ],
    )

    assert (
        first.model_dump(mode="json")
        == second.model_dump(mode="json")
    )

    assert (
        (
            first_root
            / "artifact_inventory.json"
        ).read_bytes()
        == (
            second_root
            / "artifact_inventory.json"
        ).read_bytes()
    )

    assert (
        (
            first_root
            / "artifact_inventory.csv"
        ).read_bytes()
        == (
            second_root
            / "artifact_inventory.csv"
        ).read_bytes()
    )
