from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from eg_runtime.approval_integrity_experiment import (
    run_approval_integrity_experiment,
)
from eg_runtime.artifact_footprint import (
    ArtifactFootprintSummary,
    ArtifactSensitivity,
    build_artifact_footprint,
)
from eg_runtime.models import FrozenModel
from eg_runtime.retention_experiment import (
    RetentionPolicy,
    run_retention_experiment,
)
from eg_runtime.retention_export import (
    RetentionExportVerification,
    RetentionExportVerificationStatus,
    materialize_retention_export,
    verify_retention_export,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    11,
    0,
    tzinfo=UTC,
)


class RetentionExportExperimentError(RuntimeError):
    """Raised when the export attack experiment cannot run."""


class RetentionExportAttackSignal(StrEnum):
    """Verification component that signals an export attack."""

    EXPORT_MANIFEST = "export_manifest"
    ARTIFACT_SET = "artifact_set"
    ARTIFACT_HASH = "artifact_hash"
    PROVENANCE = "provenance"


class RetentionExportAttackRecord(FrozenModel):
    """Result of one export-integrity attack."""

    attack_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    artifact_relative_path: str = Field(min_length=1)

    expected_signal: RetentionExportAttackSignal
    actual_signals: list[RetentionExportAttackSignal]

    export_valid: bool
    detected: bool
    correctly_localized: bool

    manifest_hash_valid: bool
    artifact_set_valid: bool
    artifact_hashes_valid: bool
    provenance_valid: bool

    issues: list[str]


class RetentionExportExperimentSummary(FrozenModel):
    """Summary of all retention-export attacks."""

    experiment_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )

    generated_at: datetime

    baseline_valid: bool

    attack_count: int = Field(ge=0)
    detected_attacks: int = Field(ge=0)
    attack_detection_rate: float = Field(
        ge=0.0,
        le=1.0,
    )

    correctly_localized_attacks: int = Field(ge=0)
    localization_accuracy: float = Field(
        ge=0.0,
        le=1.0,
    )

    attacks: list[RetentionExportAttackRecord]


