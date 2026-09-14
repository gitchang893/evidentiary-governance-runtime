from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eg_runtime.evidence import EvidenceRecorder
from eg_runtime.models import Preservation
from eg_runtime.preservation import (
    PreservationVerificationStatus,
    create_preservation_bundle,
    verify_preservation_bundle,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


def fixed_clock() -> datetime:
    return BASE_TIME + timedelta(hours=1)


def create_source_evidence(
    path: Path,
    *,
    run_id: str = "run-preservation-001",
    proposal_id: str = "proposal-001",
) -> None:
    recorder = EvidenceRecorder(
        path,
        run_id,
    )

    recorder.start(
        {
            "task_id": "task-001",
            "proposal_id": proposal_id,
            "policy_version": "1.0",
            "metadata": {
                "condition": "c3_organizational",
                "decision_phase": "pre_execution",
            },
        },
        timestamp=BASE_TIME,
    )

    recorder.append(
        "context_loaded",
        {
            "context": {
                "task_id": "task-001",
                "actor": "agent-01",
                "authorized_purpose": (
                    "records-maintenance"
                ),
                "declared_plan": [
                    "inspect approval",
                    "modify approved record",
                ],
                "approval": {
                    "approval_id": "approval-001",
                },
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=1),
    )

    recorder.append(
        "proposal_created",
        {
            "proposal": {
                "proposal_id": proposal_id,
                "tool": "modify_record",
                "arguments": {
                    "record_id": "R-42",
                    "updates": {
                        "status": "deleted",
                    },
                },
                "declared_purpose": (
                    "records-maintenance"
                ),
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=2),
    )

    recorder.append(
        "norms_selected",
        {
            "norm_ids": [
                "APPROVAL-BINDING-01",
            ],
        },
        timestamp=BASE_TIME + timedelta(seconds=3),
    )

    recorder.append(
        "governance_decision",
        {
            "decision": {
                "decision_id": "decision-001",
                "disposition": "block",
                "preservation": "incident",
                "applicable_norms": [
                    "APPROVAL-BINDING-01",
                ],
                "reasons": [
                    "approval object mismatch",
                ],
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=4),
    )

    recorder.append(
        "tool_blocked",
        {
            "decision_id": "decision-001",
            "execution": {
                "execution_id": "execution-001",
                "proposal_id": proposal_id,
                "tool": "modify_record",
                "executed": False,
                "side_effect": False,
            },
        },
        timestamp=BASE_TIME + timedelta(seconds=5),
    )

    recorder.complete(
        {
            "operational_outcome": "blocked",
            "executed": False,
            "side_effect": False,
            "violation_detected": True,
        },
        timestamp=BASE_TIME + timedelta(seconds=6),
    )


def test_minimal_bundle_retains_summaries_and_digests(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "minimal.json"

    create_source_evidence(source)

    bundle = create_preservation_bundle(
        source,
        output,
        Preservation.MINIMAL,
        clock=fixed_clock,
    )

    assert bundle.payload_mode == "summary"
    assert bundle.incident_lock is False

    event_types = {
        entry["event_type"]
        for entry in bundle.entries
    }

    assert "context_loaded" not in event_types
    assert "norms_selected" not in event_types
    assert "proposal_created" in event_types
    assert "governance_decision" in event_types
    assert "tool_blocked" in event_types

    proposal_entry = next(
        entry
        for entry in bundle.entries
        if entry["event_type"]
        == "proposal_created"
    )

    assert (
        "arguments"
        not in proposal_entry["summary"]["proposal"]
    )

    assert len(
        proposal_entry["payload_sha256"]
    ) == 64

    verification = verify_preservation_bundle(
        output,
        source_path=source,
    )

    assert (
        verification.status
        is PreservationVerificationStatus.VALID
    )

    assert verification.source_match is True


def test_full_bundle_retains_complete_source_events(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "full.json"

    create_source_evidence(source)

    bundle = create_preservation_bundle(
        source,
        output,
        Preservation.FULL,
        clock=fixed_clock,
    )

    assert bundle.payload_mode == "full"
    assert bundle.incident_lock is False

    assert (
        len(bundle.entries)
        == bundle.source_event_count
    )

    context_event = next(
        entry
        for entry in bundle.entries
        if entry["event_type"] == "context_loaded"
    )

    assert (
        context_event["payload"]["context"][
            "authorized_purpose"
        ]
        == "records-maintenance"
    )

    verification = verify_preservation_bundle(
        output,
        source_path=source,
    )

    assert verification.valid is True


def test_incident_bundle_applies_incident_lock(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "incident.json"

    create_source_evidence(source)

    bundle = create_preservation_bundle(
        source,
        output,
        Preservation.INCIDENT,
        incident_reason=(
            "approval scope violation"
        ),
        clock=fixed_clock,
    )

    assert bundle.payload_mode == "full"
    assert bundle.incident_lock is True

    assert (
        bundle.incident_reason
        == "approval scope violation"
    )

    assert (
        len(bundle.entries)
        == bundle.source_event_count
    )

    verification = verify_preservation_bundle(
        output,
        source_path=source,
    )

    assert verification.valid is True


def test_bundle_tampering_is_detected(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "minimal.json"

    create_source_evidence(source)

    create_preservation_bundle(
        source,
        output,
        Preservation.MINIMAL,
        clock=fixed_clock,
    )

    raw = json.loads(
        output.read_text(encoding="utf-8")
    )

    raw["entries"][0]["event_type"] = (
        "tampered_event"
    )

    output.write_text(
        json.dumps(
            raw,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    verification = verify_preservation_bundle(
        output,
        source_path=source,
    )

    assert (
        verification.status
        is PreservationVerificationStatus.INVALID
    )

    assert (
        "bundle_hash_mismatch"
        in verification.issues
    )


def test_bundle_detects_different_source_chain(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    other_source = tmp_path / "other.jsonl"
    output = tmp_path / "full.json"

    create_source_evidence(
        source,
        run_id="run-source",
        proposal_id="proposal-source",
    )

    create_source_evidence(
        other_source,
        run_id="run-other",
        proposal_id="proposal-other",
    )

    create_preservation_bundle(
        source,
        output,
        Preservation.FULL,
        clock=fixed_clock,
    )

    verification = verify_preservation_bundle(
        output,
        source_path=other_source,
    )

    assert (
        verification.status
        is PreservationVerificationStatus.INVALID
    )

    assert verification.source_match is False

    assert (
        "source_sha256_mismatch"
        in verification.issues
    )
