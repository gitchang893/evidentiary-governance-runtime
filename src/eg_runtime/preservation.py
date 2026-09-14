from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from eg_runtime.evidence import (
    EvidenceVerifier,
    VerificationStatus,
)
from eg_runtime.models import (
    FrozenModel,
    Preservation,
    utc_now,
)


class PreservationError(RuntimeError):
    """Raised when evidence preservation cannot be completed."""


class PreservationVerificationStatus(StrEnum):
    """Integrity status of a preservation bundle."""

    VALID = "valid"
    INVALID = "invalid"


class PreservationBundle(FrozenModel):
    """A directive-specific preserved representation of evidence."""

    bundle_version: str = "1.0"
    run_id: str = Field(min_length=1)
    directive: Preservation
    created_at: datetime
    source_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    source_event_count: int = Field(ge=1)
    source_integrity_status: VerificationStatus
    source_final_event_hash: str | None = None
    payload_mode: Literal["summary", "full"]
    incident_lock: bool
    incident_reason: str | None = None
    entries: list[dict[str, Any]] = Field(min_length=1)
    bundle_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class PreservationVerificationResult(FrozenModel):
    """Verification result for one preservation bundle."""

    status: PreservationVerificationStatus
    issues: list[str]
    bundle_hash: str | None
    source_match: bool | None

    @property
    def valid(self) -> bool:
        return (
            self.status
            is PreservationVerificationStatus.VALID
        )


