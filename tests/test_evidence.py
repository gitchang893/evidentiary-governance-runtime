from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eg_runtime.evidence import (
    EvidenceRecorder,
    EvidenceVerifier,
    VerificationStatus,
)


START_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)

DECISION_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    1,
    tzinfo=UTC,
)

END_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    2,
    tzinfo=UTC,
)


def create_valid_evidence(path: Path) -> None:
    """Create a complete three-event evidence run."""

    recorder = EvidenceRecorder(
        path,
        "run-001",
    )

    recorder.start(
        {
            "task_id": "task-001",
            "policy_version": "1.0",
        },
        timestamp=START_TIME,
    )

    recorder.append(
        "governance_decision",
        {
            "proposal_id": "proposal-001",
            "disposition": "block",
            "preservation": "incident",
            "applicable_norms": [
                "APPROVAL-BINDING-01",
            ],
        },
        timestamp=DECISION_TIME,
    )

    recorder.complete(
        {
            "operational_outcome": "non-execution",
        },
        timestamp=END_TIME,
    )


def read_records(path: Path) -> list[dict[str, Any]]:
    """Read JSONL records into mutable dictionaries."""

    return [
        json.loads(line)
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
    ]


def write_records(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    """Write mutable dictionaries back to JSONL."""

    path.write_text(
        "".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def test_complete_evidence_run_is_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.jsonl"
    create_valid_evidence(path)

    result = EvidenceVerifier().verify(path)

    assert result.status is VerificationStatus.VALID
    assert result.chain_valid is True
    assert result.complete is True
    assert result.event_count == 3
    assert result.run_id == "run-001"
    assert result.final_hash is not None
    assert result.errors == []


def test_recorder_links_each_event_to_preceding_hash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.jsonl"
    create_valid_evidence(path)

    records = read_records(path)

    assert records[0]["previous_hash"] == "0" * 64
    assert (
        records[1]["previous_hash"]
        == records[0]["record_hash"]
    )
    assert (
        records[2]["previous_hash"]
        == records[1]["record_hash"]
    )


def test_payload_modification_is_detected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.jsonl"
    create_valid_evidence(path)

    records = read_records(path)
    records[1]["payload"]["disposition"] = "allow"
    write_records(path, records)

    result = EvidenceVerifier().verify(path)

    assert result.status is VerificationStatus.INVALID
    assert result.chain_valid is False
    assert any(
        "record_hash mismatch" in error
        for error in result.errors
    )


def test_event_deletion_is_detected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.jsonl"
    create_valid_evidence(path)

    records = read_records(path)
    del records[1]
    write_records(path, records)

    result = EvidenceVerifier().verify(path)

    assert result.status is VerificationStatus.INVALID
    assert result.chain_valid is False
    assert any(
        "sequence" in error
        or "previous_hash mismatch" in error
        for error in result.errors
    )


def test_event_reordering_is_detected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.jsonl"
    create_valid_evidence(path)

    records = read_records(path)
    records[1], records[2] = records[2], records[1]
    write_records(path, records)

    result = EvidenceVerifier().verify(path)

    assert result.status is VerificationStatus.INVALID
    assert result.chain_valid is False
    assert result.errors


def test_terminal_truncation_is_incomplete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.jsonl"
    create_valid_evidence(path)

    records = read_records(path)
    write_records(path, records[:-1])

    result = EvidenceVerifier().verify(path)

    assert result.status is VerificationStatus.INCOMPLETE
    assert result.chain_valid is True
    assert result.complete is False
    assert result.event_count == 2
