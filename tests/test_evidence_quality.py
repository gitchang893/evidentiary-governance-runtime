from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from eg_runtime.evidence import EvidenceRecorder
from eg_runtime.evidence_quality import (
    EvidenceQualityError,
    assess_evidence_completeness,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


REQUIRED_EVIDENCE = [
    "authorized_purpose",
    "declared_plan",
    "approval",
    "proposal",
    "applicable_norms",
    "predicate_results",
    "governance_decision",
    "execution_status",
    "integrity",
]


def create_full_governance_record(
    path: Path,
) -> None:
    """Create a complete organization-level evidence record."""

    recorder = EvidenceRecorder(
        path,
        "run-full",
    )

    recorder.start(
        {
            "task_id": "task-001",
            "proposal_id": "proposal-001",
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
                "task": "Modify the approved record.",
                "actor": "agent-01",
                "role": "records-operator",
                "authorized_purpose": "records-maintenance",
                "declared_plan": [
                    "inspect approval",
                    "modify approved record",
                ],
                "history": [],
                "data_context": {
                    "classification": "internal",
                },
                "approval": {
                    "approval_id": "approval-001",
                    "object_id": "R-17",
                },
                "policy_version": "1.0",
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=1),
    )

    recorder.append(
        "proposal_created",
        {
            "proposal": {
                "proposal_id": "proposal-001",
                "tool": "modify_record",
                "arguments": {
                    "record_id": "R-42",
                    "updates": {
                        "status": "deleted",
                    },
                },
                "justification": "Apply the approval.",
                "declared_purpose": "records-maintenance",
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
            "norm_versions": {
                "APPROVAL-BINDING-01": "1.0",
            },
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
                "predicate_results": [
                    {
                        "norm_id": "APPROVAL-BINDING-01",
                        "predicate_type": "approval_binding",
                        "satisfied": False,
                        "reason": "approval object mismatch",
                    }
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
                "proposal_id": "proposal-001",
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
        },
        timestamp=BASE_TIME + timedelta(seconds=6),
    )


def create_ordinary_log(
    path: Path,
) -> None:
    """Create an ordinary execution log without governance context."""

    recorder = EvidenceRecorder(
        path,
        "run-log",
    )

    recorder.start(
        {
            "task_id": "task-001",
            "proposal_id": "proposal-001",
            "metadata": {
                "condition": "c0_logging",
                "decision_phase": "none",
            },
        },
        timestamp=BASE_TIME,
    )

    recorder.append(
        "proposal_logged",
        {
            "proposal": {
                "proposal_id": "proposal-001",
                "tool": "modify_record",
                "arguments": {
                    "record_id": "R-42",
                    "updates": {
                        "status": "deleted",
                    },
                },
                "justification": "Update the record.",
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=1),
    )

    recorder.append(
        "tool_invoked",
        {
            "proposal_id": "proposal-001",
            "tool": "modify_record",
        },
        timestamp=BASE_TIME + timedelta(seconds=2),
    )

    recorder.append(
        "tool_completed",
        {
            "execution": {
                "execution_id": "execution-001",
                "proposal_id": "proposal-001",
                "tool": "modify_record",
                "executed": True,
                "side_effect": True,
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=3),
    )

    recorder.complete(
        {
            "operational_outcome": "executed",
            "executed": True,
            "side_effect": True,
        },
        timestamp=BASE_TIME + timedelta(seconds=4),
    )


def read_records(
    path: Path,
) -> list[dict[str, Any]]:
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


def test_full_governance_record_is_complete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "full.jsonl"
    create_full_governance_record(path)

    result = assess_evidence_completeness(
        path,
        REQUIRED_EVIDENCE,
    )

    assert result.completeness == 1.0
    assert result.present == REQUIRED_EVIDENCE
    assert result.missing == []
    assert result.integrity_valid is True


def test_ordinary_log_has_partial_completeness(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ordinary.jsonl"
    create_ordinary_log(path)

    result = assess_evidence_completeness(
        path,
        REQUIRED_EVIDENCE,
    )

    assert result.completeness == pytest.approx(
        3 / 9
    )

    assert result.present == [
        "proposal",
        "execution_status",
        "integrity",
    ]

    assert "authorized_purpose" in result.missing
    assert "governance_decision" in result.missing


def test_integrity_failure_removes_integrity_element(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tampered.jsonl"
    create_full_governance_record(path)

    records = read_records(path)
    records[2]["payload"]["proposal"]["tool"] = "send_message"
    write_records(path, records)

    result = assess_evidence_completeness(
        path,
        REQUIRED_EVIDENCE,
    )

    assert result.integrity_valid is False
    assert "integrity" in result.missing
    assert result.completeness == pytest.approx(
        8 / 9
    )


def test_unknown_evidence_element_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "full.jsonl"
    create_full_governance_record(path)

    with pytest.raises(
        EvidenceQualityError,
        match="unsupported evidence elements",
    ):
        assess_evidence_completeness(
            path,
            [
                "proposal",
                "unknown_evidence_element",
            ],
        )


def test_declared_purpose_is_detected_as_claim_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "declared-purpose.jsonl"
    create_full_governance_record(path)

    result = assess_evidence_completeness(
        path,
        ["declared_purpose"],
    )

    assert result.completeness == 1.0
    assert result.present == ["declared_purpose"]
    assert result.missing == []


def test_missing_declared_purpose_is_reported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-purpose.jsonl"
    create_ordinary_log(path)

    result = assess_evidence_completeness(
        path,
        ["declared_purpose"],
    )

    assert result.completeness == 0.0
    assert result.present == []
    assert result.missing == ["declared_purpose"]