MINIMAL_EVENT_TYPES = frozenset(
    {
        "run_started",
        "proposal_created",
        "proposal_logged",
        "governance_decision",
        "post_hoc_governance_decision",
        "tool_invoked",
        "tool_completed",
        "tool_failed",
        "tool_blocked",
        "approval_required",
        "run_completed",
    }
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
    """Return a lowercase SHA-256 digest."""

    return hashlib.sha256(value).hexdigest()


def _sha256_json(
    value: Any,
) -> str:
    """Hash a JSON-compatible value canonically."""

    return _sha256_bytes(
        _canonical_json(value).encode("utf-8")
    )


def _read_jsonl(
    path: Path,
) -> list[dict[str, Any]]:
    """Read one evidence JSONL file."""

    if not path.exists():
        raise PreservationError(
            f"evidence file does not exist: {path}"
        )

    records: list[dict[str, Any]] = []

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PreservationError(
                f"line {line_number} contains invalid JSON"
            ) from exc

        if not isinstance(value, dict):
            raise PreservationError(
                f"line {line_number} must contain "
                "a JSON object"
            )

        records.append(value)

    if not records:
        raise PreservationError(
            "evidence file contains no events"
        )

    return records


def _selected_keys(
    value: dict[str, Any],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    """Copy selected keys that are present."""

    return {
        key: value[key]
        for key in keys
        if key in value
    }


def _minimal_payload_summary(
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a reduced operational summary of one payload."""

    if event_type == "run_started":
        return _selected_keys(
            payload,
            (
                "task_id",
                "proposal_id",
                "policy_version",
                "metadata",
            ),
        )

    if event_type in {
        "proposal_created",
        "proposal_logged",
    }:
        proposal = payload.get("proposal")

        if isinstance(proposal, dict):
            return {
                "proposal": _selected_keys(
                    proposal,
                    (
                        "proposal_id",
                        "tool",
                        "declared_purpose",
                    ),
                )
            }

        return {}

    if event_type in {
        "governance_decision",
        "post_hoc_governance_decision",
    }:
        decision = payload.get("decision")

        summary: dict[str, Any] = {}

        if isinstance(decision, dict):
            summary["decision"] = _selected_keys(
                decision,
                (
                    "decision_id",
                    "disposition",
                    "preservation",
                    "applicable_norms",
                    "reasons",
                ),
            )

        if "execution_already_occurred" in payload:
            summary["execution_already_occurred"] = (
                payload["execution_already_occurred"]
            )

        return summary

    if event_type == "tool_invoked":
        return _selected_keys(
            payload,
            (
                "proposal_id",
                "tool",
            ),
        )

    if event_type in {
        "tool_completed",
        "tool_failed",
        "tool_blocked",
    }:
        summary = _selected_keys(
            payload,
            (
                "decision_id",
                "proposal_id",
            ),
        )

        execution = payload.get("execution")

        if isinstance(execution, dict):
            summary["execution"] = _selected_keys(
                execution,
                (
                    "execution_id",
                    "proposal_id",
                    "tool",
                    "executed",
                    "side_effect",
                    "error",
                ),
            )

        return summary

    if event_type == "approval_required":
        return _selected_keys(
            payload,
            (
                "proposal_id",
                "decision_id",
                "approval_request_id",
            ),
        )

    if event_type == "run_completed":
        return _selected_keys(
            payload,
            (
                "operational_outcome",
                "executed",
                "side_effect",
                "violation_detected",
            ),
        )

    return {}


def _minimal_entry(
    event: dict[str, Any],
) -> dict[str, Any]:
    """Create a summarized entry anchored to the source event."""

    payload = event.get("payload")

    if not isinstance(payload, dict):
        payload = {}

    event_type = event.get("event_type")

    if not isinstance(event_type, str):
        event_type = "unknown"

    return {
        "run_id": event.get("run_id"),
        "sequence": event.get("sequence"),
        "event_type": event_type,
        "timestamp": event.get("timestamp"),
        "source_event_hash": event.get("record_hash"),
        "payload_sha256": _sha256_json(payload),
        "summary": _minimal_payload_summary(
            event_type,
            payload,
        ),
    }


def _bundle_content(
    *,
    run_id: str,
    directive: Preservation,
    created_at: datetime,
    source_sha256: str,
    source_event_count: int,
    source_final_event_hash: str | None,
    payload_mode: Literal["summary", "full"],
    incident_lock: bool,
    incident_reason: str | None,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create the hashable portion of a preservation bundle."""

    return {
        "bundle_version": "1.0",
        "run_id": run_id,
        "directive": directive.value,
        "created_at": created_at.isoformat(),
        "source_sha256": source_sha256,
        "source_event_count": source_event_count,
        "source_integrity_status": (
            VerificationStatus.VALID.value
        ),
        "source_final_event_hash": (
            source_final_event_hash
        ),
        "payload_mode": payload_mode,
        "incident_lock": incident_lock,
        "incident_reason": incident_reason,
        "entries": entries,
    }


def create_preservation_bundle(
    source_path: str | Path,
    output_path: str | Path,
    directive: Preservation,
    *,
    incident_reason: str | None = None,
    clock: Callable[[], datetime] = utc_now,
    overwrite: bool = False,
) -> PreservationBundle:
    """Create a directive-specific preservation bundle."""

    source = Path(source_path)
    output = Path(output_path)

    if output.exists() and not overwrite:
        raise PreservationError(
            f"preservation bundle already exists: {output}"
        )

    verification = EvidenceVerifier().verify(source)

    if verification.status is not VerificationStatus.VALID:
        raise PreservationError(
            "source evidence integrity must be valid"
        )

    created_at = clock()

    if (
        created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise PreservationError(
            "preservation clock must be timezone-aware"
        )

    normalized_reason = (
        incident_reason.strip()
        if isinstance(incident_reason, str)
        else None
    )

    if (
        directive is Preservation.INCIDENT
        and not normalized_reason
    ):
        raise PreservationError(
            "incident preservation requires "
            "an incident reason"
        )

    records = _read_jsonl(source)

    run_ids = {
        value
        for event in records
        if isinstance(
            value := event.get("run_id"),
            str,
        )
        and value
    }

    if len(run_ids) != 1:
        raise PreservationError(
            "source evidence must contain exactly "
            "one run identifier"
        )

    run_id = next(iter(run_ids))

    source_bytes = source.read_bytes()
    source_sha256 = _sha256_bytes(source_bytes)

    raw_final_hash = records[-1].get(
        "record_hash"
    )

    source_final_event_hash = (
        raw_final_hash
        if isinstance(raw_final_hash, str)
        else None
    )

    if directive is Preservation.MINIMAL:
        payload_mode: Literal["summary", "full"] = (
            "summary"
        )

        entries = [
            _minimal_entry(event)
            for event in records
            if event.get("event_type")
            in MINIMAL_EVENT_TYPES
        ]

        incident_lock = False

    else:
        payload_mode = "full"
        entries = records
        incident_lock = (
            directive is Preservation.INCIDENT
        )

    content = _bundle_content(
        run_id=run_id,
        directive=directive,
        created_at=created_at,
        source_sha256=source_sha256,
        source_event_count=len(records),
        source_final_event_hash=(
            source_final_event_hash
        ),
        payload_mode=payload_mode,
        incident_lock=incident_lock,
        incident_reason=normalized_reason,
        entries=entries,
    )

    bundle_hash = _sha256_json(content)

    serialized = {
        **content,
        "bundle_hash": bundle_hash,
    }

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            serialized,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return PreservationBundle.model_validate(
        serialized
    )


def verify_preservation_bundle(
    bundle_path: str | Path,
    *,
    source_path: str | Path | None = None,
) -> PreservationVerificationResult:
    """Verify a preservation bundle and optional source evidence."""

    path = Path(bundle_path)
    issues: list[str] = []
    source_match: bool | None = None

    if not path.exists():
        raise PreservationError(
            f"preservation bundle does not exist: {path}"
        )

    try:
        raw = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise PreservationError(
            "preservation bundle contains invalid JSON"
        ) from exc

    if not isinstance(raw, dict):
        raise PreservationError(
            "preservation bundle root must be an object"
        )

    supplied_hash = raw.get("bundle_hash")

    hashable = {
        key: value
        for key, value in raw.items()
        if key != "bundle_hash"
    }

    calculated_hash = _sha256_json(
        hashable
    )

    if supplied_hash != calculated_hash:
        issues.append("bundle_hash_mismatch")

    try:
        bundle = PreservationBundle.model_validate(
            raw
        )
    except ValidationError:
        bundle = None
        issues.append("bundle_schema_invalid")

    if bundle is not None:
        if (
            bundle.payload_mode == "full"
            and len(bundle.entries)
            != bundle.source_event_count
        ):
            issues.append(
                "full_bundle_event_count_mismatch"
            )

        if (
            bundle.directive is Preservation.MINIMAL
            and bundle.payload_mode != "summary"
        ):
            issues.append(
                "minimal_bundle_mode_mismatch"
            )

        if (
            bundle.directive is Preservation.INCIDENT
            and not bundle.incident_lock
        ):
            issues.append(
                "incident_lock_missing"
            )

        if source_path is not None:
            source = Path(source_path)

            if not source.exists():
                source_match = False
                issues.append("source_missing")
            else:
                actual_source_hash = _sha256_bytes(
                    source.read_bytes()
                )

                source_match = (
                    actual_source_hash
                    == bundle.source_sha256
                )

                if not source_match:
                    issues.append(
                        "source_sha256_mismatch"
                    )

    status = (
        PreservationVerificationStatus.VALID
        if not issues
        else PreservationVerificationStatus.INVALID
    )

    return PreservationVerificationResult(
        status=status,
        issues=issues,
        bundle_hash=(
            supplied_hash
            if isinstance(supplied_hash, str)
            else None
        ),
        source_match=source_match,
    )
