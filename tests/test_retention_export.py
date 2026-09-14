from __future__ import annotations

from pathlib import Path

import pytest

from eg_runtime.approval_integrity_experiment import (
    run_approval_integrity_experiment,
)
from eg_runtime.artifact_footprint import (
    ArtifactSensitivity,
    build_artifact_footprint,
)
from eg_runtime.retention_experiment import (
    RetentionPolicy,
    run_retention_experiment,
)
from eg_runtime.retention_export import (
    RetentionExportError,
    RetentionExportVerificationStatus,
    materialize_retention_export,
    verify_retention_export,
)


REGISTRY_KEY = (
    b"retention-export-registry-key-2026"
)

WITNESS_KEY = (
    b"retention-export-witness-key-2026"
)


def create_source(
    tmp_path: Path,
):
    source_root = (
        tmp_path / "approval-integrity"
    )

    run_approval_integrity_experiment(
        source_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    inventory = build_artifact_footprint(
        source_root,
        root_label="approval-integrity",
        secret_values=[
            REGISTRY_KEY,
            WITNESS_KEY,
        ],
    )

    retention_root = (
        tmp_path / "approval-retention"
    )

    retention = run_retention_experiment(
        inventory,
        retention_root,
    )

    inventory_path = (
        source_root
        / "artifact_inventory.json"
    )

    retention_manifest_path = (
        retention_root
        / RetentionPolicy.RESEARCH_MINIMAL.value
        / "retention_manifest.json"
    )

    return (
        source_root,
        inventory,
        retention,
        inventory_path,
        retention_manifest_path,
    )


def create_export(
    tmp_path: Path,
):
    (
        source_root,
        inventory,
        retention,
        inventory_path,
        retention_manifest_path,
    ) = create_source(tmp_path)

    export_root = (
        tmp_path
        / "approval-retention-export"
    )

    manifest = materialize_retention_export(
        source_root=source_root,
        inventory_path=inventory_path,
        retention_manifest_path=(
            retention_manifest_path
        ),
        output_dir=export_root,
    )

    return (
        source_root,
        inventory,
        retention,
        inventory_path,
        retention_manifest_path,
        export_root,
        manifest,
    )


def test_research_minimal_export_materializes_exact_set(
    tmp_path: Path,
) -> None:
    (
        _,
        inventory,
        retention,
        _,
        _,
        export_root,
        manifest,
    ) = create_export(tmp_path)

    policy = retention.policies[
        RetentionPolicy.RESEARCH_MINIMAL.value
    ]

    actual_paths = sorted(
        path.relative_to(
            export_root / "artifacts"
        ).as_posix()
        for path in (
            export_root / "artifacts"
        ).rglob("*")
        if path.is_file()
    )

    assert actual_paths == policy.retained_paths

    assert (
        manifest.artifact_count
        == policy.retained_artifact_count
    )

    assert (
        manifest.total_bytes
        == policy.retained_bytes
    )

    inventory_index = {
        entry.relative_path: entry
        for entry in inventory.entries
    }

    assert all(
        inventory_index[path].sensitivity
        is not ArtifactSensitivity.RESTRICTED
        for path in actual_paths
    )


def test_valid_export_and_provenance_verify(
    tmp_path: Path,
) -> None:
    (
        _,
        _,
        _,
        inventory_path,
        retention_manifest_path,
        export_root,
        manifest,
    ) = create_export(tmp_path)

    result = verify_retention_export(
        export_dir=export_root,
        inventory_path=inventory_path,
        retention_manifest_path=(
            retention_manifest_path
        ),
    )

    assert (
        result.status
        is RetentionExportVerificationStatus.VALID
    )

    assert result.issues == []
    assert result.manifest_hash_valid is True
    assert result.artifact_set_valid is True
    assert result.artifact_hashes_valid is True
    assert result.provenance_valid is True

    assert (
        result.verified_artifact_count
        == manifest.artifact_count
    )


def test_exported_artifact_tampering_is_detected(
    tmp_path: Path,
) -> None:
    (
        _,
        _,
        _,
        inventory_path,
        retention_manifest_path,
        export_root,
        manifest,
    ) = create_export(tmp_path)

    target = (
        export_root
        / "artifacts"
        / manifest.artifacts[0].relative_path
    )

    target.write_bytes(
        target.read_bytes()
        + b"\nmodified\n"
    )

    result = verify_retention_export(
        export_dir=export_root,
        inventory_path=inventory_path,
        retention_manifest_path=(
            retention_manifest_path
        ),
    )

    assert (
        result.status
        is RetentionExportVerificationStatus.INVALID
    )

    assert result.artifact_hashes_valid is False

    assert any(
        issue.startswith(
            "export_artifact_"
        )
        for issue in result.issues
    )


def test_missing_and_unexpected_export_files_are_detected(
    tmp_path: Path,
) -> None:
    (
        _,
        _,
        _,
        inventory_path,
        retention_manifest_path,
        export_root,
        manifest,
    ) = create_export(tmp_path)

    missing_relative = (
        manifest.artifacts[0].relative_path
    )

    missing_path = (
        export_root
        / "artifacts"
        / missing_relative
    )

    missing_path.unlink()

    unexpected_relative = "unexpected.txt"

    unexpected_path = (
        export_root
        / "artifacts"
        / unexpected_relative
    )

    unexpected_path.write_text(
        "unexpected artifact\n",
        encoding="utf-8",
    )

    result = verify_retention_export(
        export_dir=export_root,
        inventory_path=inventory_path,
        retention_manifest_path=(
            retention_manifest_path
        ),
    )

    assert (
        result.status
        is RetentionExportVerificationStatus.INVALID
    )

    assert result.artifact_set_valid is False

    assert (
        f"missing_export_artifact:{missing_relative}"
        in result.issues
    )

    assert (
        f"unexpected_export_artifact:{unexpected_relative}"
        in result.issues
    )


def test_source_drift_prevents_export(
    tmp_path: Path,
) -> None:
    (
        source_root,
        _,
        _,
        inventory_path,
        retention_manifest_path,
    ) = create_source(tmp_path)

    source_summary = (
        source_root / "summary.json"
    )

    original_content = source_summary.read_text(
        encoding="utf-8"
    )

    old_value = '"attack_count": 6'
    new_value = '"attack_count": 7'

    assert old_value in original_content
    assert len(old_value) == len(new_value)

    source_summary.write_text(
        original_content.replace(
            old_value,
            new_value,
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        RetentionExportError,
        match="source hash mismatch: summary.json",
    ):
        materialize_retention_export(
            source_root=source_root,
            inventory_path=inventory_path,
            retention_manifest_path=(
                retention_manifest_path
            ),
            output_dir=(
                tmp_path
                / "drifted-export"
            ),
        )

    assert not (
        tmp_path / "drifted-export"
    ).exists()
