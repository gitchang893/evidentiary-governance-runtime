from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import Field, ValidationError

from eg_runtime.artifact_footprint import (
    ArtifactFootprintSummary,
    ArtifactInventoryEntry,
    ArtifactKind,
    ArtifactSensitivity,
)
from eg_runtime.models import FrozenModel


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    50,
    tzinfo=UTC,
)


class RetentionExperimentError(RuntimeError):
    """Raised when a retention experiment cannot run."""


class RetentionPolicy(StrEnum):
    """Counterfactual evidence-retention policies."""

    FULL = "full"
    BASELINE_ONLY = "baseline_only"
    RESEARCH_MINIMAL = "research_minimal"


class RetentionClaim(StrEnum):
    """Claims supported by retained artifact categories."""

    AGGREGATE_RESULTS = "aggregate_results"
    ATTACK_FORENSICS = "attack_forensics"
    RUNTIME_REPLAY = "runtime_replay"
    APPROVAL_REQUEST_DETAIL = "approval_request_detail"
    HUMAN_RESOLUTION_DETAIL = "human_resolution_detail"
    PACKAGE_INTEGRITY = "package_integrity"
    REGISTRY_INTEGRITY = "registry_integrity"
    ROLLBACK_WITNESS = "rollback_witness"


class RetentionPolicyResult(FrozenModel):
    """Storage and evidence result for one retention policy."""

    policy: RetentionPolicy

    source_artifact_count: int = Field(ge=0)
    retained_artifact_count: int = Field(ge=0)
    removed_artifact_count: int = Field(ge=0)

    source_bytes: int = Field(ge=0)
    retained_bytes: int = Field(ge=0)
    removed_bytes: int = Field(ge=0)
    storage_reduction_ratio: float = Field(
        ge=0.0,
        le=1.0,
    )

    source_restricted_artifact_count: int = Field(ge=0)
    retained_restricted_artifact_count: int = Field(ge=0)

    source_restricted_bytes: int = Field(ge=0)
    retained_restricted_bytes: int = Field(ge=0)
    restricted_byte_reduction_ratio: float = Field(
        ge=0.0,
        le=1.0,
    )

    retained_unique_content_count: int = Field(ge=0)
    retained_unique_content_bytes: int = Field(ge=0)
    retained_duplicate_artifact_count: int = Field(ge=0)
    retained_duplicated_bytes: int = Field(ge=0)

    supported_claim_count: int = Field(ge=0)
    total_claim_count: int = Field(ge=1)
    claim_coverage: float = Field(
        ge=0.0,
        le=1.0,
    )
    claim_support: dict[str, bool]

    retained_paths: list[str]
    removed_paths: list[str]


class RetentionExperimentSummary(FrozenModel):
    """Summary of all counterfactual retention policies."""

    experiment_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    generated_at: datetime
    source_root_label: str = Field(min_length=1)
    source_artifact_count: int = Field(ge=0)
    source_total_bytes: int = Field(ge=0)
    policies: dict[str, RetentionPolicyResult]


def _ratio(
    numerator: int,
    denominator: int,
) -> float:
    """Return a safe ratio."""

    if denominator == 0:
        return 0.0

    return numerator / denominator


