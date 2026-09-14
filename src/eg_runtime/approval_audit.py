from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from eg_runtime.approval_lineage import (
    ApprovalLineageStatus,
    ApprovalLineageVerifier,
)
from eg_runtime.approval_workflow import (
    ApprovalResolution,
    ApprovalResolutionStatus,
    PendingApproval,
)
from eg_runtime.evidence import (
    EvidenceEvent,
    EvidenceVerifier,
    VerificationStatus,
)
from eg_runtime.models import FrozenModel


_HASH_PATTERN = r"^[0-9a-f]{64}$"


class ApprovalAuditError(RuntimeError):
    """Raised when an approval audit record cannot be created."""


class ApprovalAuditVerificationStatus(StrEnum):
    """Verification status for an approval audit record."""

    VALID = "valid"
    INVALID = "invalid"


class ApprovalAuditRecord(FrozenModel):
    """Durable record of one human approval workflow."""

    audit_version: str = Field(
        default="1.0",
        pattern=r"^1\.0$",
    )
    approval_request_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    proposal_id: str = Field(min_length=1)
    escalation_decision_id: str = Field(min_length=1)

    original_run_id: str = Field(min_length=1)
    original_evidence_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    original_event_count: int = Field(ge=1)
    original_final_record_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )

    resolution_status: ApprovalResolutionStatus
    resolver: str = Field(min_length=1)
    resolved_at: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    validation_errors: list[str] = Field(
        default_factory=list
    )
    approval_id: str | None = None

    resumed_run_id: str | None = None
    resumed_evidence_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )
    resumed_event_count: int | None = Field(
        default=None,
        ge=1,
    )
    resumed_final_record_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )

    record_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_HASH_PATTERN,
    )


class ApprovalAuditVerification(FrozenModel):
    """Verification result for one approval audit record."""

    status: ApprovalAuditVerificationStatus
    issues: list[str]
    record_hash_valid: bool
    original_source_match: bool | None
    resumed_source_match: bool | None

    @property
    def valid(self) -> bool:
        """Return whether the audit record is valid."""

        return (
            self.status
            is ApprovalAuditVerificationStatus.VALID
        )


class _EvidenceIdentity(FrozenModel):
    """Identity of one evidence chain."""

    run_id: str
    file_sha256: str
    event_count: int
    final_record_hash: str


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


def _sha256_bytes(
    value: bytes,
) -> str:
    """Return a SHA-256 digest."""

    return hashlib.sha256(value).hexdigest()


def _read_events(
    path: Path,
) -> list[EvidenceEvent]:
    """Read structured events from one JSONL chain."""

    if not path.exists():
        raise ApprovalAuditError(
            f"evidence file does not exist: {path}"
        )

    events: list[EvidenceEvent] = []

    for line_number, line in enumerate(
        path.read_text(
            encoding="utf-8"
        ).splitlines(),
        start=1,
    ):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApprovalAuditError(
                f"line {line_number} contains invalid JSON"
            ) from exc

        try:
            event = EvidenceEvent.model_validate(raw)
        except ValidationError as exc:
            raise ApprovalAuditError(
                f"line {line_number} contains "
                "an invalid evidence event"
            ) from exc

        events.append(event)

    if not events:
        raise ApprovalAuditError(
            "evidence file contains no events"
        )

    return events


def _evidence_identity(
    path: Path,
) -> _EvidenceIdentity:
    """Verify and identify one evidence chain."""

    verification = EvidenceVerifier().verify(path)

    if verification.status is not VerificationStatus.VALID:
        raise ApprovalAuditError(
            f"evidence integrity is invalid: {path}"
        )

    events = _read_events(path)

    run_ids = {
        event.run_id
        for event in events
    }

    if len(run_ids) != 1:
        raise ApprovalAuditError(
            "evidence chain contains multiple run IDs"
        )

    return _EvidenceIdentity(
        run_id=next(iter(run_ids)),
        file_sha256=_sha256_bytes(
            path.read_bytes()
        ),
        event_count=len(events),
        final_record_hash=events[-1].record_hash,
    )


def _record_hash(
    record: ApprovalAuditRecord,
) -> str:
    """Compute the hash of an audit record."""

    content = record.model_dump(
        mode="json",
        exclude={"record_hash"},
    )

    return _sha256_bytes(
        _canonical_json(content).encode("utf-8")
    )


