from typing import Any

import pytest

from eg_runtime.tools import (
    ModifyRecordTool,
    ReadDocumentTool,
    RequestHumanApprovalTool,
    SendMessageTool,
    ToolArgumentError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
    WriteReportTool,
)


def make_registry() -> tuple[
    ToolRegistry,
    dict[str, str],
    dict[str, str],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    """Create a fresh sandbox and capability registry for each test."""

    documents = {
        "D-01": "Internal policy text",
    }

    reports: dict[str, str] = {}

    messages: list[dict[str, Any]] = []

    records = {
        "R-17": {
            "status": "pending",
            "owner": "records-team",
        }
    }

    approval_requests: list[dict[str, Any]] = []

    registry = ToolRegistry(
        [
            ReadDocumentTool(documents),
            WriteReportTool(reports),
            SendMessageTool(messages),
            ModifyRecordTool(records),
            RequestHumanApprovalTool(approval_requests),
        ]
    )

    return (
        registry,
        documents,
        reports,
        messages,
        records,
        approval_requests,
    )


def test_registry_exposes_registered_tools() -> None:
    registry, *_ = make_registry()

    assert registry.available_tools() == (
        "modify_record",
        "read_document",
        "request_human_approval",
        "send_message",
        "write_report",
    )


def test_read_document_has_no_side_effect() -> None:
    registry, *_ = make_registry()

    outcome = registry.execute(
        "read_document",
        {
            "document_id": "D-01",
        },
    )

    assert outcome.side_effect is False
    assert outcome.output["document_id"] == "D-01"
    assert outcome.output["content"] == "Internal policy text"
    assert registry.call_count("read_document") == 1


def test_missing_document_is_rejected() -> None:
    registry, *_ = make_registry()

    with pytest.raises(ToolExecutionError):
        registry.execute(
            "read_document",
            {
                "document_id": "D-99",
            },
        )

    assert registry.call_count("read_document") == 1


def test_write_report_changes_sandbox_state() -> None:
    registry, _, reports, _, _, _ = make_registry()

    outcome = registry.execute(
        "write_report",
        {
            "report_id": "REPORT-01",
            "content": "Governance evaluation summary",
        },
    )

    assert outcome.side_effect is True
    assert outcome.output["written"] is True
    assert reports["REPORT-01"] == "Governance evaluation summary"
    assert registry.call_count("write_report") == 1


def test_modify_record_changes_sandbox_state() -> None:
    registry, _, _, _, records, _ = make_registry()

    outcome = registry.execute(
        "modify_record",
        {
            "record_id": "R-17",
            "updates": {
                "status": "reviewed",
            },
        },
    )

    assert outcome.side_effect is True
    assert records["R-17"]["status"] == "reviewed"
    assert outcome.output["updated_fields"] == ["status"]
    assert registry.call_count("modify_record") == 1


def test_send_message_records_external_action() -> None:
    registry, _, _, messages, _, _ = make_registry()

    outcome = registry.execute(
        "send_message",
        {
            "recipient": "reviewer@example.org",
            "subject": "Review request",
            "body": "Please review the report.",
            "data_classification": "internal",
        },
    )

    assert outcome.side_effect is True
    assert len(messages) == 1
    assert messages[0]["recipient"] == "reviewer@example.org"
    assert messages[0]["data_classification"] == "internal"
    assert registry.call_count("send_message") == 1


def test_request_human_approval_creates_pending_request() -> None:
    registry, _, _, _, _, approval_requests = make_registry()

    outcome = registry.execute(
        "request_human_approval",
        {
            "request_id": "REQ-001",
            "subject": "agent-01",
            "action": "modify_record",
            "object_id": "R-17",
            "constraints": {
                "status": "reviewed",
            },
        },
    )

    assert outcome.side_effect is True
    assert outcome.output["status"] == "pending"
    assert approval_requests[0]["object_id"] == "R-17"
    assert approval_requests[0]["constraints"]["status"] == "reviewed"


def test_empty_update_arguments_are_rejected() -> None:
    registry, *_ = make_registry()

    with pytest.raises(ToolArgumentError):
        registry.execute(
            "modify_record",
            {
                "record_id": "R-17",
                "updates": {},
            },
        )

    assert registry.call_count("modify_record") == 1


def test_unknown_tool_is_rejected_without_incrementing_counts() -> None:
    registry, *_ = make_registry()

    with pytest.raises(ToolNotFoundError):
        registry.execute(
            "delete_database",
            {},
        )

    assert registry.call_count("delete_database") == 0
