from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from eg_runtime.approval_package import (
    ApprovalArtifactPackageError,
    ApprovalPackageVerificationStatus,
    verify_approval_artifact_package,
)
from eg_runtime.approval_registry import (
    ApprovalRegistryError,
    ApprovalRegistryVerificationStatus,
    register_approval_package,
    verify_approval_registry,
)
from eg_runtime.approval_simulation import (
    ApprovalWorkflowSimulationSummary,
    run_approval_workflow_simulation,
)
from eg_runtime.approval_witness import (
    ApprovalWitnessError,
    ApprovalWitnessVerificationStatus,
    create_approval_witness_receipt,
    verify_approval_witness_receipt,
)
from eg_runtime.models import FrozenModel


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    40,
    tzinfo=UTC,
)


class ApprovalIntegrityExperimentError(RuntimeError):
    """Raised when the integrity experiment cannot run."""


class DetectionLayer(StrEnum):
    """The first layer that detects an integrity attack."""

    NONE = "none"
    PACKAGE = "package"
    REGISTRY = "registry"
    WITNESS = "witness"


class IntegrityAttackRecord(FrozenModel):
    """Result of one layered integrity attack."""

    attack_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    artifact_relative_path: str = Field(min_length=1)

    expected_first_detection: DetectionLayer
    actual_first_detection: DetectionLayer

    package_valid: bool
    registry_valid: bool
    witness_valid: bool

    detected: bool
    correctly_localized: bool

    package_issues: list[str] = Field(
        default_factory=list
    )

    registry_issues: list[str] = Field(
        default_factory=list
    )

    witness_issues: list[str] = Field(
        default_factory=list
    )


class ApprovalIntegrityExperimentSummary(FrozenModel):
    """Summary of the layered integrity experiment."""

    experiment_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )

    generated_at: datetime

    baseline_packages_valid: bool
    baseline_registry_valid: bool
    baseline_witness_valid: bool
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

    attacks: list[IntegrityAttackRecord]