def create_approval_audit_record(
    *,
    output_path: str | Path,
    original_evidence_path: str | Path,
    pending: PendingApproval,
    resolution: ApprovalResolution,
    resumed_evidence_path: str | Path | None = None,
    overwrite: bool = False,
) -> ApprovalAuditRecord:
    """Create a durable audit record for an approval workflow."""

    output = Path(output_path)
    original_path = Path(original_evidence_path)

    resumed_path = (
        Path(resumed_evidence_path)
        if resumed_evidence_path is not None
        else None
    )

    if output.exists() and not overwrite:
        raise ApprovalAuditError(
            f"output file already exists: {output}"
        )

    lineage = ApprovalLineageVerifier().verify(
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=resumed_path,
    )

    if lineage.status is not ApprovalLineageStatus.VALID:
        codes = ", ".join(
            issue.code
            for issue in lineage.issues
        )

        raise ApprovalAuditError(
            f"invalid approval lineage: {codes}"
        )

    original_identity = _evidence_identity(
        original_path
    )

    resumed_identity = (
        _evidence_identity(resumed_path)
        if resumed_path is not None
        else None
    )

    approval_id = (
        resolution.approval.approval_id
        if resolution.approval is not None
        else None
    )

    draft = ApprovalAuditRecord(
        approval_request_id=(
            pending.approval_request_id
        ),
        proposal_id=pending.proposal.proposal_id,
        escalation_decision_id=(
            pending.decision.decision_id
        ),
        original_run_id=original_identity.run_id,
        original_evidence_sha256=(
            original_identity.file_sha256
        ),
        original_event_count=(
            original_identity.event_count
        ),
        original_final_record_hash=(
            original_identity.final_record_hash
        ),
        resolution_status=resolution.status,
        resolver=resolution.resolver,
        resolved_at=(
            resolution.resolved_at.isoformat()
        ),
        reason=resolution.reason,
        validation_errors=(
            resolution.validation_errors
        ),
        approval_id=approval_id,
        resumed_run_id=(
            resumed_identity.run_id
            if resumed_identity is not None
            else None
        ),
        resumed_evidence_sha256=(
            resumed_identity.file_sha256
            if resumed_identity is not None
            else None
        ),
        resumed_event_count=(
            resumed_identity.event_count
            if resumed_identity is not None
            else None
        ),
        resumed_final_record_hash=(
            resumed_identity.final_record_hash
            if resumed_identity is not None
            else None
        ),
        record_hash="0" * 64,
    )

    record = draft.model_copy(
        update={
            "record_hash": _record_hash(draft),
        }
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return record


def verify_approval_audit_record(
    path: str | Path,
    *,
    original_evidence_path: str | Path | None = None,
    resumed_evidence_path: str | Path | None = None,
) -> ApprovalAuditVerification:
    """Verify an approval audit record and its source chains."""

    audit_path = Path(path)

    if not audit_path.exists():
        raise ApprovalAuditError(
            f"audit record does not exist: {audit_path}"
        )

    try:
        raw = json.loads(
            audit_path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise ApprovalAuditError(
            "audit record contains invalid JSON"
        ) from exc

    try:
        record = ApprovalAuditRecord.model_validate(
            raw
        )
    except ValidationError as exc:
        raise ApprovalAuditError(
            "audit record has an invalid schema"
        ) from exc

    issues: list[str] = []

    record_hash_valid = (
        _record_hash(record)
        == record.record_hash
    )

    if not record_hash_valid:
        issues.append("record_hash_mismatch")

    resumed_fields = (
        record.resumed_run_id,
        record.resumed_evidence_sha256,
        record.resumed_event_count,
        record.resumed_final_record_hash,
    )

    if (
        record.resolution_status
        is ApprovalResolutionStatus.APPROVED
    ):
        if record.approval_id is None:
            issues.append(
                "approved_record_missing_approval"
            )

        if any(
            value is None
            for value in resumed_fields
        ):
            issues.append(
                "approved_record_missing_resume"
            )

    else:
        if any(
            value is not None
            for value in resumed_fields
        ):
            issues.append(
                "non_approved_record_has_resume"
            )

    original_source_match: bool | None = None

    if original_evidence_path is not None:
        try:
            original = _evidence_identity(
                Path(original_evidence_path)
            )
        except ApprovalAuditError:
            issues.append(
                "original_source_invalid"
            )
            original_source_match = False
        else:
            original_source_match = (
                original.run_id
                == record.original_run_id
                and original.file_sha256
                == record.original_evidence_sha256
                and original.event_count
                == record.original_event_count
                and original.final_record_hash
                == record.original_final_record_hash
            )

            if not original_source_match:
                issues.append(
                    "original_source_mismatch"
                )

    resumed_source_match: bool | None = None

    if resumed_evidence_path is not None:
        try:
            resumed = _evidence_identity(
                Path(resumed_evidence_path)
            )
        except ApprovalAuditError:
            issues.append(
                "resumed_source_invalid"
            )
            resumed_source_match = False
        else:
            resumed_source_match = (
                resumed.run_id
                == record.resumed_run_id
                and resumed.file_sha256
                == record.resumed_evidence_sha256
                and resumed.event_count
                == record.resumed_event_count
                and resumed.final_record_hash
                == record.resumed_final_record_hash
            )

            if not resumed_source_match:
                issues.append(
                    "resumed_source_mismatch"
                )

    status = (
        ApprovalAuditVerificationStatus.VALID
        if not issues
        else ApprovalAuditVerificationStatus.INVALID
    )

    return ApprovalAuditVerification(
        status=status,
        issues=issues,
        record_hash_valid=record_hash_valid,
        original_source_match=original_source_match,
        resumed_source_match=resumed_source_match,
    )
