from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field, ValidationError

from eg_runtime.approval_package import (
    ApprovalArtifactManifest,
    ApprovalArtifactPackageError,
    ApprovalPackageVerificationStatus,
    verify_approval_artifact_package,
)
from eg_runtime.approval_workflow import (
    ApprovalResolutionStatus,
)
from eg_runtime.models import FrozenModel


_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ZERO_HASH = "0" * 64


class ApprovalRegistryError(RuntimeError):
    """Raised when an approval registry operation cannot run."""


class ApprovalRegistryVerificationStatus(StrEnum):
    """Verification status for an authenticated registry."""

    VALID = "valid"
    INVALID = "invalid"


class ApprovalRegistryEntry(FrozenModel):
    """One authenticated registration of an approval package."""

    registry_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    sequence: int = Field(ge=0)
    entry_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    recorded_at: datetime
    approval_request_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    resolution_status: ApprovalResolutionStatus
    package_relative_path: str = Field(min_length=1)
    package_manifest_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    package_manifest_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    previous_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    record_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    signature: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )


class ApprovalRegistryCheckpoint(FrozenModel):
    """Authenticated checkpoint for a complete registry state."""

    checkpoint_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    entry_count: int = Field(ge=1)
    final_record_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    registry_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    created_at: datetime
    signature: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )


