from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field, ValidationError

from eg_runtime.artifact_footprint import (
    ArtifactFootprintSummary,
    ArtifactInventoryEntry,
)
from eg_runtime.models import FrozenModel
from eg_runtime.retention_experiment import (
    RetentionPolicy,
    RetentionPolicyResult,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    55,
    tzinfo=UTC,
)

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ZERO_HASH = "0" * 64


class RetentionExportError(RuntimeError):
    """Raised when a retention export cannot be processed."""


class RetentionExportVerificationStatus(StrEnum):
    """Verification status for a materialized export."""

    VALID = "valid"
    INVALID = "invalid"


class RetentionExportArtifact(FrozenModel):
    """One artifact copied into a retention export."""

    relative_path: str = Field(min_length=1)
    byte_count: int = Field(ge=1)
    sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )


class RetentionExportManifest(FrozenModel):
    """Integrity manifest for one materialized export."""

    export_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )

    generated_at: datetime
    policy: RetentionPolicy
    source_root_label: str = Field(min_length=1)

    source_inventory_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )

    source_retention_manifest_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )

    artifact_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    artifacts: list[RetentionExportArtifact]

    manifest_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )


class RetentionExportVerification(FrozenModel):
    """Verification result for a materialized export."""

    status: RetentionExportVerificationStatus
    issues: list[str]

    manifest_hash_valid: bool
    artifact_set_valid: bool
    artifact_hashes_valid: bool
    provenance_valid: bool

    expected_artifact_count: int = Field(ge=0)
    verified_artifact_count: int = Field(ge=0)

    @property
    def valid(self) -> bool:
        """Return whether the export is valid."""

        return (
            self.status
            is RetentionExportVerificationStatus.VALID
        )