def _write_json(
    path: Path,
    value: Any,
) -> None:
    """Write deterministic human-readable JSON."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if isinstance(value, FrozenModel):
        content = value.model_dump(mode="json")
    else:
        content = value

    path.write_text(
        json.dumps(
            content,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _prepare_output(
    output: Path,
    *,
    overwrite: bool,
) -> None:
    """Create a clean experiment output directory."""

    if output.exists():
        if not overwrite:
            raise RetentionExportExperimentError(
                "output directory already exists: "
                f"{output}"
            )

        if output.is_symlink():
            output.unlink()
        elif output.is_dir():
            shutil.rmtree(output)
        else:
            output.unlink()

    output.mkdir(
        parents=True,
        exist_ok=False,
    )


def _source_root(
    root: Path,
) -> Path:
    return root / "source"


def _inventory_path(
    root: Path,
) -> Path:
    return (
        _source_root(root)
        / "artifact_inventory.json"
    )


def _retention_root(
    root: Path,
) -> Path:
    return root / "retention"


def _retention_manifest_path(
    root: Path,
) -> Path:
    return (
        _retention_root(root)
        / RetentionPolicy.RESEARCH_MINIMAL.value
        / "retention_manifest.json"
    )


def _export_root(
    root: Path,
) -> Path:
    return root / "export"


def _export_manifest_path(
    root: Path,
) -> Path:
    return (
        _export_root(root)
        / "export_manifest.json"
    )


def _create_baseline(
    root: Path,
    *,
    registry_key: bytes,
    witness_key: bytes,
) -> ArtifactFootprintSummary:
    """Create a valid source, retention decision, and export."""

    source = _source_root(root)

    run_approval_integrity_experiment(
        source,
        registry_key=registry_key,
        witness_key=witness_key,
    )

    inventory = build_artifact_footprint(
        source,
        root_label="approval-integrity",
        secret_values=[
            registry_key,
            witness_key,
        ],
    )

    run_retention_experiment(
        inventory,
        _retention_root(root),
    )

    materialize_retention_export(
        source_root=source,
        inventory_path=_inventory_path(root),
        retention_manifest_path=(
            _retention_manifest_path(root)
        ),
        output_dir=_export_root(root),
    )

    return inventory


def _verify(
    root: Path,
) -> RetentionExportVerification:
    """Verify one retention export hierarchy."""

    return verify_retention_export(
        export_dir=_export_root(root),
        inventory_path=_inventory_path(root),
        retention_manifest_path=(
            _retention_manifest_path(root)
        ),
    )


def _signals(
    verification: RetentionExportVerification,
) -> list[RetentionExportAttackSignal]:
    """Return all invalid verification components."""

    result: list[
        RetentionExportAttackSignal
    ] = []

    if not verification.manifest_hash_valid:
        result.append(
            RetentionExportAttackSignal.EXPORT_MANIFEST
        )

    if not verification.artifact_set_valid:
        result.append(
            RetentionExportAttackSignal.ARTIFACT_SET
        )

    if not verification.artifact_hashes_valid:
        result.append(
            RetentionExportAttackSignal.ARTIFACT_HASH
        )

    if not verification.provenance_valid:
        result.append(
            RetentionExportAttackSignal.PROVENANCE
        )

    return result


def _copy_attack_root(
    *,
    baseline: Path,
    attacks_root: Path,
    attack_id: str,
) -> Path:
    """Copy the baseline into an independent attack root."""

    attack_root = (
        attacks_root / attack_id
    )

    shutil.copytree(
        baseline,
        attack_root,
    )

    return attack_root


def _first_exported_artifact(
    root: Path,
) -> tuple[str, Path]:
    """Return the first exported artifact deterministically."""

    raw = json.loads(
        _export_manifest_path(root).read_text(
            encoding="utf-8"
        )
    )

    artifacts = raw.get("artifacts", [])

    if not artifacts:
        raise RetentionExportExperimentError(
            "export manifest has no artifacts"
        )

    relative_path = artifacts[0][
        "relative_path"
    ]

    return (
        relative_path,
        (
            _export_root(root)
            / "artifacts"
            / relative_path
        ),
    )


def _tamper_exported_artifact(
    root: Path,
) -> None:
    """Modify one exported artifact."""

    _, path = _first_exported_artifact(root)

    path.write_bytes(
        path.read_bytes()
        + b"\nmodified\n"
    )


def _delete_exported_artifact(
    root: Path,
) -> None:
    """Delete one expected exported artifact."""

    _, path = _first_exported_artifact(root)

    path.unlink()


def _inject_restricted_artifact(
    root: Path,
) -> None:
    """Inject a restricted source artifact excluded by policy."""

    inventory = ArtifactFootprintSummary.model_validate_json(
        _inventory_path(root).read_text(
            encoding="utf-8"
        )
    )

    export_manifest = json.loads(
        _export_manifest_path(root).read_text(
            encoding="utf-8"
        )
    )

    exported_paths = {
        artifact["relative_path"]
        for artifact in export_manifest["artifacts"]
    }

    candidates = sorted(
        (
            entry
            for entry in inventory.entries
            if (
                entry.sensitivity
                is ArtifactSensitivity.RESTRICTED
                and entry.relative_path
                not in exported_paths
            )
        ),
        key=lambda entry: entry.relative_path,
    )

    if not candidates:
        raise RetentionExportExperimentError(
            "no excluded restricted artifact "
            "is available"
        )

    selected = candidates[0]

    source = (
        _source_root(root)
        / selected.relative_path
    )

    destination = (
        _export_root(root)
        / "artifacts"
        / selected.relative_path
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.write_bytes(
        source.read_bytes()
    )


def _tamper_export_manifest(
    root: Path,
) -> None:
    """Modify the export manifest without rehashing it."""

    path = _export_manifest_path(root)

    raw = json.loads(
        path.read_text(encoding="utf-8")
    )

    raw["source_root_label"] = (
        "altered-approval-integrity"
    )

    _write_json(path, raw)


def _tamper_inventory_provenance(
    root: Path,
) -> None:
    """Modify the referenced inventory without invalid JSON."""

    path = _inventory_path(root)

    path.write_bytes(
        path.read_bytes()
        + b"\n"
    )


def _tamper_retention_provenance(
    root: Path,
) -> None:
    """Modify the referenced retention manifest."""

    path = _retention_manifest_path(root)

    path.write_bytes(
        path.read_bytes()
        + b"\n"
    )


def _record_attack(
    *,
    output: Path,
    root: Path,
    attack_id: str,
    title: str,
    expected_signal: RetentionExportAttackSignal,
) -> RetentionExportAttackRecord:
    """Verify and record one attacked export."""

    verification = _verify(root)

    actual_signals = _signals(
        verification
    )

    detected = (
        verification.status
        is RetentionExportVerificationStatus.INVALID
    )

    record = RetentionExportAttackRecord(
        attack_id=attack_id,
        title=title,
        artifact_relative_path=(
            root.relative_to(output).as_posix()
        ),
        expected_signal=expected_signal,
        actual_signals=actual_signals,
        export_valid=verification.valid,
        detected=detected,
        correctly_localized=(
            expected_signal in actual_signals
        ),
        manifest_hash_valid=(
            verification.manifest_hash_valid
        ),
        artifact_set_valid=(
            verification.artifact_set_valid
        ),
        artifact_hashes_valid=(
            verification.artifact_hashes_valid
        ),
        provenance_valid=(
            verification.provenance_valid
        ),
        issues=verification.issues,
    )

    _write_json(
        root / "attack_result.json",
        record,
    )

    return record


def _ratio(
    numerator: int,
    denominator: int,
) -> float:
    """Return a safe ratio."""

    if denominator == 0:
        return 0.0

    return numerator / denominator


def run_retention_export_experiment(
    output_dir: str | Path,
    *,
    registry_key: bytes,
    witness_key: bytes,
    overwrite: bool = False,
) -> RetentionExportExperimentSummary:
    """Run the retention-export integrity attack matrix."""

    if not registry_key:
        raise RetentionExportExperimentError(
            "registry_key must not be empty"
        )

    if not witness_key:
        raise RetentionExportExperimentError(
            "witness_key must not be empty"
        )

    if registry_key == witness_key:
        raise RetentionExportExperimentError(
            "registry_key and witness_key "
            "must be distinct"
        )

    output = Path(output_dir)

    _prepare_output(
        output,
        overwrite=overwrite,
    )

    baseline = output / "baseline"
    attacks_root = output / "attacks"

    try:
        _create_baseline(
            baseline,
            registry_key=registry_key,
            witness_key=witness_key,
        )

        baseline_verification = _verify(
            baseline
        )

        baseline_valid = (
            baseline_verification.status
            is RetentionExportVerificationStatus.VALID
        )

        attacks: list[
            RetentionExportAttackRecord
        ] = []

        artifact_tampering = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="exported_artifact_tampering",
        )

        _tamper_exported_artifact(
            artifact_tampering
        )

        attacks.append(
            _record_attack(
                output=output,
                root=artifact_tampering,
                attack_id=(
                    "exported_artifact_tampering"
                ),
                title=(
                    "Modify one exported artifact"
                ),
                expected_signal=(
                    RetentionExportAttackSignal
                    .ARTIFACT_HASH
                ),
            )
        )

        artifact_deletion = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="exported_artifact_deletion",
        )

        _delete_exported_artifact(
            artifact_deletion
        )

        attacks.append(
            _record_attack(
                output=output,
                root=artifact_deletion,
                attack_id=(
                    "exported_artifact_deletion"
                ),
                title=(
                    "Delete one expected "
                    "exported artifact"
                ),
                expected_signal=(
                    RetentionExportAttackSignal
                    .ARTIFACT_SET
                ),
            )
        )

        restricted_injection = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="restricted_artifact_injection",
        )

        _inject_restricted_artifact(
            restricted_injection
        )

        attacks.append(
            _record_attack(
                output=output,
                root=restricted_injection,
                attack_id=(
                    "restricted_artifact_injection"
                ),
                title=(
                    "Inject one restricted artifact "
                    "excluded by the retention policy"
                ),
                expected_signal=(
                    RetentionExportAttackSignal
                    .ARTIFACT_SET
                ),
            )
        )

        manifest_tampering = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="export_manifest_tampering",
        )

        _tamper_export_manifest(
            manifest_tampering
        )

        attacks.append(
            _record_attack(
                output=output,
                root=manifest_tampering,
                attack_id=(
                    "export_manifest_tampering"
                ),
                title=(
                    "Modify the export manifest "
                    "without updating its hash"
                ),
                expected_signal=(
                    RetentionExportAttackSignal
                    .EXPORT_MANIFEST
                ),
            )
        )

        inventory_tampering = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="inventory_provenance_tampering",
        )

        _tamper_inventory_provenance(
            inventory_tampering
        )

        attacks.append(
            _record_attack(
                output=output,
                root=inventory_tampering,
                attack_id=(
                    "inventory_provenance_tampering"
                ),
                title=(
                    "Modify the referenced artifact "
                    "inventory after export"
                ),
                expected_signal=(
                    RetentionExportAttackSignal
                    .PROVENANCE
                ),
            )
        )

        retention_tampering = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="retention_provenance_tampering",
        )

        _tamper_retention_provenance(
            retention_tampering
        )

        attacks.append(
            _record_attack(
                output=output,
                root=retention_tampering,
                attack_id=(
                    "retention_provenance_tampering"
                ),
                title=(
                    "Modify the referenced retention "
                    "decision after export"
                ),
                expected_signal=(
                    RetentionExportAttackSignal
                    .PROVENANCE
                ),
            )
        )

    except Exception:
        shutil.rmtree(
            output,
            ignore_errors=True,
        )
        raise

    detected = sum(
        attack.detected
        for attack in attacks
    )

    localized = sum(
        attack.correctly_localized
        for attack in attacks
    )

    summary = RetentionExportExperimentSummary(
        generated_at=BASE_TIME,
        baseline_valid=baseline_valid,
        attack_count=len(attacks),
        detected_attacks=detected,
        attack_detection_rate=_ratio(
            detected,
            len(attacks),
        ),
        correctly_localized_attacks=localized,
        localization_accuracy=_ratio(
            localized,
            len(attacks),
        ),
        attacks=attacks,
    )

    _write_json(
        output / "summary.json",
        summary,
    )

    return summary


def main() -> None:
    """Run the retention-export attack experiment CLI."""

    parser = argparse.ArgumentParser(
        description=(
            "Run retention-export integrity "
            "attack experiments."
        )
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    arguments = parser.parse_args()

    registry_key = os.environ.get(
        "EG_RUNTIME_REGISTRY_KEY"
    )

    witness_key = os.environ.get(
        "EG_RUNTIME_WITNESS_KEY"
    )

    if not registry_key:
        raise SystemExit(
            "EG_RUNTIME_REGISTRY_KEY is required"
        )

    if not witness_key:
        raise SystemExit(
            "EG_RUNTIME_WITNESS_KEY is required"
        )

    summary = run_retention_export_experiment(
        arguments.output,
        registry_key=registry_key.encode("utf-8"),
        witness_key=witness_key.encode("utf-8"),
        overwrite=arguments.overwrite,
    )

    print(
        "baseline_valid="
        f"{summary.baseline_valid}"
    )

    print(
        "attack_count="
        f"{summary.attack_count}"
    )

    print(
        "attack_detection_rate="
        f"{summary.attack_detection_rate}"
    )

    print(
        "localization_accuracy="
        f"{summary.localization_accuracy}"
    )


if __name__ == "__main__":
    main()