class ApprovalRegistryVerification(FrozenModel):
    """Verification result for a registry and its packages."""

    status: ApprovalRegistryVerificationStatus
    issues: list[str]
    entry_count: int = Field(ge=0)
    valid_entries: int = Field(ge=0)
    chain_valid: bool
    signatures_valid: bool
    checkpoint_valid: bool
    packages_valid: bool

    @property
    def valid(self) -> bool:
        """Return whether the complete registry is valid."""

        return (
            self.status
            is ApprovalRegistryVerificationStatus.VALID
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
    """Return a SHA-256 digest."""

    return hashlib.sha256(value).hexdigest()


def _hmac_sha256(
    key: bytes,
    value: bytes,
) -> str:
    """Return an HMAC-SHA-256 signature."""

    return hmac.new(
        key,
        value,
        hashlib.sha256,
    ).hexdigest()


def _require_secret_key(
    secret_key: bytes,
) -> None:
    """Require a non-empty registry authentication key."""

    if not isinstance(secret_key, bytes):
        raise ApprovalRegistryError(
            "secret_key must be bytes"
        )

    if not secret_key:
        raise ApprovalRegistryError(
            "secret_key must not be empty"
        )


def _require_aware_datetime(
    value: datetime,
) -> None:
    """Require a timezone-aware registry timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalRegistryError(
            "recorded_at must be timezone-aware"
        )


def _entry_id(
    *,
    approval_request_id: str,
    resolution_status: ApprovalResolutionStatus,
    package_relative_path: str,
    package_manifest_hash: str,
) -> str:
    """Create a deterministic registry-entry identifier."""

    content = {
        "approval_request_id": approval_request_id,
        "resolution_status": resolution_status.value,
        "package_relative_path": package_relative_path,
        "package_manifest_hash": package_manifest_hash,
    }

    return _sha256_bytes(
        _canonical_json(content).encode("utf-8")
    )


def _entry_hash(
    entry: ApprovalRegistryEntry,
) -> str:
    """Compute the hash of a registry entry."""

    content = entry.model_dump(
        mode="json",
        exclude={
            "record_hash",
            "signature",
        },
    )

    return _sha256_bytes(
        _canonical_json(content).encode("utf-8")
    )


def _entry_signature(
    entry: ApprovalRegistryEntry,
    secret_key: bytes,
) -> str:
    """Authenticate a registry-entry hash."""

    return _hmac_sha256(
        secret_key,
        entry.record_hash.encode("ascii"),
    )


def _checkpoint_signature(
    checkpoint: ApprovalRegistryCheckpoint,
    secret_key: bytes,
) -> str:
    """Authenticate a registry checkpoint."""

    content = checkpoint.model_dump(
        mode="json",
        exclude={"signature"},
    )

    return _hmac_sha256(
        secret_key,
        _canonical_json(content).encode("utf-8"),
    )


def _read_manifest(
    package_dir: Path,
) -> tuple[ApprovalArtifactManifest, bytes]:
    """Read one package manifest and its serialized bytes."""

    manifest_path = package_dir / "manifest.json"

    if not manifest_path.exists():
        raise ApprovalRegistryError(
            "package manifest does not exist: "
            f"{manifest_path}"
        )

    content = manifest_path.read_bytes()

    try:
        raw = json.loads(
            content.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ApprovalRegistryError(
            "package manifest contains invalid JSON"
        ) from exc

    try:
        manifest = ApprovalArtifactManifest.model_validate(
            raw
        )
    except ValidationError as exc:
        raise ApprovalRegistryError(
            "package manifest has an invalid schema"
        ) from exc

    return manifest, content


def _read_entries_strict(
    registry_path: Path,
) -> list[ApprovalRegistryEntry]:
    """Read a structurally valid registry."""

    if not registry_path.exists():
        return []

    lines = registry_path.read_text(
        encoding="utf-8"
    ).splitlines()

    if not lines:
        raise ApprovalRegistryError(
            "registry contains no entries"
        )

    entries: list[ApprovalRegistryEntry] = []

    for line_number, line in enumerate(
        lines,
        start=1,
    ):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApprovalRegistryError(
                f"registry line {line_number} "
                "contains invalid JSON"
            ) from exc

        try:
            entry = ApprovalRegistryEntry.model_validate(
                raw
            )
        except ValidationError as exc:
            raise ApprovalRegistryError(
                f"registry line {line_number} "
                "has an invalid schema"
            ) from exc

        entries.append(entry)

    return entries


def _read_checkpoint_strict(
    checkpoint_path: Path,
) -> ApprovalRegistryCheckpoint:
    """Read a structurally valid registry checkpoint."""

    if not checkpoint_path.exists():
        raise ApprovalRegistryError(
            "registry checkpoint does not exist"
        )

    try:
        raw = json.loads(
            checkpoint_path.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise ApprovalRegistryError(
            "registry checkpoint contains invalid JSON"
        ) from exc

    try:
        return ApprovalRegistryCheckpoint.model_validate(
            raw
        )
    except ValidationError as exc:
        raise ApprovalRegistryError(
            "registry checkpoint has an invalid schema"
        ) from exc


def _safe_package_path(
    package_root: Path,
    relative_path: str,
) -> Path | None:
    """Resolve a package path without leaving its root."""

    relative = PurePosixPath(relative_path)

    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
    ):
        return None

    candidate = package_root.joinpath(
        *relative.parts
    )

    try:
        candidate.resolve().relative_to(
            package_root.resolve()
        )
    except ValueError:
        return None

    return candidate


def _package_relative_path(
    package_root: Path,
    package_dir: Path,
) -> str:
    """Return a safe package path relative to its root."""

    try:
        relative = package_dir.resolve().relative_to(
            package_root.resolve()
        )
    except ValueError as exc:
        raise ApprovalRegistryError(
            "package directory is outside package_root"
        ) from exc

    if not relative.parts:
        raise ApprovalRegistryError(
            "package directory cannot equal package_root"
        )

    return relative.as_posix()


def _write_checkpoint(
    checkpoint_path: Path,
    checkpoint: ApprovalRegistryCheckpoint,
) -> None:
    """Write a registry checkpoint atomically."""

    checkpoint_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = checkpoint_path.with_suffix(
        checkpoint_path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            checkpoint.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(checkpoint_path)


def _add_issue(
    issues: list[str],
    issue: str,
) -> None:
    """Append a verification issue once."""

    if issue not in issues:
        issues.append(issue)


def register_approval_package(
    *,
    registry_path: str | Path,
    checkpoint_path: str | Path,
    package_root: str | Path,
    package_dir: str | Path,
    secret_key: bytes,
    recorded_at: datetime,
) -> ApprovalRegistryEntry:
    """Append one verified package to an authenticated registry."""

    _require_secret_key(secret_key)
    _require_aware_datetime(recorded_at)

    registry = Path(registry_path)
    checkpoint = Path(checkpoint_path)
    root = Path(package_root)
    package = Path(package_dir)

    existing_registry = registry.exists()
    existing_checkpoint = checkpoint.exists()

    if existing_registry != existing_checkpoint:
        raise ApprovalRegistryError(
            "registry and checkpoint must exist together"
        )

    if existing_registry:
        current = verify_approval_registry(
            registry_path=registry,
            checkpoint_path=checkpoint,
            package_root=root,
            secret_key=secret_key,
            verify_packages=False,
        )

        if not current.valid:
            raise ApprovalRegistryError(
                "existing registry is invalid: "
                + ", ".join(current.issues)
            )

    try:
        package_verification = (
            verify_approval_artifact_package(
                package
            )
        )
    except ApprovalArtifactPackageError as exc:
        raise ApprovalRegistryError(
            "package verification could not run"
        ) from exc

    if (
        package_verification.status
        is not ApprovalPackageVerificationStatus.VALID
    ):
        raise ApprovalRegistryError(
            "only valid approval packages "
            "can be registered"
        )

    manifest, manifest_bytes = _read_manifest(
        package
    )

    relative_path = _package_relative_path(
        root,
        package,
    )

    entries = _read_entries_strict(registry)

    sequence = len(entries)

    previous_hash = (
        entries[-1].record_hash
        if entries
        else _ZERO_HASH
    )

    draft = ApprovalRegistryEntry(
        sequence=sequence,
        entry_id=_entry_id(
            approval_request_id=(
                manifest.approval_request_id
            ),
            resolution_status=(
                manifest.resolution_status
            ),
            package_relative_path=relative_path,
            package_manifest_hash=(
                manifest.manifest_hash
            ),
        ),
        recorded_at=recorded_at,
        approval_request_id=(
            manifest.approval_request_id
        ),
        resolution_status=(
            manifest.resolution_status
        ),
        package_relative_path=relative_path,
        package_manifest_hash=(
            manifest.manifest_hash
        ),
        package_manifest_sha256=(
            _sha256_bytes(manifest_bytes)
        ),
        previous_hash=previous_hash,
        record_hash=_ZERO_HASH,
        signature=_ZERO_HASH,
    )

    hashed = draft.model_copy(
        update={
            "record_hash": _entry_hash(draft),
        }
    )

    entry = hashed.model_copy(
        update={
            "signature": _entry_signature(
                hashed,
                secret_key,
            ),
        }
    )

    if any(
        existing.entry_id == entry.entry_id
        for existing in entries
    ):
        raise ApprovalRegistryError(
            "package is already registered"
        )

    registry.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with registry.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            _canonical_json(
                entry.model_dump(mode="json")
            )
            + "\n"
        )

    checkpoint_draft = ApprovalRegistryCheckpoint(
        entry_count=sequence + 1,
        final_record_hash=entry.record_hash,
        registry_sha256=_sha256_bytes(
            registry.read_bytes()
        ),
        created_at=recorded_at,
        signature=_ZERO_HASH,
    )

    signed_checkpoint = checkpoint_draft.model_copy(
        update={
            "signature": _checkpoint_signature(
                checkpoint_draft,
                secret_key,
            ),
        }
    )

    _write_checkpoint(
        checkpoint,
        signed_checkpoint,
    )

    return entry


def verify_approval_registry(
    *,
    registry_path: str | Path,
    checkpoint_path: str | Path,
    package_root: str | Path,
    secret_key: bytes,
    verify_packages: bool = True,
) -> ApprovalRegistryVerification:
    """Verify registry integrity, authentication, and packages."""

    _require_secret_key(secret_key)

    registry = Path(registry_path)
    checkpoint_path_value = Path(
        checkpoint_path
    )

    root = Path(package_root)

    issues: list[str] = []

    if not registry.exists():
        raise ApprovalRegistryError(
            "registry does not exist"
        )

    raw_lines = registry.read_text(
        encoding="utf-8"
    ).splitlines()

    entries: list[ApprovalRegistryEntry] = []

    for line_number, line in enumerate(
        raw_lines,
        start=1,
    ):
        try:
            raw = json.loads(line)
            entry = ApprovalRegistryEntry.model_validate(
                raw
            )
        except (
            json.JSONDecodeError,
            ValidationError,
        ):
            _add_issue(
                issues,
                f"registry_line_invalid:{line_number}",
            )

            continue

        entries.append(entry)

    if not entries:
        _add_issue(
            issues,
            "registry_has_no_valid_entries",
        )

    chain_valid = True
    signatures_valid = True
    valid_entries = 0

    previous_hash = _ZERO_HASH
    entry_ids: set[str] = set()

    for expected_sequence, entry in enumerate(
        entries
    ):
        entry_valid = True

        if entry.sequence != expected_sequence:
            _add_issue(
                issues,
                "entry_sequence_mismatch:"
                f"{expected_sequence}",
            )

            entry_valid = False
            chain_valid = False

        if entry.previous_hash != previous_hash:
            _add_issue(
                issues,
                "entry_previous_hash_mismatch:"
                f"{expected_sequence}",
            )

            entry_valid = False
            chain_valid = False

        expected_hash = _entry_hash(entry)

        if entry.record_hash != expected_hash:
            _add_issue(
                issues,
                "entry_record_hash_mismatch:"
                f"{expected_sequence}",
            )

            entry_valid = False
            chain_valid = False

        expected_signature = _entry_signature(
            entry,
            secret_key,
        )

        if not hmac.compare_digest(
            entry.signature,
            expected_signature,
        ):
            _add_issue(
                issues,
                "entry_signature_mismatch:"
                f"{expected_sequence}",
            )

            entry_valid = False
            signatures_valid = False

        if entry.entry_id in entry_ids:
            _add_issue(
                issues,
                "duplicate_entry_id:"
                f"{entry.entry_id}",
            )

            entry_valid = False
            chain_valid = False

        entry_ids.add(entry.entry_id)

        if entry_valid:
            valid_entries += 1

        previous_hash = entry.record_hash

    checkpoint_valid = True

    try:
        checkpoint = _read_checkpoint_strict(
            checkpoint_path_value
        )
    except ApprovalRegistryError:
        checkpoint = None

        _add_issue(
            issues,
            "checkpoint_invalid",
        )

        checkpoint_valid = False
        signatures_valid = False

    if checkpoint is not None:
        expected_checkpoint_signature = (
            _checkpoint_signature(
                checkpoint,
                secret_key,
            )
        )

        if not hmac.compare_digest(
            checkpoint.signature,
            expected_checkpoint_signature,
        ):
            _add_issue(
                issues,
                "checkpoint_signature_mismatch",
            )

            checkpoint_valid = False
            signatures_valid = False

        if checkpoint.entry_count != len(entries):
            _add_issue(
                issues,
                "checkpoint_entry_count_mismatch",
            )

            checkpoint_valid = False

        expected_final_hash = (
            entries[-1].record_hash
            if entries
            else _ZERO_HASH
        )

        if (
            checkpoint.final_record_hash
            != expected_final_hash
        ):
            _add_issue(
                issues,
                "checkpoint_final_hash_mismatch",
            )

            checkpoint_valid = False

        actual_registry_sha256 = _sha256_bytes(
            registry.read_bytes()
        )

        if (
            checkpoint.registry_sha256
            != actual_registry_sha256
        ):
            _add_issue(
                issues,
                "checkpoint_registry_sha256_mismatch",
            )

            checkpoint_valid = False

    packages_valid = True

    if verify_packages:
        for entry in entries:
            package = _safe_package_path(
                root,
                entry.package_relative_path,
            )

            if package is None:
                _add_issue(
                    issues,
                    "unsafe_package_path:"
                    f"{entry.package_relative_path}",
                )

                packages_valid = False
                continue

            if not package.exists():
                _add_issue(
                    issues,
                    "package_missing:"
                    f"{entry.package_relative_path}",
                )

                packages_valid = False
                continue

            try:
                manifest, manifest_bytes = (
                    _read_manifest(package)
                )
            except ApprovalRegistryError:
                _add_issue(
                    issues,
                    "package_manifest_invalid:"
                    f"{entry.package_relative_path}",
                )

                packages_valid = False
                continue

            if (
                manifest.manifest_hash
                != entry.package_manifest_hash
            ):
                _add_issue(
                    issues,
                    "package_manifest_hash_mismatch:"
                    f"{entry.package_relative_path}",
                )

                packages_valid = False

            if (
                _sha256_bytes(manifest_bytes)
                != entry.package_manifest_sha256
            ):
                _add_issue(
                    issues,
                    "package_manifest_sha256_mismatch:"
                    f"{entry.package_relative_path}",
                )

                packages_valid = False

            if (
                manifest.approval_request_id
                != entry.approval_request_id
                or manifest.resolution_status
                is not entry.resolution_status
            ):
                _add_issue(
                    issues,
                    "package_identity_mismatch:"
                    f"{entry.package_relative_path}",
                )

                packages_valid = False

            try:
                package_verification = (
                    verify_approval_artifact_package(
                        package
                    )
                )
            except ApprovalArtifactPackageError:
                _add_issue(
                    issues,
                    "package_invalid:"
                    f"{entry.package_relative_path}",
                )

                packages_valid = False
            else:
                if (
                    package_verification.status
                    is not
                    ApprovalPackageVerificationStatus.VALID
                ):
                    _add_issue(
                        issues,
                        "package_invalid:"
                        f"{entry.package_relative_path}",
                    )

                    packages_valid = False

    status = (
        ApprovalRegistryVerificationStatus.VALID
        if not issues
        else ApprovalRegistryVerificationStatus.INVALID
    )

    return ApprovalRegistryVerification(
        status=status,
        issues=issues,
        entry_count=len(entries),
        valid_entries=valid_entries,
        chain_valid=chain_valid,
        signatures_valid=signatures_valid,
        checkpoint_valid=checkpoint_valid,
        packages_valid=packages_valid,
    )
