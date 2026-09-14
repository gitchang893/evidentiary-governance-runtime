from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from pydantic import Field

from eg_runtime.models import FrozenModel


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    45,
    tzinfo=UTC,
)


class ArtifactFootprintError(RuntimeError):
    """Raised when an artifact footprint cannot be produced."""


class ArtifactSensitivity(StrEnum):
    """Sensitivity assigned to a persisted artifact."""

    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ArtifactKind(StrEnum):
    """Kinds of evidentiary artifacts in the experiment."""

    RUNTIME_EVIDENCE = "runtime_evidence"
    PENDING_APPROVAL = "pending_approval"
    HUMAN_RESOLUTION = "human_resolution"
    APPROVAL_AUDIT = "approval_audit"
    PACKAGE_MANIFEST = "package_manifest"
    REGISTRY = "registry"
    REGISTRY_CHECKPOINT = "registry_checkpoint"
    WITNESS_RECEIPT = "witness_receipt"
    ATTACK_RESULT = "attack_result"
    EXPERIMENT_SUMMARY = "experiment_summary"
    OTHER = "other"


class ArtifactAggregate(FrozenModel):
    """Storage aggregate for one artifact category."""

    artifact_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)


class ArtifactInventoryEntry(FrozenModel):
    """Location and storage identity of one artifact."""

    relative_path: str = Field(min_length=1)
    kind: ArtifactKind
    sensitivity: ArtifactSensitivity
    byte_count: int = Field(ge=1)
    sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    content_tags: list[str]
    duplicate_group_size: int = Field(ge=1)
    duplicate_copy: bool


class ArtifactFootprintSummary(FrozenModel):
    """Storage and sensitivity summary for an artifact tree."""

    footprint_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    generated_at: datetime
    root_label: str = Field(min_length=1)

    artifact_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)

    unique_content_count: int = Field(ge=0)
    unique_content_bytes: int = Field(ge=0)

    duplicate_artifact_count: int = Field(ge=0)
    duplicated_bytes: int = Field(ge=0)
    duplication_ratio: float = Field(
        ge=0.0,
        le=1.0,
    )

    restricted_artifact_count: int = Field(ge=0)
    restricted_bytes: int = Field(ge=0)

    secret_free: bool
    secret_finding_count: int = Field(ge=0)
    secret_findings: list[str]

    by_kind: dict[str, ArtifactAggregate]
    by_sensitivity: dict[str, ArtifactAggregate]

    entries: list[ArtifactInventoryEntry]


