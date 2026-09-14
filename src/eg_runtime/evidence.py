from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from eg_runtime.models import FrozenModel, utc_now


GENESIS_HASH = "0" * 64


class EvidenceError(RuntimeError):
    """Base exception for evidence-recording failures."""


class EvidenceStateError(EvidenceError):
    """Raised when recorder operations occur in an invalid order."""


class VerificationStatus(StrEnum):
    """Overall outcome of evidence-file verification."""

    VALID = "valid"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


class EvidenceEvent(FrozenModel):
    """One hash-linked evidence event."""

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    event_type: str = Field(min_length=1)
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    record_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_timestamp(self) -> EvidenceEvent:
        """Require a timezone-aware event timestamp."""

        if (
            self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError("timestamp must be timezone-aware")

        return self


class VerificationResult(FrozenModel):
    """Result returned by the standalone evidence verifier."""

    status: VerificationStatus
    chain_valid: bool
    complete: bool
    event_count: int
    run_id: str | None = None
    final_hash: str | None = None
    errors: list[str] = Field(default_factory=list)


def canonicalize(value: Any) -> bytes:
    """Serialize JSON-compatible data in a deterministic form."""

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceError(
            "evidence content must be JSON-compatible"
        ) from exc

    return serialized.encode("utf-8")


def compute_record_hash(
    *,
    event_id: str,
    run_id: str,
    sequence: int,
    event_type: str,
    timestamp: str,
    payload: dict[str, Any],
    previous_hash: str,
) -> str:
    """Compute the hash committed by one evidence event."""

    material = {
        "event_id": event_id,
        "run_id": run_id,
        "sequence": sequence,
        "event_type": event_type,
        "timestamp": timestamp,
        "payload": payload,
        "previous_hash": previous_hash,
    }

    return hashlib.sha256(canonicalize(material)).hexdigest()


def _event_hash_material(event: EvidenceEvent) -> dict[str, Any]:
    """Return the fields covered by an event's record hash."""

    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat(),
        "payload": event.payload,
        "previous_hash": event.previous_hash,
    }


