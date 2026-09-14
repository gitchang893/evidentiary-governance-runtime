from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field, ValidationError

from eg_runtime.approval_audit import (
    ApprovalAuditError,
    ApprovalAuditVerificationStatus,
    create_approval_audit_record,
    verify_approval_audit_record,
)
from eg_runtime.approval_workflow import (
    ApprovalResolution,
    ApprovalResolutionStatus,
    PendingApproval,
)
from eg_runtime.models import FrozenModel


_HASH_PATTERN = r"^[0-9a-f]{64}$"


class ApprovalArtifactPackageError(RuntimeError):
    """Raised when an approval artifact package cannot be used."""


class ApprovalArtifactKind(StrEnum):
    """Artifact types contained in an approval package."""

    ORIGINAL_EVIDENCE = "original_evidence"
    PENDING_REQUEST = "pending_request"
    RESOLUTION = "resolution"
    APPROVAL_AUDIT = "approval_audit"
    RESUMED_EVIDENCE = "resumed_evidence"


class ApprovalArtifactEntry(FrozenModel):
    """Identity and location of one packaged artifact."""

    kind: ApprovalArtifactKind
    relative_path: str = Field(min_length=1)
    sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    byte_count: int = Field(ge=1)


class ApprovalArtifactManifest(FrozenModel):
    """Manifest for one self-contained approval package."""

    package_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    approval_request_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    resolution_status: ApprovalResolutionStatus
    created_at: datetime
    artifacts: list[ApprovalArtifactEntry] = Field(
        min_length=4
    )
    manifest_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )


class ApprovalPackageVerificationStatus(StrEnum):
    """Verification status for an approval package."""

    VALID = "valid"
    INVALID = "invalid"


class ApprovalPackageVerification(FrozenModel):
    """Verification result for an approval package."""

    status: ApprovalPackageVerificationStatus
    issues: list[str]
    manifest_hash_valid: bool
    artifact_matches: dict[str, bool]

    @property
    def valid(self) -> bool:
        """Return whether the complete package is valid."""

        return (
            self.status
            is ApprovalPackageVerificationStatus.VALID
        )


def _canonical_json(
    value: Any,
) -> str:
    """Serialize a JSON-compatible value deterministically."""

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