def _require_aware_datetime(
    value: datetime,
) -> None:
    """Require a timezone-aware inventory timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ArtifactFootprintError(
            "generated_at must be timezone-aware"
        )


def _sha256_bytes(
    value: bytes,
) -> str:
    """Return the SHA-256 digest of bytes."""

    return hashlib.sha256(value).hexdigest()


def _ratio(
    numerator: int,
    denominator: int,
) -> float:
    """Return a safe ratio."""

    if denominator == 0:
        return 0.0

    return numerator / denominator


def _classify_artifact(
    relative_path: PurePosixPath,
) -> tuple[
    ArtifactKind,
    ArtifactSensitivity,
    list[str],
]:
    """Classify one persisted artifact by its location."""

    name = relative_path.name
    parts = set(relative_path.parts)

    if (
        name in {
            "original.jsonl",
            "resumed.jsonl",
        }
        and "evidence" in parts
    ):
        return (
            ArtifactKind.RUNTIME_EVIDENCE,
            ArtifactSensitivity.RESTRICTED,
            [
                "runtime_events",
                "context",
                "proposal_arguments",
                "governance_decision",
                "execution_status",
            ],
        )

    if (
        name == "pending.json"
        and "workflow" in parts
    ):
        return (
            ArtifactKind.PENDING_APPROVAL,
            ArtifactSensitivity.RESTRICTED,
            [
                "context",
                "proposal",
                "escalation_decision",
                "approval_request",
            ],
        )

    if (
        name == "resolution.json"
        and "workflow" in parts
    ):
        return (
            ArtifactKind.HUMAN_RESOLUTION,
            ArtifactSensitivity.RESTRICTED,
            [
                "human_decision",
                "resolver",
                "approval",
                "resumed_context",
            ],
        )

    if name == "approval_audit.json":
        return (
            ArtifactKind.APPROVAL_AUDIT,
            ArtifactSensitivity.CONFIDENTIAL,
            [
                "human_decision",
                "lineage",
                "source_hashes",
                "approval_identity",
            ],
        )

    if name == "manifest.json":
        return (
            ArtifactKind.PACKAGE_MANIFEST,
            ArtifactSensitivity.INTERNAL,
            [
                "artifact_locations",
                "artifact_hashes",
                "artifact_sizes",
            ],
        )

    if (
        name == "registry.jsonl"
        and "approval-registry" in parts
    ):
        return (
            ArtifactKind.REGISTRY,
            ArtifactSensitivity.INTERNAL,
            [
                "package_locations",
                "package_hashes",
                "registry_chain",
                "hmac_signatures",
            ],
        )

    if (
        name == "checkpoint.json"
        and "approval-registry" in parts
    ):
        return (
            ArtifactKind.REGISTRY_CHECKPOINT,
            ArtifactSensitivity.INTERNAL,
            [
                "registry_hash",
                "entry_count",
                "final_record_hash",
                "hmac_signature",
            ],
        )

    if (
        name == "receipt.json"
        and "approval-witness" in parts
    ):
        return (
            ArtifactKind.WITNESS_RECEIPT,
            ArtifactSensitivity.INTERNAL,
            [
                "registry_snapshot",
                "checkpoint_snapshot",
                "witness_signature",
            ],
        )

    if name == "attack_result.json":
        return (
            ArtifactKind.ATTACK_RESULT,
            ArtifactSensitivity.INTERNAL,
            [
                "attack_outcome",
                "detection_layer",
                "verification_issues",
            ],
        )

    if name == "summary.json":
        return (
            ArtifactKind.EXPERIMENT_SUMMARY,
            ArtifactSensitivity.INTERNAL,
            [
                "aggregate_metrics",
                "artifact_index",
            ],
        )

    return (
        ArtifactKind.OTHER,
        ArtifactSensitivity.INTERNAL,
        [
            "unclassified_artifact",
        ],
    )


def _collect_files(
    root: Path,
    excluded_paths: set[Path],
) -> list[Path]:
    """Collect regular files in deterministic order."""

    files: list[Path] = []

    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArtifactFootprintError(
                "artifact tree contains a symlink: "
                f"{path}"
            )

        if not path.is_file():
            continue

        if path.resolve() in excluded_paths:
            continue

        files.append(path)

    return sorted(
        files,
        key=lambda value: (
            value.relative_to(root).as_posix()
        ),
    )


def _aggregate(
    entries: Iterable[ArtifactInventoryEntry],
    attribute: str,
) -> dict[str, ArtifactAggregate]:
    """Aggregate counts and bytes by one enum attribute."""

    counts: dict[str, int] = defaultdict(int)
    sizes: dict[str, int] = defaultdict(int)

    for entry in entries:
        value = getattr(entry, attribute)

        key = (
            value.value
            if isinstance(value, StrEnum)
            else str(value)
        )

        counts[key] += 1
        sizes[key] += entry.byte_count

    return {
        key: ArtifactAggregate(
            artifact_count=counts[key],
            byte_count=sizes[key],
        )
        for key in sorted(counts)
    }


def _write_json(
    path: Path,
    summary: ArtifactFootprintSummary,
) -> None:
    """Write a deterministic JSON inventory."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            summary.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_csv(
    path: Path,
    entries: list[ArtifactInventoryEntry],
) -> None:
    """Write one row per persisted artifact."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "relative_path",
                "kind",
                "sensitivity",
                "byte_count",
                "sha256",
                "duplicate_group_size",
                "duplicate_copy",
                "content_tags",
            ],
        )

        writer.writeheader()

        for entry in entries:
            writer.writerow(
                {
                    "relative_path": (
                        entry.relative_path
                    ),
                    "kind": entry.kind.value,
                    "sensitivity": (
                        entry.sensitivity.value
                    ),
                    "byte_count": (
                        entry.byte_count
                    ),
                    "sha256": entry.sha256,
                    "duplicate_group_size": (
                        entry.duplicate_group_size
                    ),
                    "duplicate_copy": (
                        entry.duplicate_copy
                    ),
                    "content_tags": ";".join(
                        entry.content_tags
                    ),
                }
            )


def build_artifact_footprint(
    root_dir: str | Path,
    *,
    json_output: str | Path | None = None,
    csv_output: str | Path | None = None,
    root_label: str | None = None,
    generated_at: datetime = BASE_TIME,
    secret_values: Iterable[bytes] = (),
) -> ArtifactFootprintSummary:
    """Inventory an evidentiary artifact tree."""

    _require_aware_datetime(generated_at)

    root = Path(root_dir)

    if not root.exists() or not root.is_dir():
        raise ArtifactFootprintError(
            f"artifact root does not exist: {root}"
        )

    normalized_root_label = (
        root_label.strip()
        if isinstance(root_label, str)
        else root.name
    )

    if not normalized_root_label:
        raise ArtifactFootprintError(
            "root_label must not be empty"
        )

    json_path = (
        Path(json_output)
        if json_output is not None
        else root / "artifact_inventory.json"
    )

    csv_path = (
        Path(csv_output)
        if csv_output is not None
        else root / "artifact_inventory.csv"
    )

    excluded_paths = {
        json_path.resolve(),
        csv_path.resolve(),
    }

    files = _collect_files(
        root,
        excluded_paths,
    )

    normalized_secrets = [
        value
        for value in secret_values
        if isinstance(value, bytes) and value
    ]

    initial_entries: list[
        ArtifactInventoryEntry
    ] = []

    secret_findings: list[str] = []

    for path in files:
        relative = path.relative_to(
            root
        ).as_posix()

        content = path.read_bytes()

        (
            kind,
            sensitivity,
            tags,
        ) = _classify_artifact(
            PurePosixPath(relative)
        )

        initial_entries.append(
            ArtifactInventoryEntry(
                relative_path=relative,
                kind=kind,
                sensitivity=sensitivity,
                byte_count=len(content),
                sha256=_sha256_bytes(content),
                content_tags=tags,
                duplicate_group_size=1,
                duplicate_copy=False,
            )
        )

        for index, secret in enumerate(
            normalized_secrets,
            start=1,
        ):
            if secret in content:
                secret_findings.append(
                    f"{relative}:secret_{index}"
                )

    groups: dict[
        str,
        list[ArtifactInventoryEntry],
    ] = defaultdict(list)

    for entry in initial_entries:
        groups[entry.sha256].append(entry)

    entries: list[ArtifactInventoryEntry] = []

    unique_content_bytes = 0
    duplicate_artifact_count = 0

    for sha256 in sorted(groups):
        group = sorted(
            groups[sha256],
            key=lambda entry: (
                entry.relative_path
            ),
        )

        unique_content_bytes += (
            group[0].byte_count
        )

        duplicate_artifact_count += (
            len(group) - 1
        )

        for index, entry in enumerate(group):
            entries.append(
                entry.model_copy(
                    update={
                        "duplicate_group_size": (
                            len(group)
                        ),
                        "duplicate_copy": (
                            index > 0
                        ),
                    }
                )
            )

    entries.sort(
        key=lambda entry: entry.relative_path
    )

    total_bytes = sum(
        entry.byte_count
        for entry in entries
    )

    duplicated_bytes = (
        total_bytes - unique_content_bytes
    )

    restricted_entries = [
        entry
        for entry in entries
        if (
            entry.sensitivity
            is ArtifactSensitivity.RESTRICTED
        )
    ]

    summary = ArtifactFootprintSummary(
        generated_at=generated_at,
        root_label=normalized_root_label,
        artifact_count=len(entries),
        total_bytes=total_bytes,
        unique_content_count=len(groups),
        unique_content_bytes=(
            unique_content_bytes
        ),
        duplicate_artifact_count=(
            duplicate_artifact_count
        ),
        duplicated_bytes=duplicated_bytes,
        duplication_ratio=_ratio(
            duplicated_bytes,
            total_bytes,
        ),
        restricted_artifact_count=len(
            restricted_entries
        ),
        restricted_bytes=sum(
            entry.byte_count
            for entry in restricted_entries
        ),
        secret_free=not secret_findings,
        secret_finding_count=len(
            secret_findings
        ),
        secret_findings=sorted(
            secret_findings
        ),
        by_kind=_aggregate(
            entries,
            "kind",
        ),
        by_sensitivity=_aggregate(
            entries,
            "sensitivity",
        ),
        entries=entries,
    )

    _write_json(
        json_path,
        summary,
    )

    _write_csv(
        csv_path,
        entries,
    )

    return summary


def main() -> None:
    """Run the artifact footprint CLI."""

    parser = argparse.ArgumentParser(
        description=(
            "Measure storage, duplication, "
            "sensitivity, and location of "
            "evidentiary artifacts."
        )
    )

    parser.add_argument(
        "--root",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--json-output",
        type=Path,
    )

    parser.add_argument(
        "--csv-output",
        type=Path,
    )

    parser.add_argument(
        "--root-label",
        type=str,
    )

    arguments = parser.parse_args()

    secret_values = [
        value.encode("utf-8")
        for value in [
            os.environ.get(
                "EG_RUNTIME_REGISTRY_KEY"
            ),
            os.environ.get(
                "EG_RUNTIME_WITNESS_KEY"
            ),
        ]
        if value
    ]

    summary = build_artifact_footprint(
        arguments.root,
        json_output=arguments.json_output,
        csv_output=arguments.csv_output,
        root_label=arguments.root_label,
        secret_values=secret_values,
    )

    print(
        "artifact_count="
        f"{summary.artifact_count}"
    )

    print(
        "total_bytes="
        f"{summary.total_bytes}"
    )

    print(
        "duplicate_artifact_count="
        f"{summary.duplicate_artifact_count}"
    )

    print(
        "duplication_ratio="
        f"{summary.duplication_ratio}"
    )

    print(
        "restricted_artifact_count="
        f"{summary.restricted_artifact_count}"
    )

    print(
        "secret_free="
        f"{summary.secret_free}"
    )


if __name__ == "__main__":
    main()