def _canonical_json(
    value: Any,
) -> str:
    """Serialize JSON deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _write_json(
    path: Path,
    value: Any,
) -> None:
    """Write human-readable deterministic JSON."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            value,
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
            raise ApprovalIntegrityExperimentError(
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


def _workflow_root(
    root: Path,
) -> Path:
    return root / "approval-workflows"


def _registry_path(
    root: Path,
) -> Path:
    return (
        root
        / "approval-registry"
        / "registry.jsonl"
    )


def _checkpoint_path(
    root: Path,
) -> Path:
    return (
        root
        / "approval-registry"
        / "checkpoint.json"
    )


def _receipt_path(
    root: Path,
) -> Path:
    return (
        root
        / "approval-witness"
        / "receipt.json"
    )


def _package_path(
    root: Path,
    relative_path: str,
) -> Path:
    return (
        _workflow_root(root)
        / relative_path
    )


def _approved_record(
    summary: ApprovalWorkflowSimulationSummary,
):
    matches = [
        record
        for record in summary.records
        if record.resolution_status.value == "approved"
    ]

    if len(matches) != 1:
        raise ApprovalIntegrityExperimentError(
            "expected exactly one approved workflow"
        )

    return matches[0]


def _create_baseline(
    root: Path,
    *,
    registry_key: bytes,
    witness_key: bytes,
) -> ApprovalWorkflowSimulationSummary:
    """Create the valid baseline artifact hierarchy."""

    root.mkdir(
        parents=True,
        exist_ok=False,
    )

    summary = run_approval_workflow_simulation(
        _workflow_root(root)
    )

    for offset, record in enumerate(
        summary.records
    ):
        register_approval_package(
            registry_path=_registry_path(root),
            checkpoint_path=_checkpoint_path(root),
            package_root=root,
            package_dir=_package_path(
                root,
                record.package_relative_path,
            ),
            secret_key=registry_key,
            recorded_at=(
                BASE_TIME
                + timedelta(seconds=offset)
            ),
        )

    create_approval_witness_receipt(
        output_path=_receipt_path(root),
        registry_path=_registry_path(root),
        checkpoint_path=_checkpoint_path(root),
        package_root=root,
        registry_key=registry_key,
        witness_key=witness_key,
        witness_id="integrity-witness-01",
        observed_at=BASE_TIME + timedelta(
            minutes=1
        ),
    )

    return summary


def _verify_packages(
    root: Path,
    summary: ApprovalWorkflowSimulationSummary,
) -> tuple[bool, list[str]]:
    """Verify all workflow packages."""

    valid = True
    issues: list[str] = []

    for record in summary.records:
        package = _package_path(
            root,
            record.package_relative_path,
        )

        try:
            result = verify_approval_artifact_package(
                package
            )
        except ApprovalArtifactPackageError:
            valid = False
            issues.append(
                f"{record.workflow_id}:verification_error"
            )
            continue

        if (
            result.status
            is not ApprovalPackageVerificationStatus.VALID
        ):
            valid = False

            issues.extend(
                f"{record.workflow_id}:{issue}"
                for issue in result.issues
            )

    return valid, issues


def _verify_registry(
    root: Path,
    *,
    registry_key: bytes,
) -> tuple[bool, list[str]]:
    """Verify the authenticated package registry."""

    try:
        result = verify_approval_registry(
            registry_path=_registry_path(root),
            checkpoint_path=_checkpoint_path(root),
            package_root=root,
            secret_key=registry_key,
        )
    except ApprovalRegistryError:
        return False, ["verification_error"]

    return (
        result.status
        is ApprovalRegistryVerificationStatus.VALID,
        result.issues,
    )


def _verify_witness(
    root: Path,
    *,
    registry_key: bytes,
    witness_key: bytes,
) -> tuple[bool, list[str]]:
    """Verify the independent witness receipt."""

    try:
        result = verify_approval_witness_receipt(
            receipt_path=_receipt_path(root),
            registry_path=_registry_path(root),
            checkpoint_path=_checkpoint_path(root),
            package_root=root,
            registry_key=registry_key,
            witness_key=witness_key,
        )
    except ApprovalWitnessError:
        return False, ["verification_error"]

    return (
        result.status
        is ApprovalWitnessVerificationStatus.VALID,
        result.issues,
    )


def _first_detection(
    *,
    package_valid: bool,
    registry_valid: bool,
    witness_valid: bool,
) -> DetectionLayer:
    """Return the first invalid verification layer."""

    if not package_valid:
        return DetectionLayer.PACKAGE

    if not registry_valid:
        return DetectionLayer.REGISTRY

    if not witness_valid:
        return DetectionLayer.WITNESS

    return DetectionLayer.NONE


def _evaluate_state(
    root: Path,
    summary: ApprovalWorkflowSimulationSummary,
    *,
    registry_key: bytes,
    witness_key: bytes,
) -> tuple[
    bool,
    list[str],
    bool,
    list[str],
    bool,
    list[str],
]:
    """Verify all three integrity layers."""

    package_valid, package_issues = (
        _verify_packages(
            root,
            summary,
        )
    )

    registry_valid, registry_issues = (
        _verify_registry(
            root,
            registry_key=registry_key,
        )
    )

    witness_valid, witness_issues = (
        _verify_witness(
            root,
            registry_key=registry_key,
            witness_key=witness_key,
        )
    )

    return (
        package_valid,
        package_issues,
        registry_valid,
        registry_issues,
        witness_valid,
        witness_issues,
    )


def _record_attack(
    *,
    output: Path,
    root: Path,
    summary: ApprovalWorkflowSimulationSummary,
    registry_key: bytes,
    witness_key: bytes,
    attack_id: str,
    title: str,
    expected_first_detection: DetectionLayer,
) -> IntegrityAttackRecord:
    """Verify and record one attacked artifact hierarchy."""

    (
        package_valid,
        package_issues,
        registry_valid,
        registry_issues,
        witness_valid,
        witness_issues,
    ) = _evaluate_state(
        root,
        summary,
        registry_key=registry_key,
        witness_key=witness_key,
    )

    actual = _first_detection(
        package_valid=package_valid,
        registry_valid=registry_valid,
        witness_valid=witness_valid,
    )

    record = IntegrityAttackRecord(
        attack_id=attack_id,
        title=title,
        artifact_relative_path=(
            root.relative_to(output).as_posix()
        ),
        expected_first_detection=(
            expected_first_detection
        ),
        actual_first_detection=actual,
        package_valid=package_valid,
        registry_valid=registry_valid,
        witness_valid=witness_valid,
        detected=actual is not DetectionLayer.NONE,
        correctly_localized=(
            actual is expected_first_detection
        ),
        package_issues=package_issues,
        registry_issues=registry_issues,
        witness_issues=witness_issues,
    )

    _write_json(
        root / "attack_result.json",
        record.model_dump(mode="json"),
    )

    return record


def _copy_attack_root(
    *,
    baseline: Path,
    attacks_root: Path,
    attack_id: str,
) -> Path:
    """Copy the baseline into one attack directory."""

    attack_root = attacks_root / attack_id

    shutil.copytree(
        baseline,
        attack_root,
    )

    return attack_root


def _tamper_resolution(
    root: Path,
    summary: ApprovalWorkflowSimulationSummary,
) -> None:
    """Modify a resolution without updating its manifest."""

    approved = _approved_record(summary)

    path = (
        _package_path(
            root,
            approved.package_relative_path,
        )
        / "workflow"
        / "resolution.json"
    )

    raw = json.loads(
        path.read_text(encoding="utf-8")
    )

    raw["reason"] = (
        "Altered resolution after packaging."
    )

    _write_json(path, raw)


def _rehash_manifest(
    root: Path,
    summary: ApprovalWorkflowSimulationSummary,
) -> None:
    """Modify and internally rehash one package manifest."""

    approved = _approved_record(summary)

    path = (
        _package_path(
            root,
            approved.package_relative_path,
        )
        / "manifest.json"
    )

    raw = json.loads(
        path.read_text(encoding="utf-8")
    )

    raw["created_at"] = (
        "2026-07-22T10:11:00Z"
    )

    content = {
        key: value
        for key, value in raw.items()
        if key != "manifest_hash"
    }

    raw["manifest_hash"] = hashlib.sha256(
        _canonical_json(content).encode(
            "utf-8"
        )
    ).hexdigest()

    _write_json(path, raw)


def _tamper_registry_entry(
    root: Path,
) -> None:
    """Modify one registry entry without reauthentication."""

    path = _registry_path(root)

    lines = path.read_text(
        encoding="utf-8"
    ).splitlines()

    first = json.loads(lines[0])

    first["recorded_at"] = (
        "2026-07-22T12:40:00Z"
    )

    lines[0] = _canonical_json(first)

    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _delete_registry_tail(
    root: Path,
) -> None:
    """Delete the final registry entry."""

    path = _registry_path(root)

    lines = path.read_text(
        encoding="utf-8"
    ).splitlines()

    if len(lines) < 2:
        raise ApprovalIntegrityExperimentError(
            "registry tail attack requires "
            "at least two entries"
        )

    path.write_text(
        "\n".join(lines[:-1]) + "\n",
        encoding="utf-8",
    )


def _rollback_registry_validly(
    root: Path,
    summary: ApprovalWorkflowSimulationSummary,
    *,
    registry_key: bytes,
) -> None:
    """Replace the registry with a valid earlier snapshot."""

    alternate = root / "alternate-registry"

    alternate_registry = (
        alternate / "registry.jsonl"
    )

    alternate_checkpoint = (
        alternate / "checkpoint.json"
    )

    first_record = summary.records[0]

    register_approval_package(
        registry_path=alternate_registry,
        checkpoint_path=alternate_checkpoint,
        package_root=root,
        package_dir=_package_path(
            root,
            first_record.package_relative_path,
        ),
        secret_key=registry_key,
        recorded_at=BASE_TIME,
    )

    _registry_path(root).write_bytes(
        alternate_registry.read_bytes()
    )

    _checkpoint_path(root).write_bytes(
        alternate_checkpoint.read_bytes()
    )

    shutil.rmtree(alternate)


def _tamper_witness_receipt(
    root: Path,
) -> None:
    """Modify the independent witness receipt."""

    path = _receipt_path(root)

    raw = json.loads(
        path.read_text(encoding="utf-8")
    )

    raw["witness_id"] = "altered-witness"

    _write_json(path, raw)


def _ratio(
    numerator: int,
    denominator: int,
) -> float:
    """Return a safe ratio."""

    if denominator == 0:
        return 0.0

    return numerator / denominator


def run_approval_integrity_experiment(
    output_dir: str | Path,
    *,
    registry_key: bytes,
    witness_key: bytes,
    overwrite: bool = False,
) -> ApprovalIntegrityExperimentSummary:
    """Run the layered approval-integrity attack matrix."""

    if registry_key == witness_key:
        raise ApprovalIntegrityExperimentError(
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
        workflow_summary = _create_baseline(
            baseline,
            registry_key=registry_key,
            witness_key=witness_key,
        )

        (
            baseline_packages_valid,
            _,
            baseline_registry_valid,
            _,
            baseline_witness_valid,
            _,
        ) = _evaluate_state(
            baseline,
            workflow_summary,
            registry_key=registry_key,
            witness_key=witness_key,
        )

        attacks: list[IntegrityAttackRecord] = []

        resolution_root = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="resolution_tampering",
        )

        _tamper_resolution(
            resolution_root,
            workflow_summary,
        )

        attacks.append(
            _record_attack(
                output=output,
                root=resolution_root,
                summary=workflow_summary,
                registry_key=registry_key,
                witness_key=witness_key,
                attack_id="resolution_tampering",
                title=(
                    "Modify a packaged human "
                    "resolution without rehashing"
                ),
                expected_first_detection=(
                    DetectionLayer.PACKAGE
                ),
            )
        )

        manifest_root = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="internally_rehashed_manifest",
        )

        _rehash_manifest(
            manifest_root,
            workflow_summary,
        )

        attacks.append(
            _record_attack(
                output=output,
                root=manifest_root,
                summary=workflow_summary,
                registry_key=registry_key,
                witness_key=witness_key,
                attack_id=(
                    "internally_rehashed_manifest"
                ),
                title=(
                    "Modify and internally rehash "
                    "a package manifest"
                ),
                expected_first_detection=(
                    DetectionLayer.REGISTRY
                ),
            )
        )

        entry_root = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="registry_entry_tampering",
        )

        _tamper_registry_entry(entry_root)

        attacks.append(
            _record_attack(
                output=output,
                root=entry_root,
                summary=workflow_summary,
                registry_key=registry_key,
                witness_key=witness_key,
                attack_id="registry_entry_tampering",
                title=(
                    "Modify an authenticated "
                    "registry entry"
                ),
                expected_first_detection=(
                    DetectionLayer.REGISTRY
                ),
            )
        )

        tail_root = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="registry_tail_deletion",
        )

        _delete_registry_tail(tail_root)

        attacks.append(
            _record_attack(
                output=output,
                root=tail_root,
                summary=workflow_summary,
                registry_key=registry_key,
                witness_key=witness_key,
                attack_id="registry_tail_deletion",
                title=(
                    "Delete the final registry entry"
                ),
                expected_first_detection=(
                    DetectionLayer.REGISTRY
                ),
            )
        )

        rollback_root = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="valid_registry_rollback",
        )

        _rollback_registry_validly(
            rollback_root,
            workflow_summary,
            registry_key=registry_key,
        )

        attacks.append(
            _record_attack(
                output=output,
                root=rollback_root,
                summary=workflow_summary,
                registry_key=registry_key,
                witness_key=witness_key,
                attack_id="valid_registry_rollback",
                title=(
                    "Replace the registry with a "
                    "validly authenticated earlier state"
                ),
                expected_first_detection=(
                    DetectionLayer.WITNESS
                ),
            )
        )

        witness_root = _copy_attack_root(
            baseline=baseline,
            attacks_root=attacks_root,
            attack_id="witness_receipt_tampering",
        )

        _tamper_witness_receipt(witness_root)

        attacks.append(
            _record_attack(
                output=output,
                root=witness_root,
                summary=workflow_summary,
                registry_key=registry_key,
                witness_key=witness_key,
                attack_id="witness_receipt_tampering",
                title=(
                    "Modify the independent "
                    "witness receipt"
                ),
                expected_first_detection=(
                    DetectionLayer.WITNESS
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

    baseline_valid = (
        baseline_packages_valid
        and baseline_registry_valid
        and baseline_witness_valid
    )

    summary = ApprovalIntegrityExperimentSummary(
        generated_at=BASE_TIME + timedelta(
            minutes=2
        ),
        baseline_packages_valid=(
            baseline_packages_valid
        ),
        baseline_registry_valid=(
            baseline_registry_valid
        ),
        baseline_witness_valid=(
            baseline_witness_valid
        ),
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
        summary.model_dump(mode="json"),
    )

    return summary


def main() -> None:
    """Run the integrity attack experiment CLI."""

    parser = argparse.ArgumentParser(
        description=(
            "Run layered approval-integrity "
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

    summary = run_approval_integrity_experiment(
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