def _prepare_output(
    output: Path,
    *,
    overwrite: bool,
) -> None:
    """Create a clean retention output directory."""

    if output.exists():
        if not overwrite:
            raise RetentionExperimentError(
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


def _retain_entry(
    entry: ArtifactInventoryEntry,
    policy: RetentionPolicy,
) -> bool:
    """Return whether one policy retains an artifact."""

    path = entry.relative_path

    if policy is RetentionPolicy.FULL:
        return True

    if policy is RetentionPolicy.BASELINE_ONLY:
        return (
            path == "summary.json"
            or path.startswith("baseline/")
        )

    if policy is RetentionPolicy.RESEARCH_MINIMAL:
        if path == "summary.json":
            return True

        if path.endswith("/attack_result.json"):
            return True

        if not path.startswith("baseline/"):
            return False

        return entry.kind in {
            ArtifactKind.EXPERIMENT_SUMMARY,
            ArtifactKind.PACKAGE_MANIFEST,
            ArtifactKind.APPROVAL_AUDIT,
            ArtifactKind.REGISTRY,
            ArtifactKind.REGISTRY_CHECKPOINT,
            ArtifactKind.WITNESS_RECEIPT,
        }

    raise RetentionExperimentError(
        f"unsupported retention policy: {policy}"
    )


def _claim_support(
    retained: list[ArtifactInventoryEntry],
) -> dict[str, bool]:
    """Calculate claim support from retained artifact kinds."""

    kinds = {
        entry.kind
        for entry in retained
    }

    paths = {
        entry.relative_path
        for entry in retained
    }

    support = {
        RetentionClaim.AGGREGATE_RESULTS.value: (
            "summary.json" in paths
        ),
        RetentionClaim.ATTACK_FORENSICS.value: (
            ArtifactKind.ATTACK_RESULT in kinds
        ),
        RetentionClaim.RUNTIME_REPLAY.value: (
            ArtifactKind.RUNTIME_EVIDENCE in kinds
        ),
        RetentionClaim.APPROVAL_REQUEST_DETAIL.value: (
            ArtifactKind.PENDING_APPROVAL in kinds
        ),
        RetentionClaim.HUMAN_RESOLUTION_DETAIL.value: (
            ArtifactKind.HUMAN_RESOLUTION in kinds
        ),
        RetentionClaim.PACKAGE_INTEGRITY.value: (
            ArtifactKind.PACKAGE_MANIFEST in kinds
            and ArtifactKind.APPROVAL_AUDIT in kinds
        ),
        RetentionClaim.REGISTRY_INTEGRITY.value: (
            ArtifactKind.REGISTRY in kinds
            and ArtifactKind.REGISTRY_CHECKPOINT in kinds
        ),
        RetentionClaim.ROLLBACK_WITNESS.value: (
            ArtifactKind.WITNESS_RECEIPT in kinds
        ),
    }

    return support


def _content_metrics(
    entries: list[ArtifactInventoryEntry],
) -> tuple[int, int, int, int]:
    """Calculate retained unique and duplicate content metrics."""

    groups: dict[
        str,
        list[ArtifactInventoryEntry],
    ] = defaultdict(list)

    for entry in entries:
        groups[entry.sha256].append(entry)

    unique_bytes = sum(
        group[0].byte_count
        for group in groups.values()
    )

    duplicate_count = sum(
        len(group) - 1
        for group in groups.values()
    )

    duplicated_bytes = sum(
        sum(
            entry.byte_count
            for entry in group[1:]
        )
        for group in groups.values()
    )

    return (
        len(groups),
        unique_bytes,
        duplicate_count,
        duplicated_bytes,
    )


def _evaluate_policy(
    inventory: ArtifactFootprintSummary,
    policy: RetentionPolicy,
) -> RetentionPolicyResult:
    """Evaluate one policy without modifying source artifacts."""

    retained = [
        entry
        for entry in inventory.entries
        if _retain_entry(entry, policy)
    ]

    removed = [
        entry
        for entry in inventory.entries
        if not _retain_entry(entry, policy)
    ]

    retained_bytes = sum(
        entry.byte_count
        for entry in retained
    )

    removed_bytes = sum(
        entry.byte_count
        for entry in removed
    )

    retained_restricted = [
        entry
        for entry in retained
        if (
            entry.sensitivity
            is ArtifactSensitivity.RESTRICTED
        )
    ]

    source_restricted = [
        entry
        for entry in inventory.entries
        if (
            entry.sensitivity
            is ArtifactSensitivity.RESTRICTED
        )
    ]

    retained_restricted_bytes = sum(
        entry.byte_count
        for entry in retained_restricted
    )

    source_restricted_bytes = sum(
        entry.byte_count
        for entry in source_restricted
    )

    (
        unique_count,
        unique_bytes,
        duplicate_count,
        duplicated_bytes,
    ) = _content_metrics(retained)

    support = _claim_support(retained)

    supported_claims = sum(
        support.values()
    )

    total_claims = len(support)

    return RetentionPolicyResult(
        policy=policy,
        source_artifact_count=(
            inventory.artifact_count
        ),
        retained_artifact_count=len(retained),
        removed_artifact_count=len(removed),
        source_bytes=inventory.total_bytes,
        retained_bytes=retained_bytes,
        removed_bytes=removed_bytes,
        storage_reduction_ratio=_ratio(
            removed_bytes,
            inventory.total_bytes,
        ),
        source_restricted_artifact_count=len(
            source_restricted
        ),
        retained_restricted_artifact_count=len(
            retained_restricted
        ),
        source_restricted_bytes=(
            source_restricted_bytes
        ),
        retained_restricted_bytes=(
            retained_restricted_bytes
        ),
        restricted_byte_reduction_ratio=_ratio(
            (
                source_restricted_bytes
                - retained_restricted_bytes
            ),
            source_restricted_bytes,
        ),
        retained_unique_content_count=unique_count,
        retained_unique_content_bytes=unique_bytes,
        retained_duplicate_artifact_count=(
            duplicate_count
        ),
        retained_duplicated_bytes=(
            duplicated_bytes
        ),
        supported_claim_count=supported_claims,
        total_claim_count=total_claims,
        claim_coverage=_ratio(
            supported_claims,
            total_claims,
        ),
        claim_support=support,
        retained_paths=sorted(
            entry.relative_path
            for entry in retained
        ),
        removed_paths=sorted(
            entry.relative_path
            for entry in removed
        ),
    )


def _write_json(
    path: Path,
    value: object,
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


def _write_policy_csv(
    path: Path,
    results: list[RetentionPolicyResult],
) -> None:
    """Write one summary row per retention policy."""

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "policy",
                "retained_artifact_count",
                "removed_artifact_count",
                "retained_bytes",
                "removed_bytes",
                "storage_reduction_ratio",
                "retained_restricted_artifact_count",
                "retained_restricted_bytes",
                "restricted_byte_reduction_ratio",
                "retained_duplicate_artifact_count",
                "retained_duplicated_bytes",
                "supported_claim_count",
                "total_claim_count",
                "claim_coverage",
            ],
        )

        writer.writeheader()

        for result in results:
            writer.writerow(
                {
                    "policy": result.policy.value,
                    "retained_artifact_count": (
                        result.retained_artifact_count
                    ),
                    "removed_artifact_count": (
                        result.removed_artifact_count
                    ),
                    "retained_bytes": (
                        result.retained_bytes
                    ),
                    "removed_bytes": (
                        result.removed_bytes
                    ),
                    "storage_reduction_ratio": (
                        result.storage_reduction_ratio
                    ),
                    "retained_restricted_artifact_count": (
                        result
                        .retained_restricted_artifact_count
                    ),
                    "retained_restricted_bytes": (
                        result.retained_restricted_bytes
                    ),
                    "restricted_byte_reduction_ratio": (
                        result
                        .restricted_byte_reduction_ratio
                    ),
                    "retained_duplicate_artifact_count": (
                        result
                        .retained_duplicate_artifact_count
                    ),
                    "retained_duplicated_bytes": (
                        result.retained_duplicated_bytes
                    ),
                    "supported_claim_count": (
                        result.supported_claim_count
                    ),
                    "total_claim_count": (
                        result.total_claim_count
                    ),
                    "claim_coverage": (
                        result.claim_coverage
                    ),
                }
            )


