from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from eg_runtime.approval_registry import (
    ApprovalRegistryCheckpoint,
    ApprovalRegistryError,
    ApprovalRegistryVerificationStatus,
    verify_approval_registry,
)
from eg_runtime.models import FrozenModel


_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ZERO_HASH = "0" * 64


class ApprovalWitnessError(RuntimeError):
    """Raised when a witness receipt cannot be processed."""


class ApprovalWitnessVerificationStatus(StrEnum):
    """Verification status for a witness receipt."""

    VALID = "valid"
    INVALID = "invalid"


class ApprovalWitnessReceipt(FrozenModel):
    """Independent authentication of one registry snapshot."""

    witness_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    receipt_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    witness_id: str = Field(min_length=1)
    observed_at: datetime

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
    checkpoint_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    checkpoint_signature: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )

    record_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    witness_signature: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )


class ApprovalWitnessVerification(FrozenModel):
    """Verification result for one witnessed snapshot."""

    status: ApprovalWitnessVerificationStatus
    issues: list[str]
    receipt_id_valid: bool
    record_hash_valid: bool
    witness_signature_valid: bool
    registry_valid: bool
    registry_snapshot_match: bool
    checkpoint_snapshot_match: bool

    @property
    def valid(self) -> bool:
        """Return whether the witnessed snapshot is valid."""

        return (
            self.status
            is ApprovalWitnessVerificationStatus.VALID
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


def _require_key(
    key: bytes,
    field_name: str,
) -> None:
    """Require a non-empty byte key."""

    if not isinstance(key, bytes):
        raise ApprovalWitnessError(
            f"{field_name} must be bytes"
        )

    if not key:
        raise ApprovalWitnessError(
            f"{field_name} must not be empty"
        )


def _require_distinct_keys(
    registry_key: bytes,
    witness_key: bytes,
) -> None:
    """Require separate registry and witness keys."""

    _require_key(
        registry_key,
        "registry_key",
    )

    _require_key(
        witness_key,
        "witness_key",
    )

    if hmac.compare_digest(
        registry_key,
        witness_key,
    ):
        raise ApprovalWitnessError(
            "registry_key and witness_key "
            "must be distinct"
        )


def _require_aware_datetime(
    value: datetime,
) -> None:
    """Require a timezone-aware witness timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalWitnessError(
            "observed_at must be timezone-aware"
        )


def _read_checkpoint(
    path: Path,
) -> tuple[
    ApprovalRegistryCheckpoint,
    bytes,
]:
    """Read one registry checkpoint and its bytes."""

    if not path.exists():
        raise ApprovalWitnessError(
            "registry checkpoint does not exist"
        )

    content = path.read_bytes()

    try:
        raw = json.loads(
            content.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ApprovalWitnessError(
            "registry checkpoint contains invalid JSON"
        ) from exc

    try:
        checkpoint = (
            ApprovalRegistryCheckpoint.model_validate(
                raw
            )
        )
    except ValidationError as exc:
        raise ApprovalWitnessError(
            "registry checkpoint has an invalid schema"
        ) from exc

    return checkpoint, content


def _receipt_id(
    *,
    witness_id: str,
    observed_at: datetime,
    registry_sha256: str,
    checkpoint_sha256: str,
) -> str:
    """Create a deterministic witness-receipt identifier."""

    content = {
        "witness_id": witness_id,
        "observed_at": observed_at.isoformat(),
        "registry_sha256": registry_sha256,
        "checkpoint_sha256": checkpoint_sha256,
    }

    return _sha256_bytes(
        _canonical_json(content).encode("utf-8")
    )


def _record_hash(
    receipt: ApprovalWitnessReceipt,
) -> str:
    """Compute a witness receipt record hash."""

    content = receipt.model_dump(
        mode="json",
        exclude={
            "record_hash",
            "witness_signature",
        },
    )

    return _sha256_bytes(
        _canonical_json(content).encode("utf-8")
    )


def _witness_signature(
    receipt: ApprovalWitnessReceipt,
    witness_key: bytes,
) -> str:
    """Authenticate a witness receipt record hash."""

    return _hmac_sha256(
        witness_key,
        receipt.record_hash.encode("ascii"),
    )


def _add_issue(
    issues: list[str],
    issue: str,
) -> None:
    """Append an issue once."""

    if issue not in issues:
        issues.append(issue)


def create_approval_witness_receipt(
    *,
    output_path: str | Path,
    registry_path: str | Path,
    checkpoint_path: str | Path,
    package_root: str | Path,
    registry_key: bytes,
    witness_key: bytes,
    witness_id: str,
    observed_at: datetime,
    overwrite: bool = False,
) -> ApprovalWitnessReceipt:
    """Authenticate one valid registry snapshot."""

    _require_distinct_keys(
        registry_key,
        witness_key,
    )

    _require_aware_datetime(observed_at)

    normalized_witness_id = witness_id.strip()

    if not normalized_witness_id:
        raise ApprovalWitnessError(
            "witness_id must not be empty"
        )

    output = Path(output_path)
    registry = Path(registry_path)
    checkpoint_path_value = Path(
        checkpoint_path
    )

    if output.exists() and not overwrite:
        raise ApprovalWitnessError(
            f"output file already exists: {output}"
        )

    try:
        registry_verification = (
            verify_approval_registry(
                registry_path=registry,
                checkpoint_path=(
                    checkpoint_path_value
                ),
                package_root=package_root,
                secret_key=registry_key,
            )
        )
    except ApprovalRegistryError as exc:
        raise ApprovalWitnessError(
            "registry verification could not run"
        ) from exc

    if (
        registry_verification.status
        is not ApprovalRegistryVerificationStatus.VALID
    ):
        raise ApprovalWitnessError(
            "registry is invalid: "
            + ", ".join(
                registry_verification.issues
            )
        )

    checkpoint, checkpoint_bytes = (
        _read_checkpoint(
            checkpoint_path_value
        )
    )

    registry_bytes = registry.read_bytes()

    registry_sha256 = _sha256_bytes(
        registry_bytes
    )

    checkpoint_sha256 = _sha256_bytes(
        checkpoint_bytes
    )

    draft = ApprovalWitnessReceipt(
        receipt_id=_receipt_id(
            witness_id=normalized_witness_id,
            observed_at=observed_at,
            registry_sha256=registry_sha256,
            checkpoint_sha256=checkpoint_sha256,
        ),
        witness_id=normalized_witness_id,
        observed_at=observed_at,
        entry_count=checkpoint.entry_count,
        final_record_hash=(
            checkpoint.final_record_hash
        ),
        registry_sha256=registry_sha256,
        checkpoint_sha256=checkpoint_sha256,
        checkpoint_signature=(
            checkpoint.signature
        ),
        record_hash=_ZERO_HASH,
        witness_signature=_ZERO_HASH,
    )

    hashed = draft.model_copy(
        update={
            "record_hash": _record_hash(draft),
        }
    )

    receipt = hashed.model_copy(
        update={
            "witness_signature": (
                _witness_signature(
                    hashed,
                    witness_key,
                )
            ),
        }
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = output.with_suffix(
        output.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(output)

    return receipt


def verify_approval_witness_receipt(
    *,
    receipt_path: str | Path,
    registry_path: str | Path,
    checkpoint_path: str | Path,
    package_root: str | Path,
    registry_key: bytes,
    witness_key: bytes,
) -> ApprovalWitnessVerification:
    """Verify a receipt and its current registry snapshot."""

    _require_distinct_keys(
        registry_key,
        witness_key,
    )

    receipt_file = Path(receipt_path)
    registry = Path(registry_path)
    checkpoint_file = Path(
        checkpoint_path
    )

    if not receipt_file.exists():
        raise ApprovalWitnessError(
            "witness receipt does not exist"
        )

    try:
        raw = json.loads(
            receipt_file.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise ApprovalWitnessError(
            "witness receipt contains invalid JSON"
        ) from exc

    try:
        receipt = (
            ApprovalWitnessReceipt.model_validate(
                raw
            )
        )
    except ValidationError as exc:
        raise ApprovalWitnessError(
            "witness receipt has an invalid schema"
        ) from exc

    issues: list[str] = []

    expected_receipt_id = _receipt_id(
        witness_id=receipt.witness_id,
        observed_at=receipt.observed_at,
        registry_sha256=receipt.registry_sha256,
        checkpoint_sha256=(
            receipt.checkpoint_sha256
        ),
    )

    receipt_id_valid = (
        receipt.receipt_id
        == expected_receipt_id
    )

    if not receipt_id_valid:
        _add_issue(
            issues,
            "receipt_id_mismatch",
        )

    record_hash_valid = (
        receipt.record_hash
        == _record_hash(receipt)
    )

    if not record_hash_valid:
        _add_issue(
            issues,
            "receipt_record_hash_mismatch",
        )

    expected_witness_signature = (
        _witness_signature(
            receipt,
            witness_key,
        )
    )

    witness_signature_valid = (
        hmac.compare_digest(
            receipt.witness_signature,
            expected_witness_signature,
        )
    )

    if not witness_signature_valid:
        _add_issue(
            issues,
            "witness_signature_mismatch",
        )

    registry_valid = False

    try:
        registry_verification = (
            verify_approval_registry(
                registry_path=registry,
                checkpoint_path=checkpoint_file,
                package_root=package_root,
                secret_key=registry_key,
            )
        )
    except ApprovalRegistryError:
        _add_issue(
            issues,
            "registry_verification_error",
        )
    else:
        registry_valid = (
            registry_verification.status
            is ApprovalRegistryVerificationStatus.VALID
        )

        if not registry_valid:
            _add_issue(
                issues,
                "registry_invalid",
            )

            for registry_issue in (
                registry_verification.issues
            ):
                _add_issue(
                    issues,
                    f"registry:{registry_issue}",
                )

    registry_snapshot_match = False

    if registry.exists():
        registry_snapshot_match = (
            _sha256_bytes(
                registry.read_bytes()
            )
            == receipt.registry_sha256
        )

    if not registry_snapshot_match:
        _add_issue(
            issues,
            "registry_sha256_mismatch",
        )

    checkpoint_snapshot_match = False

    try:
        checkpoint, checkpoint_bytes = (
            _read_checkpoint(
                checkpoint_file
            )
        )
    except ApprovalWitnessError:
        _add_issue(
            issues,
            "checkpoint_unreadable",
        )
    else:
        checkpoint_snapshot_match = (
            _sha256_bytes(checkpoint_bytes)
            == receipt.checkpoint_sha256
            and checkpoint.entry_count
            == receipt.entry_count
            and checkpoint.final_record_hash
            == receipt.final_record_hash
            and checkpoint.signature
            == receipt.checkpoint_signature
        )

        if (
            checkpoint.entry_count
            != receipt.entry_count
        ):
            _add_issue(
                issues,
                "entry_count_mismatch",
            )

        if (
            checkpoint.final_record_hash
            != receipt.final_record_hash
        ):
            _add_issue(
                issues,
                "final_record_hash_mismatch",
            )

        if (
            checkpoint.signature
            != receipt.checkpoint_signature
        ):
            _add_issue(
                issues,
                "checkpoint_signature_snapshot_mismatch",
            )

        if (
            _sha256_bytes(checkpoint_bytes)
            != receipt.checkpoint_sha256
        ):
            _add_issue(
                issues,
                "checkpoint_sha256_mismatch",
            )

    status = (
        ApprovalWitnessVerificationStatus.VALID
        if not issues
        else ApprovalWitnessVerificationStatus.INVALID
    )

    return ApprovalWitnessVerification(
        status=status,
        issues=issues,
        receipt_id_valid=receipt_id_valid,
        record_hash_valid=record_hash_valid,
        witness_signature_valid=(
            witness_signature_valid
        ),
        registry_valid=registry_valid,
        registry_snapshot_match=(
            registry_snapshot_match
        ),
        checkpoint_snapshot_match=(
            checkpoint_snapshot_match
        ),
    )