class EvidenceRecorder:
    """Append-only JSONL recorder with a per-run hash chain."""

    def __init__(
        self,
        path: str | Path,
        run_id: str,
        *,
        overwrite: bool = False,
        durable_writes: bool = False,
    ) -> None:
        if not run_id.strip():
            raise ValueError("run_id must contain text")

        self.path = Path(path)
        self.run_id = run_id
        self._durable_writes = durable_writes
        self._sequence = 0
        self._previous_hash = GENESIS_HASH
        self._started = False
        self._completed = False

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self.path.exists() and self.path.stat().st_size > 0:
            if not overwrite:
                raise EvidenceStateError(
                    f"evidence file already contains data: {self.path}"
                )

            self.path.write_text("", encoding="utf-8")

    def start(
        self,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: datetime | None = None,
    ) -> EvidenceEvent:
        """Create the initial run_started event."""

        if self._started:
            raise EvidenceStateError(
                "the evidence run has already started"
            )

        event = self._write_event(
            "run_started",
            payload or {},
            timestamp=timestamp,
        )

        self._started = True

        return event

    def append(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: datetime | None = None,
    ) -> EvidenceEvent:
        """Append one event to an active evidence run."""

        if not self._started:
            raise EvidenceStateError(
                "start() must be called before append()"
            )

        if self._completed:
            raise EvidenceStateError(
                "events cannot be appended after completion"
            )

        if event_type in {"run_started", "run_completed"}:
            raise EvidenceStateError(
                f"{event_type} is managed by the recorder"
            )

        return self._write_event(
            event_type,
            payload or {},
            timestamp=timestamp,
        )

    def complete(
        self,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: datetime | None = None,
    ) -> EvidenceEvent:
        """Create the terminal run_completed event."""

        if not self._started:
            raise EvidenceStateError(
                "start() must be called before complete()"
            )

        if self._completed:
            raise EvidenceStateError(
                "the evidence run has already completed"
            )

        completion_payload = {
            "status": "completed",
            "preceding_event_count": self._sequence,
        }

        if payload:
            completion_payload.update(payload)

        event = self._write_event(
            "run_completed",
            completion_payload,
            timestamp=timestamp,
        )

        self._completed = True

        return event

    @property
    def final_hash(self) -> str:
        """Return the latest committed hash."""

        return self._previous_hash

    @property
    def event_count(self) -> int:
        """Return the number of written events."""

        return self._sequence

    def _write_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        timestamp: datetime | None,
    ) -> EvidenceEvent:
        """Construct, hash, and persist one evidence event."""

        event_time = timestamp or utc_now()

        if (
            event_time.tzinfo is None
            or event_time.utcoffset() is None
        ):
            raise EvidenceError(
                "event timestamp must be timezone-aware"
            )

        canonicalize(payload)

        event_id = (
            f"{self.run_id}:event:{self._sequence:06d}"
        )

        record_hash = compute_record_hash(
            event_id=event_id,
            run_id=self.run_id,
            sequence=self._sequence,
            event_type=event_type,
            timestamp=event_time.isoformat(),
            payload=payload,
            previous_hash=self._previous_hash,
        )

        event = EvidenceEvent(
            event_id=event_id,
            run_id=self.run_id,
            sequence=self._sequence,
            event_type=event_type,
            timestamp=event_time,
            payload=payload,
            previous_hash=self._previous_hash,
            record_hash=record_hash,
        )

        serialized = json.dumps(
            event.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            stream.write(serialized)
            stream.write("\n")
            stream.flush()

            if self._durable_writes:
                os.fsync(stream.fileno())

        self._previous_hash = record_hash
        self._sequence += 1

        return event


class EvidenceVerifier:
    """Verify structure, order, completeness, and hash continuity."""

    def verify(
        self,
        path: str | Path,
    ) -> VerificationResult:
        """Verify one JSONL evidence file."""

        evidence_path = Path(path)

        if not evidence_path.exists():
            return VerificationResult(
                status=VerificationStatus.INVALID,
                chain_valid=False,
                complete=False,
                event_count=0,
                errors=[
                    f"evidence file does not exist: {evidence_path}"
                ],
            )

        lines = evidence_path.read_text(
            encoding="utf-8"
        ).splitlines()

        if not lines:
            return VerificationResult(
                status=VerificationStatus.INCOMPLETE,
                chain_valid=True,
                complete=False,
                event_count=0,
                errors=["evidence file is empty"],
            )

        events: list[EvidenceEvent] = []
        errors: list[str] = []
        expected_previous_hash = GENESIS_HASH
        expected_run_id: str | None = None

        for line_number, line in enumerate(lines, start=1):
            try:
                raw_event = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(
                    f"line {line_number}: invalid JSON: {exc.msg}"
                )
                continue

            try:
                event = EvidenceEvent.model_validate(raw_event)
            except ValueError as exc:
                errors.append(
                    f"line {line_number}: invalid event schema: {exc}"
                )
                continue

            events.append(event)

            expected_sequence = line_number - 1

            if event.sequence != expected_sequence:
                errors.append(
                    f"line {line_number}: expected sequence "
                    f"{expected_sequence}, found {event.sequence}"
                )

            expected_event_id = (
                f"{event.run_id}:event:{event.sequence:06d}"
            )

            if event.event_id != expected_event_id:
                errors.append(
                    f"line {line_number}: event_id is inconsistent "
                    f"with run_id and sequence"
                )

            if expected_run_id is None:
                expected_run_id = event.run_id
            elif event.run_id != expected_run_id:
                errors.append(
                    f"line {line_number}: run_id changed from "
                    f"{expected_run_id} to {event.run_id}"
                )

            if event.previous_hash != expected_previous_hash:
                errors.append(
                    f"line {line_number}: previous_hash mismatch"
                )

            material = _event_hash_material(event)

            computed_hash = compute_record_hash(
                event_id=str(material["event_id"]),
                run_id=str(material["run_id"]),
                sequence=int(material["sequence"]),
                event_type=str(material["event_type"]),
                timestamp=str(material["timestamp"]),
                payload=event.payload,
                previous_hash=str(material["previous_hash"]),
            )

            if event.record_hash != computed_hash:
                errors.append(
                    f"line {line_number}: record_hash mismatch"
                )

            expected_previous_hash = event.record_hash

        complete = (
            bool(events)
            and events[0].event_type == "run_started"
            and events[-1].event_type == "run_completed"
        )

        if events and events[0].event_type != "run_started":
            errors.append(
                "the first event is not run_started"
            )

        chain_valid = len(errors) == 0

        if not chain_valid:
            status = VerificationStatus.INVALID
        elif complete:
            status = VerificationStatus.VALID
        else:
            status = VerificationStatus.INCOMPLETE

        return VerificationResult(
            status=status,
            chain_valid=chain_valid,
            complete=complete,
            event_count=len(events),
            run_id=expected_run_id,
            final_hash=(
                events[-1].record_hash
                if events
                else None
            ),
            errors=errors,
        )
    