def _require_aware_datetime(
    value: datetime,
) -> None:
    """Require a timezone-aware package timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalArtifactPackageError(
            "created_at must be timezone-aware"
        )


def _write_json(
    path: Path,
    value: Any,
) -> None:
    """Write deterministic, human-readable JSON."""

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


def _artifact_entry(
    root: Path,
    path: Path,
    kind: ApprovalArtifactKind,
) -> ApprovalArtifactEntry:
    """Build a manifest entry for one package file."""

    content = path.read_bytes()

    return ApprovalArtifactEntry(
        kind=kind,
        relative_path=(
            path.relative_to(root).as_posix()
        ),
        sha256=_sha256_bytes(content),
        byte_count=len(content),
    )


def _manifest_hash(
    manifest: ApprovalArtifactManifest,
) -> str:
    """Compute the manifest hash."""

    content = manifest.model_dump(
        mode="json",
        exclude={"manifest_hash"},
    )

    return _sha256_bytes(
        _canonical_json(content).encode("utf-8")
    )


def _safe_package_path(
    root: Path,
    relative_path: str,
) -> Path | None:
    """Resolve a manifest path without leaving the package."""

    relative = PurePosixPath(relative_path)

    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
    ):
        return None

    candidate = root.joinpath(
        *relative.parts
    )

    try:
        candidate.resolve().relative_to(
            root.resolve()
        )
    except ValueError:
        return None

    return candidate


def _add_issue(
    issues: list[str],
    issue: str,
) -> None:
    """Append an issue once."""

    if issue not in issues:
        issues.append(issue)


def create_approval_artifact_package(
    *,
    output_dir: str | Path,
    original_evidence_path: str | Path,
    pending: PendingApproval,
    resolution: ApprovalResolution,
    created_at: datetime,
    resumed_evidence_path: str | Path | None = None,
    overwrite: bool = False,
) -> ApprovalArtifactManifest:
    """Create a self-contained approval evidence package."""

    _require_aware_datetime(created_at)

    root = Path(output_dir)
    original_source = Path(
        original_evidence_path
    )

    resumed_source = (
        Path(resumed_evidence_path)
        if resumed_evidence_path is not None
        else None
    )

    try:
        original_content = original_source.read_bytes()
    except OSError as exc:
        raise ApprovalArtifactPackageError(
            "could not read original evidence"
        ) from exc

    resumed_content: bytes | None = None

    if resumed_source is not None:
        try:
            resumed_content = (
                resumed_source.read_bytes()
            )
        except OSError as exc:
            raise ApprovalArtifactPackageError(
                "could not read resumed evidence"
            ) from exc

    if root.exists():
        if not overwrite:
            raise ApprovalArtifactPackageError(
                f"output directory already exists: {root}"
            )

        if root.is_dir():
            shutil.rmtree(root)
        else:
            root.unlink()

    original_path = (
        root / "evidence" / "original.jsonl"
    )

    resumed_path = (
        root / "evidence" / "resumed.jsonl"
        if resumed_content is not None
        else None
    )

    pending_path = (
        root / "workflow" / "pending.json"
    )

    resolution_path = (
        root / "workflow" / "resolution.json"
    )

    audit_path = (
        root / "audit" / "approval_audit.json"
    )

    original_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    original_path.write_bytes(
        original_content
    )

    if (
        resumed_path is not None
        and resumed_content is not None
    ):
        resumed_path.write_bytes(
            resumed_content
        )

    _write_json(
        pending_path,
        pending.model_dump(mode="json"),
    )

    _write_json(
        resolution_path,
        resolution.model_dump(mode="json"),
    )

    try:
        create_approval_audit_record(
            output_path=audit_path,
            original_evidence_path=original_path,
            pending=pending,
            resolution=resolution,
            resumed_evidence_path=resumed_path,
        )
    except ApprovalAuditError as exc:
        shutil.rmtree(
            root,
            ignore_errors=True,
        )

        raise ApprovalArtifactPackageError(
            "could not create approval audit record"
        ) from exc

    artifacts = [
        _artifact_entry(
            root,
            original_path,
            ApprovalArtifactKind.ORIGINAL_EVIDENCE,
        ),
        _artifact_entry(
            root,
            pending_path,
            ApprovalArtifactKind.PENDING_REQUEST,
        ),
        _artifact_entry(
            root,
            resolution_path,
            ApprovalArtifactKind.RESOLUTION,
        ),
        _artifact_entry(
            root,
            audit_path,
            ApprovalArtifactKind.APPROVAL_AUDIT,
        ),
    ]

    if resumed_path is not None:
        artifacts.append(
            _artifact_entry(
                root,
                resumed_path,
                ApprovalArtifactKind.RESUMED_EVIDENCE,
            )
        )

    draft = ApprovalArtifactManifest(
        approval_request_id=(
            pending.approval_request_id
        ),
        resolution_status=resolution.status,
        created_at=created_at,
        artifacts=artifacts,
        manifest_hash="0" * 64,
    )

    manifest = draft.model_copy(
        update={
            "manifest_hash": _manifest_hash(
                draft
            ),
        }
    )

    _write_json(
        root / "manifest.json",
        manifest.model_dump(mode="json"),
    )

    return manifest


def verify_approval_artifact_package(
    package_dir: str | Path,
) -> ApprovalPackageVerification:
    """Verify a complete approval evidence package."""

    root = Path(package_dir)
    manifest_path = root / "manifest.json"

    if not manifest_path.exists():
        raise ApprovalArtifactPackageError(
            "package manifest does not exist"
        )

    try:
        raw = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise ApprovalArtifactPackageError(
            "package manifest contains invalid JSON"
        ) from exc

    try:
        manifest = (
            ApprovalArtifactManifest.model_validate(
                raw
            )
        )
    except ValidationError as exc:
        raise ApprovalArtifactPackageError(
            "package manifest has an invalid schema"
        ) from exc

    issues: list[str] = []

    manifest_hash_valid = (
        _manifest_hash(manifest)
        == manifest.manifest_hash
    )

    if not manifest_hash_valid:
        _add_issue(
            issues,
            "manifest_hash_mismatch",
        )

    artifact_matches: dict[str, bool] = {
        kind.value: False
        for kind in ApprovalArtifactKind
    }

    kind_counts: dict[
        ApprovalArtifactKind,
        int,
    ] = {}

    path_counts: dict[str, int] = {}

    resolved_paths: dict[
        ApprovalArtifactKind,
        Path,
    ] = {}

    for entry in manifest.artifacts:
        kind_counts[entry.kind] = (
            kind_counts.get(entry.kind, 0) + 1
        )

        path_counts[entry.relative_path] = (
            path_counts.get(
                entry.relative_path,
                0,
            )
            + 1
        )

        path = _safe_package_path(
            root,
            entry.relative_path,
        )

        if path is None:
            _add_issue(
                issues,
                "unsafe_artifact_path:"
                f"{entry.relative_path}",
            )

            continue

        resolved_paths[entry.kind] = path

        if not path.exists() or not path.is_file():
            _add_issue(
                issues,
                "artifact_missing:"
                f"{entry.relative_path}",
            )

            continue

        content = path.read_bytes()

        size_matches = (
            len(content) == entry.byte_count
        )

        hash_matches = (
            _sha256_bytes(content)
            == entry.sha256
        )

        if not size_matches:
            _add_issue(
                issues,
                "artifact_size_mismatch:"
                f"{entry.relative_path}",
            )

        if not hash_matches:
            _add_issue(
                issues,
                "artifact_hash_mismatch:"
                f"{entry.relative_path}",
            )

        artifact_matches[
            entry.kind.value
        ] = size_matches and hash_matches

    for kind, count in kind_counts.items():
        if count > 1:
            _add_issue(
                issues,
                "duplicate_artifact_kind:"
                f"{kind.value}",
            )

    for relative_path, count in path_counts.items():
        if count > 1:
            _add_issue(
                issues,
                "duplicate_artifact_path:"
                f"{relative_path}",
            )

    required_kinds = {
        ApprovalArtifactKind.ORIGINAL_EVIDENCE,
        ApprovalArtifactKind.PENDING_REQUEST,
        ApprovalArtifactKind.RESOLUTION,
        ApprovalArtifactKind.APPROVAL_AUDIT,
    }

    if (
        manifest.resolution_status
        is ApprovalResolutionStatus.APPROVED
    ):
        required_kinds.add(
            ApprovalArtifactKind.RESUMED_EVIDENCE
        )

    actual_kinds = set(kind_counts)

    for missing in sorted(
        required_kinds - actual_kinds,
        key=lambda value: value.value,
    ):
        _add_issue(
            issues,
            "missing_artifact_kind:"
            f"{missing.value}",
        )

    for unexpected in sorted(
        actual_kinds - required_kinds,
        key=lambda value: value.value,
    ):
        _add_issue(
            issues,
            "unexpected_artifact_kind:"
            f"{unexpected.value}",
        )

    pending_path = resolved_paths.get(
        ApprovalArtifactKind.PENDING_REQUEST
    )

    if (
        pending_path is not None
        and artifact_matches[
            ApprovalArtifactKind
            .PENDING_REQUEST
            .value
        ]
    ):
        try:
            pending = PendingApproval.model_validate(
                json.loads(
                    pending_path.read_text(
                        encoding="utf-8"
                    )
                )
            )
        except (
            json.JSONDecodeError,
            ValidationError,
        ):
            _add_issue(
                issues,
                "pending_record_invalid",
            )
        else:
            if (
                pending.approval_request_id
                != manifest.approval_request_id
            ):
                _add_issue(
                    issues,
                    "pending_request_id_mismatch",
                )

    resolution_path = resolved_paths.get(
        ApprovalArtifactKind.RESOLUTION
    )

    if (
        resolution_path is not None
        and artifact_matches[
            ApprovalArtifactKind.RESOLUTION.value
        ]
    ):
        try:
            resolution = (
                ApprovalResolution.model_validate(
                    json.loads(
                        resolution_path.read_text(
                            encoding="utf-8"
                        )
                    )
                )
            )
        except (
            json.JSONDecodeError,
            ValidationError,
        ):
            _add_issue(
                issues,
                "resolution_record_invalid",
            )
        else:
            if (
                resolution.approval_request_id
                != manifest.approval_request_id
            ):
                _add_issue(
                    issues,
                    "resolution_request_id_mismatch",
                )

            if (
                resolution.status
                is not manifest.resolution_status
            ):
                _add_issue(
                    issues,
                    "resolution_status_mismatch",
                )

    original_path = resolved_paths.get(
        ApprovalArtifactKind.ORIGINAL_EVIDENCE
    )

    audit_path = resolved_paths.get(
        ApprovalArtifactKind.APPROVAL_AUDIT
    )

    resumed_path = resolved_paths.get(
        ApprovalArtifactKind.RESUMED_EVIDENCE
    )

    audit_inputs_valid = (
        original_path is not None
        and audit_path is not None
        and artifact_matches[
            ApprovalArtifactKind
            .ORIGINAL_EVIDENCE
            .value
        ]
        and artifact_matches[
            ApprovalArtifactKind
            .APPROVAL_AUDIT
            .value
        ]
    )

    if (
        manifest.resolution_status
        is ApprovalResolutionStatus.APPROVED
    ):
        audit_inputs_valid = (
            audit_inputs_valid
            and resumed_path is not None
            and artifact_matches[
                ApprovalArtifactKind
                .RESUMED_EVIDENCE
                .value
            ]
        )

    if (
        audit_inputs_valid
        and original_path is not None
        and audit_path is not None
    ):
        try:
            audit_verification = (
                verify_approval_audit_record(
                    audit_path,
                    original_evidence_path=(
                        original_path
                    ),
                    resumed_evidence_path=(
                        resumed_path
                        if (
                            manifest.resolution_status
                            is ApprovalResolutionStatus
                            .APPROVED
                        )
                        else None
                    ),
                )
            )
        except ApprovalAuditError:
            _add_issue(
                issues,
                "approval_audit_invalid",
            )
        else:
            if (
                audit_verification.status
                is not
                ApprovalAuditVerificationStatus.VALID
            ):
                _add_issue(
                    issues,
                    "approval_audit_invalid",
                )

                for issue in (
                    audit_verification.issues
                ):
                    _add_issue(
                        issues,
                        f"approval_audit:{issue}",
                    )

    status = (
        ApprovalPackageVerificationStatus.VALID
        if not issues
        else ApprovalPackageVerificationStatus.INVALID
    )

    return ApprovalPackageVerification(
        status=status,
        issues=issues,
        manifest_hash_valid=manifest_hash_valid,
        artifact_matches=artifact_matches,
    )