def run_retention_experiment(
    inventory: ArtifactFootprintSummary,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    generated_at: datetime = BASE_TIME,
) -> RetentionExperimentSummary:
    """Evaluate counterfactual retention policies."""

    if (
        generated_at.tzinfo is None
        or generated_at.utcoffset() is None
    ):
        raise RetentionExperimentError(
            "generated_at must be timezone-aware"
        )

    output = Path(output_dir)

    _prepare_output(
        output,
        overwrite=overwrite,
    )

    results = [
        _evaluate_policy(
            inventory,
            policy,
        )
        for policy in RetentionPolicy
    ]

    summary = RetentionExperimentSummary(
        generated_at=generated_at,
        source_root_label=inventory.root_label,
        source_artifact_count=(
            inventory.artifact_count
        ),
        source_total_bytes=(
            inventory.total_bytes
        ),
        policies={
            result.policy.value: result
            for result in results
        },
    )

    _write_json(
        output / "summary.json",
        summary,
    )

    _write_policy_csv(
        output / "policies.csv",
        results,
    )

    for result in results:
        _write_json(
            (
                output
                / result.policy.value
                / "retention_manifest.json"
            ),
            result,
        )

    return summary


def load_artifact_footprint(
    path: str | Path,
) -> ArtifactFootprintSummary:
    """Load a persisted artifact inventory."""

    inventory_path = Path(path)

    if not inventory_path.exists():
        raise RetentionExperimentError(
            f"inventory does not exist: {inventory_path}"
        )

    try:
        raw = json.loads(
            inventory_path.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise RetentionExperimentError(
            "inventory contains invalid JSON"
        ) from exc

    try:
        return ArtifactFootprintSummary.model_validate(
            raw
        )
    except ValidationError as exc:
        raise RetentionExperimentError(
            "inventory has an invalid schema"
        ) from exc


def main() -> None:
    """Run the retention experiment CLI."""

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate storage reduction and "
            "claim coverage under retention policies."
        )
    )

    parser.add_argument(
        "--inventory",
        type=Path,
        required=True,
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

    inventory = load_artifact_footprint(
        arguments.inventory
    )

    summary = run_retention_experiment(
        inventory,
        arguments.output,
        overwrite=arguments.overwrite,
    )

    for policy in RetentionPolicy:
        result = summary.policies[
            policy.value
        ]

        print(
            policy.value,
            "storage_reduction_ratio="
            f"{result.storage_reduction_ratio}",
            "restricted_reduction_ratio="
            f"{result.restricted_byte_reduction_ratio}",
            "claim_coverage="
            f"{result.claim_coverage}",
        )


if __name__ == "__main__":
    main()