def _canonical_json(
    value: Any,
) -> str:
    """Serialize JSON-compatible data deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(
    value: bytes,
) -> str:
    """Return the SHA-256 digest of bytes."""

    return hashlib.sha256(value).hexdigest()


def _sha256_file(
    path: Path,
) -> str:
    """Return the SHA-256 digest of a file."""

    return _sha256_bytes(
        path.read_bytes()
    )


def _manifest_hash(
    manifest: RetentionExportManifest,
) -> str:
    """Calculate an export manifest hash."""

    content = manifest.model_dump(
        mode="json",
        exclude={"manifest_hash"},
    )

    return _sha256_bytes(
        _canonical_json(content).encode("utf-8")
    )


def _safe_relative_path(
    value: str,
) -> PurePosixPath:
    """Validate one portable relative artifact path."""

    if "\\" in value:
        raise RetentionExportError(
            f"unsafe artifact path: {value}"
        )

    path = PurePosixPath(value)

    if path.is_absolute():
        raise RetentionExportError(
            f"absolute artifact path: {value}"
        )

    if not path.parts:
        raise RetentionExportError(
            "artifact path must not be empty"
        )

    if any(
        part in {"", ".", ".."}
        for part in path.parts
    ):
        raise RetentionExportError(
            f"unsafe artifact path: {value}"
        )

    if path.as_posix() != value:
        raise RetentionExportError(
            f"non-canonical artifact path: {value}"
        )

    return path


def _load_json(
    path: Path,
) -> Any:
    """Load one JSON file."""

    if not path.exists():
        raise RetentionExportError(
            f"required file does not exist: {path}"
        )

    if path.is_symlink():
        raise RetentionExportError(
            f"required file is a symlink: {path}"
        )

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RetentionExportError(
            f"invalid JSON file: {path}"
        ) from exc


def _load_inventory(
    path: Path,
) -> ArtifactFootprintSummary:
    """Load a persisted artifact inventory."""

    try:
        return ArtifactFootprintSummary.model_validate(
            _load_json(path)
        )
    except ValidationError as exc:
        raise RetentionExportError(
            "artifact inventory has an invalid schema"
        ) from exc


def _load_retention_manifest(
    path: Path,
) -> RetentionPolicyResult:
    """Load a persisted retention policy result."""

    try:
        return RetentionPolicyResult.model_validate(
            _load_json(path)
        )
    except ValidationError as exc:
        raise RetentionExportError(
            "retention manifest has an invalid schema"
        ) from exc


def _load_export_manifest(
    path: Path,
) -> RetentionExportManifest:
    """Load a persisted export manifest."""

    try:
        return RetentionExportManifest.model_validate(
            _load_json(path)
        )
    except ValidationError as exc:
        raise RetentionExportError(
            "export manifest has an invalid schema"
        ) from exc


def _prepare_output(
    output: Path,
    *,
    overwrite: bool,
) -> None:
    """Create a clean export directory."""

    if output.exists():
        if not overwrite:
            raise RetentionExportError(
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


def _ensure_output_outside_source(
    source_root: Path,
    output: Path,
) -> None:
    """Prevent the export from being nested in its source tree."""

    source = source_root.resolve()
    destination = output.resolve(
        strict=False
    )

    if (
        destination == source
        or destination.is_relative_to(source)
    ):
        raise RetentionExportError(
            "output directory must be outside "
            "the source artifact root"
        )


def _inventory_index(
    inventory: ArtifactFootprintSummary,
) -> dict[str, ArtifactInventoryEntry]:
    """Index inventory entries by relative path."""

    index: dict[
        str,
        ArtifactInventoryEntry,
    ] = {}

    for entry in inventory.entries:
        _safe_relative_path(
            entry.relative_path
        )

        if entry.relative_path in index:
            raise RetentionExportError(
                "duplicate inventory path: "
                f"{entry.relative_path}"
            )

        index[entry.relative_path] = entry

    return index


def _validate_retention_partition(
    inventory: ArtifactFootprintSummary,
    retention: RetentionPolicyResult,
) -> list[ArtifactInventoryEntry]:
    """Validate retained and removed path partitions."""

    if (
        retention.source_artifact_count
        != inventory.artifact_count
    ):
        raise RetentionExportError(
            "retention source artifact count mismatch"
        )

    if (
        retention.source_bytes
        != inventory.total_bytes
    ):
        raise RetentionExportError(
            "retention source byte count mismatch"
        )

    retained_paths = retention.retained_paths
    removed_paths = retention.removed_paths

    if len(set(retained_paths)) != len(
        retained_paths
    ):
        raise RetentionExportError(
            "retention manifest contains "
            "duplicate retained paths"
        )

    if len(set(removed_paths)) != len(
        removed_paths
    ):
        raise RetentionExportError(
            "retention manifest contains "
            "duplicate removed paths"
        )

    for value in [
        *retained_paths,
        *removed_paths,
    ]:
        _safe_relative_path(value)

    retained_set = set(retained_paths)
    removed_set = set(removed_paths)

    if retained_set & removed_set:
        raise RetentionExportError(
            "retained and removed paths overlap"
        )

    index = _inventory_index(inventory)
    inventory_paths = set(index)

    if (
        retained_set | removed_set
        != inventory_paths
    ):
        raise RetentionExportError(
            "retention paths do not partition "
            "the artifact inventory"
        )

    if (
        len(retained_paths)
        != retention.retained_artifact_count
    ):
        raise RetentionExportError(
            "retained artifact count mismatch"
        )

    selected = [
        index[path]
        for path in sorted(retained_paths)
    ]

    selected_bytes = sum(
        entry.byte_count
        for entry in selected
    )

    if selected_bytes != retention.retained_bytes:
        raise RetentionExportError(
            "retained byte count mismatch"
        )

    return selected


def _source_file(
    source_root: Path,
    relative_path: str,
) -> Path:
    """Resolve a source artifact without following symlinks."""

    portable = _safe_relative_path(
        relative_path
    )

    current = source_root

    for part in portable.parts:
        current = current / part

        if current.is_symlink():
            raise RetentionExportError(
                "source path contains a symlink: "
                f"{relative_path}"
            )

    if not current.exists():
        raise RetentionExportError(
            "source artifact is missing: "
            f"{relative_path}"
        )

    if not current.is_file():
        raise RetentionExportError(
            "source artifact is not a regular file: "
            f"{relative_path}"
        )

    resolved_root = source_root.resolve()
    resolved_file = current.resolve()

    if not resolved_file.is_relative_to(
        resolved_root
    ):
        raise RetentionExportError(
            "source artifact escapes source root: "
            f"{relative_path}"
        )

    return current


def _write_manifest(
    path: Path,
    manifest: RetentionExportManifest,
) -> None:
    """Write a deterministic export manifest."""

    path.write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def materialize_retention_export(
    *,
    source_root: str | Path,
    inventory_path: str | Path,
    retention_manifest_path: str | Path,
    output_dir: str | Path,
    generated_at: datetime = BASE_TIME,
    overwrite: bool = False,
) -> RetentionExportManifest:
    """Copy retained artifacts into a verified export."""

    if (
        generated_at.tzinfo is None
        or generated_at.utcoffset() is None
    ):
        raise RetentionExportError(
            "generated_at must be timezone-aware"
        )

    source = Path(source_root)

    if not source.exists() or not source.is_dir():
        raise RetentionExportError(
            f"source root does not exist: {source}"
        )

    inventory_file = Path(inventory_path)
    retention_file = Path(
        retention_manifest_path
    )
    output = Path(output_dir)

    _ensure_output_outside_source(
        source,
        output,
    )

    inventory = _load_inventory(
        inventory_file
    )

    retention = _load_retention_manifest(
        retention_file
    )

    selected = _validate_retention_partition(
        inventory,
        retention,
    )

    _prepare_output(
        output,
        overwrite=overwrite,
    )

    artifacts_root = output / "artifacts"

    exported: list[
        RetentionExportArtifact
    ] = []

    try:
        for entry in selected:
            source_file = _source_file(
                source,
                entry.relative_path,
            )

            source_bytes = source_file.read_bytes()
            source_sha256 = _sha256_bytes(
                source_bytes
            )

            if len(source_bytes) != entry.byte_count:
                raise RetentionExportError(
                    "source byte count mismatch: "
                    f"{entry.relative_path}"
                )

            if source_sha256 != entry.sha256:
                raise RetentionExportError(
                    "source hash mismatch: "
                    f"{entry.relative_path}"
                )

            relative = _safe_relative_path(
                entry.relative_path
            )

            destination = (
                artifacts_root
                / Path(*relative.parts)
            )

            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            destination.write_bytes(
                source_bytes
            )

            exported.append(
                RetentionExportArtifact(
                    relative_path=(
                        entry.relative_path
                    ),
                    byte_count=(
                        entry.byte_count
                    ),
                    sha256=entry.sha256,
                )
            )

        draft = RetentionExportManifest(
            generated_at=generated_at,
            policy=retention.policy,
            source_root_label=(
                inventory.root_label
            ),
            source_inventory_sha256=(
                _sha256_file(inventory_file)
            ),
            source_retention_manifest_sha256=(
                _sha256_file(retention_file)
            ),
            artifact_count=len(exported),
            total_bytes=sum(
                artifact.byte_count
                for artifact in exported
            ),
            artifacts=exported,
            manifest_hash=_ZERO_HASH,
        )

        manifest = draft.model_copy(
            update={
                "manifest_hash": (
                    _manifest_hash(draft)
                ),
            }
        )

        _write_manifest(
            output / "export_manifest.json",
            manifest,
        )

    except Exception:
        shutil.rmtree(
            output,
            ignore_errors=True,
        )
        raise

    return manifest


def _collect_exported_files(
    artifacts_root: Path,
    issues: list[str],
) -> dict[str, Path]:
    """Collect exported artifact files safely."""

    actual: dict[str, Path] = {}

    if not artifacts_root.exists():
        issues.append(
            "artifacts_directory_missing"
        )
        return actual

    if artifacts_root.is_symlink():
        issues.append(
            "artifacts_directory_symlink"
        )
        return actual

    for path in sorted(
        artifacts_root.rglob("*")
    ):
        if path.is_symlink():
            issues.append(
                "artifact_symlink:"
                + path.relative_to(
                    artifacts_root
                ).as_posix()
            )
            continue

        if not path.is_file():
            continue

        relative = path.relative_to(
            artifacts_root
        ).as_posix()

        actual[relative] = path

    return actual


def verify_retention_export(
    *,
    export_dir: str | Path,
    inventory_path: str | Path,
    retention_manifest_path: str | Path,
) -> RetentionExportVerification:
    """Verify an export and its provenance references."""

    export = Path(export_dir)

    manifest = _load_export_manifest(
        export / "export_manifest.json"
    )

    issues: list[str] = []

    expected_manifest_hash = (
        _manifest_hash(manifest)
    )

    manifest_hash_valid = (
        manifest.manifest_hash
        == expected_manifest_hash
    )

    if not manifest_hash_valid:
        issues.append(
            "export_manifest_hash_mismatch"
        )

    expected: dict[
        str,
        RetentionExportArtifact,
    ] = {}

    for artifact in manifest.artifacts:
        try:
            _safe_relative_path(
                artifact.relative_path
            )
        except RetentionExportError:
            issues.append(
                "unsafe_manifest_artifact_path:"
                f"{artifact.relative_path}"
            )
            continue

        if artifact.relative_path in expected:
            issues.append(
                "duplicate_manifest_artifact_path:"
                f"{artifact.relative_path}"
            )
            continue

        expected[
            artifact.relative_path
        ] = artifact

    actual = _collect_exported_files(
        export / "artifacts",
        issues,
    )

    expected_paths = set(expected)
    actual_paths = set(actual)

    missing_paths = sorted(
        expected_paths - actual_paths
    )

    unexpected_paths = sorted(
        actual_paths - expected_paths
    )

    for path in missing_paths:
        issues.append(
            f"missing_export_artifact:{path}"
        )

    for path in unexpected_paths:
        issues.append(
            f"unexpected_export_artifact:{path}"
        )

    artifact_set_valid = (
        not missing_paths
        and not unexpected_paths
        and len(expected)
        == manifest.artifact_count
    )

    if (
        len(expected)
        != manifest.artifact_count
    ):
        issues.append(
            "export_artifact_count_mismatch"
        )

    artifact_hashes_valid = True
    verified_artifacts = 0
    verified_bytes = 0

    for path in sorted(
        expected_paths & actual_paths
    ):
        expected_artifact = expected[path]
        file_path = actual[path]

        content = file_path.read_bytes()
        actual_size = len(content)
        actual_sha256 = _sha256_bytes(
            content
        )

        if (
            actual_size
            != expected_artifact.byte_count
        ):
            artifact_hashes_valid = False
            issues.append(
                f"export_artifact_size_mismatch:{path}"
            )

        if (
            actual_sha256
            != expected_artifact.sha256
        ):
            artifact_hashes_valid = False
            issues.append(
                f"export_artifact_hash_mismatch:{path}"
            )

        if (
            actual_size
            == expected_artifact.byte_count
            and actual_sha256
            == expected_artifact.sha256
        ):
            verified_artifacts += 1

        verified_bytes += (
            expected_artifact.byte_count
        )

    expected_total_bytes = sum(
        artifact.byte_count
        for artifact in expected.values()
    )

    if expected_total_bytes != manifest.total_bytes:
        artifact_hashes_valid = False
        issues.append(
            "export_total_bytes_mismatch"
        )

    inventory_file = Path(inventory_path)
    retention_file = Path(
        retention_manifest_path
    )

    provenance_valid = True

    if not inventory_file.exists():
        provenance_valid = False
        issues.append(
            "source_inventory_missing"
        )
    elif (
        _sha256_file(inventory_file)
        != manifest.source_inventory_sha256
    ):
        provenance_valid = False
        issues.append(
            "source_inventory_hash_mismatch"
        )

    if not retention_file.exists():
        provenance_valid = False
        issues.append(
            "source_retention_manifest_missing"
        )
    elif (
        _sha256_file(retention_file)
        != manifest
        .source_retention_manifest_sha256
    ):
        provenance_valid = False
        issues.append(
            "source_retention_manifest_hash_mismatch"
        )

    if inventory_file.exists():
        inventory = _load_inventory(
            inventory_file
        )

        if (
            inventory.root_label
            != manifest.source_root_label
        ):
            provenance_valid = False
            issues.append(
                "source_root_label_mismatch"
            )

    if retention_file.exists():
        retention = _load_retention_manifest(
            retention_file
        )

        if retention.policy is not manifest.policy:
            provenance_valid = False
            issues.append(
                "retention_policy_mismatch"
            )

        if (
            retention.retained_artifact_count
            != manifest.artifact_count
        ):
            provenance_valid = False
            issues.append(
                "retention_artifact_count_mismatch"
            )

        if (
            retention.retained_bytes
            != manifest.total_bytes
        ):
            provenance_valid = False
            issues.append(
                "retention_byte_count_mismatch"
            )

        if (
            set(retention.retained_paths)
            != expected_paths
        ):
            provenance_valid = False
            issues.append(
                "retention_path_set_mismatch"
            )

    if verified_bytes != expected_total_bytes:
        artifact_hashes_valid = False
        issues.append(
            "verified_byte_count_mismatch"
        )

    status = (
        RetentionExportVerificationStatus.VALID
        if not issues
        else RetentionExportVerificationStatus.INVALID
    )

    return RetentionExportVerification(
        status=status,
        issues=issues,
        manifest_hash_valid=(
            manifest_hash_valid
        ),
        artifact_set_valid=(
            artifact_set_valid
        ),
        artifact_hashes_valid=(
            artifact_hashes_valid
        ),
        provenance_valid=provenance_valid,
        expected_artifact_count=(
            manifest.artifact_count
        ),
        verified_artifact_count=(
            verified_artifacts
        ),
    )


def main() -> None:
    """Run the retention export CLI."""

    parser = argparse.ArgumentParser(
        description=(
            "Materialize or verify a retention "
            "policy export."
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    create_parser = subparsers.add_parser(
        "create"
    )

    create_parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
    )

    create_parser.add_argument(
        "--inventory",
        type=Path,
        required=True,
    )

    create_parser.add_argument(
        "--retention-manifest",
        type=Path,
        required=True,
    )

    create_parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    create_parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    verify_parser = subparsers.add_parser(
        "verify"
    )

    verify_parser.add_argument(
        "--export",
        type=Path,
        required=True,
    )

    verify_parser.add_argument(
        "--inventory",
        type=Path,
        required=True,
    )

    verify_parser.add_argument(
        "--retention-manifest",
        type=Path,
        required=True,
    )

    arguments = parser.parse_args()

    if arguments.command == "create":
        manifest = materialize_retention_export(
            source_root=arguments.source_root,
            inventory_path=arguments.inventory,
            retention_manifest_path=(
                arguments.retention_manifest
            ),
            output_dir=arguments.output,
            overwrite=arguments.overwrite,
        )

        print(
            "policy="
            f"{manifest.policy.value}"
        )

        print(
            "artifact_count="
            f"{manifest.artifact_count}"
        )

        print(
            "total_bytes="
            f"{manifest.total_bytes}"
        )

        print(
            "manifest_hash="
            f"{manifest.manifest_hash}"
        )

        return

    verification = verify_retention_export(
        export_dir=arguments.export,
        inventory_path=arguments.inventory,
        retention_manifest_path=(
            arguments.retention_manifest
        ),
    )

    print(
        "status="
        f"{verification.status.value}"
    )

    print(
        "verified_artifact_count="
        f"{verification.verified_artifact_count}"
    )

    for issue in verification.issues:
        print(
            f"issue={issue}"
        )

    if not verification.valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
