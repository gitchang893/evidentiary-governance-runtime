from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from eg_runtime.evidence import EvidenceRecorder
from eg_runtime.evidence_semantics import (
    EvidenceSemanticsVerifier,
    SemanticVerificationStatus,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


def start_run(
    recorder: EvidenceRecorder,
    proposal_id: str = "proposal-001",
) -> None:
    recorder.start(
        {
            "task_id": "task-001",
            "proposal_id": proposal_id,
        },
        timestamp=BASE_TIME,
    )


def add_proposal(
    recorder: EvidenceRecorder,
    proposal_id: str = "proposal-001",
) -> None:
    recorder.append(
        "proposal_created",
        {
            "proposal": {
                "proposal_id": proposal_id,
                "tool": "modify_record",
                "arguments": {
                    "record_id": "R-17",
                    "updates": {
                        "status": "reviewed",
                    },
                },
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=1),
    )


def complete_run(
    recorder: EvidenceRecorder,
    *,
    executed: bool,
) -> None:
    recorder.complete(
        {
            "operational_outcome": (
                "executed"
                if executed
                else "blocked"
            ),
            "executed": executed,
            "side_effect": executed,
        },
        timestamp=BASE_TIME + timedelta(seconds=6),
    )


def test_valid_allow_execution_is_semantically_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "allow.jsonl"
    recorder = EvidenceRecorder(path, "run-allow")

    start_run(recorder)
    add_proposal(recorder)

    recorder.append(
        "governance_decision",
        {
            "decision": {
                "decision_id": "decision-001",
                "disposition": "allow",
                "preservation": "minimal",
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=2),
    )

    recorder.append(
        "tool_invoked",
        {
            "proposal_id": "proposal-001",
            "tool": "modify_record",
        },
        timestamp=BASE_TIME + timedelta(seconds=3),
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
        timestamp=BASE_TIME + timedelta(seconds=4),
    )

    complete_run(
        recorder,
        executed=True,
    )

    result = EvidenceSemanticsVerifier().verify(path)

    assert (
        result.status
        is SemanticVerificationStatus.VALID
    )
    assert result.issues == []
    assert result.proposal_id == "proposal-001"


def test_mismatched_execution_proposal_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mismatch.jsonl"
    recorder = EvidenceRecorder(path, "run-mismatch")

    start_run(recorder)
    add_proposal(recorder)

    recorder.append(
        "governance_decision",
        {
            "decision": {
                "decision_id": "decision-001",
                "disposition": "allow",
                "preservation": "minimal",
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=2),
    )

    recorder.append(
        "tool_invoked",
        {
            "proposal_id": "proposal-999",
            "tool": "modify_record",
        },
        timestamp=BASE_TIME + timedelta(seconds=3),
    )

    recorder.append(
        "tool_completed",
        {
            "execution": {
                "execution_id": "execution-001",
                "proposal_id": "proposal-999",
                "tool": "modify_record",
                "executed": True,
                "side_effect": True,
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=4),
    )

    complete_run(
        recorder,
        executed=True,
    )

    result = EvidenceSemanticsVerifier().verify(path)

    assert (
        result.status
        is SemanticVerificationStatus.INVALID
    )

    assert any(
        issue.code == "proposal_execution_mismatch"
        for issue in result.issues
    )


def test_block_followed_by_execution_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid-block.jsonl"
    recorder = EvidenceRecorder(
        path,
        "run-invalid-block",
    )

    start_run(recorder)
    add_proposal(recorder)

    recorder.append(
        "governance_decision",
        {
            "decision": {
                "decision_id": "decision-001",
                "disposition": "block",
                "preservation": "incident",
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=2),
    )

    recorder.append(
        "tool_invoked",
        {
            "proposal_id": "proposal-001",
            "tool": "modify_record",
        },
        timestamp=BASE_TIME + timedelta(seconds=3),
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
        timestamp=BASE_TIME + timedelta(seconds=4),
    )

    complete_run(
        recorder,
        executed=True,
    )

    result = EvidenceSemanticsVerifier().verify(path)

    assert (
        result.status
        is SemanticVerificationStatus.INVALID
    )

    assert any(
        issue.code == "blocked_action_executed"
        for issue in result.issues
    )

    assert any(
        issue.code == "block_without_non_execution"
        for issue in result.issues
    )


def test_valid_block_records_explicit_non_execution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "block.jsonl"
    recorder = EvidenceRecorder(path, "run-block")

    start_run(recorder)
    add_proposal(recorder)

    recorder.append(
        "governance_decision",
        {
            "decision": {
                "decision_id": "decision-001",
                "disposition": "block",
                "preservation": "incident",
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=2),
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
        timestamp=BASE_TIME + timedelta(seconds=3),
    )

    complete_run(
        recorder,
        executed=False,
    )

    result = EvidenceSemanticsVerifier().verify(path)

    assert (
        result.status
        is SemanticVerificationStatus.VALID
    )
    assert result.issues == []


def test_post_hoc_block_after_execution_is_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "post-hoc.jsonl"
    recorder = EvidenceRecorder(
        path,
        "run-post-hoc",
    )

    start_run(recorder)
    add_proposal(recorder)

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

    recorder.append(
        "post_hoc_governance_decision",
        {
            "decision": {
                "decision_id": "decision-001",
                "disposition": "block",
                "preservation": "incident",
            },
            "execution_already_occurred": True,
        },
        timestamp=BASE_TIME + timedelta(seconds=4),
    )

    complete_run(
        recorder,
        executed=True,
    )

    result = EvidenceSemanticsVerifier().verify(path)

    assert (
        result.status
        is SemanticVerificationStatus.VALID
    )
    assert result.issues == []


def test_valid_escalation_requires_approval_event(
    tmp_path: Path,
) -> None:
    path = tmp_path / "escalate.jsonl"
    recorder = EvidenceRecorder(
        path,
        "run-escalate",
    )

    start_run(recorder)
    add_proposal(recorder)

    recorder.append(
        "governance_decision",
        {
            "decision": {
                "decision_id": "decision-001",
                "disposition": "escalate",
                "preservation": "full",
            }
        },
        timestamp=BASE_TIME + timedelta(seconds=2),
    )

    recorder.append(
        "approval_required",
        {
            "proposal_id": "proposal-001",
            "decision_id": "decision-001",
        },
        timestamp=BASE_TIME + timedelta(seconds=3),
    )

    complete_run(
        recorder,
        executed=False,
    )

    result = EvidenceSemanticsVerifier().verify(path)

    assert (
        result.status
        is SemanticVerificationStatus.VALID
    )
    assert result.issues == []
