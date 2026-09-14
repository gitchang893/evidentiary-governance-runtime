from __future__ import annotations

from typing import Any, Iterable, Protocol, TypeVar

from pydantic import Field, ValidationError

from eg_runtime.models import FrozenModel


class ToolError(RuntimeError):
    """Base exception for sandboxed-tool failures."""


class ToolNotFoundError(ToolError):
    """Raised when a requested tool is absent from the registry."""


class ToolArgumentError(ToolError):
    """Raised when tool arguments fail schema validation."""


class ToolExecutionError(ToolError):
    """Raised when a valid tool request cannot be completed."""


class ToolOutcome(FrozenModel):
    """Typed result returned by a sandboxed tool adapter."""

    output: dict[str, Any] = Field(default_factory=dict)
    side_effect: bool
    status: str = "ok"


class ToolAdapter(Protocol):
    """Common interface implemented by every sandboxed tool."""

    name: str

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        """Validate arguments and execute the sandboxed operation."""
        ...


ArgumentModel = TypeVar("ArgumentModel", bound=FrozenModel)


def _validate_arguments(
    schema: type[ArgumentModel],
    arguments: dict[str, Any],
) -> ArgumentModel:
    """Validate raw tool arguments through a Pydantic schema."""

    try:
        return schema.model_validate(arguments)
    except ValidationError as exc:
        raise ToolArgumentError(str(exc)) from exc


class ReadDocumentArgs(FrozenModel):
    """Arguments accepted by read_document."""

    document_id: str = Field(min_length=1)


class ReadDocumentsArgs(FrozenModel):
    """Arguments accepted by read_documents."""

    document_ids: list[str] = Field(min_length=1)


class WriteReportArgs(FrozenModel):
    """Arguments accepted by write_report."""

    report_id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class SendMessageArgs(FrozenModel):
    """Arguments accepted by send_message."""

    recipient: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    data_classification: str = Field(default="public", min_length=1)


class ModifyRecordArgs(FrozenModel):
    """Arguments accepted by modify_record."""

    record_id: str = Field(min_length=1)
    updates: dict[str, Any] = Field(min_length=1)


class RequestHumanApprovalArgs(FrozenModel):
    """Arguments accepted by request_human_approval."""

    request_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    action: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)


class ReadDocumentTool:
    """Read a document from an in-memory sandbox."""

    name = "read_document"

    def __init__(self, documents: dict[str, str]) -> None:
        self._documents = documents

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = _validate_arguments(ReadDocumentArgs, arguments)

        if parsed.document_id not in self._documents:
            raise ToolExecutionError(
                f"document does not exist: {parsed.document_id}"
            )

        return ToolOutcome(
            output={
                "document_id": parsed.document_id,
                "content": self._documents[parsed.document_id],
            },
            side_effect=False,
        )


class ReadDocumentsTool:
    """Read multiple documents from an in-memory sandbox."""

    name = "read_documents"

    def __init__(self, documents: dict[str, str]) -> None:
        self._documents = documents

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = _validate_arguments(
            ReadDocumentsArgs,
            arguments,
        )

        if len(parsed.document_ids) != len(
            set(parsed.document_ids)
        ):
            raise ToolArgumentError(
                "document_ids must be unique"
            )

        missing_document_ids = [
            document_id
            for document_id in parsed.document_ids
            if document_id not in self._documents
        ]

        if missing_document_ids:
            raise ToolExecutionError(
                "document does not exist: "
                f"{missing_document_ids[0]}"
            )

        documents = [
            {
                "document_id": document_id,
                "content": self._documents[document_id],
            }
            for document_id in parsed.document_ids
        ]

        return ToolOutcome(
            output={
                "document_count": len(documents),
                "documents": documents,
            },
            side_effect=False,
        )


class WriteReportTool:
    """Write a report into an in-memory sandbox."""

    name = "write_report"

    def __init__(self, reports: dict[str, str]) -> None:
        self._reports = reports

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = _validate_arguments(WriteReportArgs, arguments)

        self._reports[parsed.report_id] = parsed.content

        return ToolOutcome(
            output={
                "report_id": parsed.report_id,
                "written": True,
            },
            side_effect=True,
        )


class SendMessageTool:
    """Append an outbound message to an in-memory message store."""

    name = "send_message"

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = _validate_arguments(SendMessageArgs, arguments)

        message = {
            "recipient": parsed.recipient,
            "subject": parsed.subject,
            "body": parsed.body,
            "data_classification": parsed.data_classification,
        }

        self._messages.append(message)

        return ToolOutcome(
            output={
                "message_index": len(self._messages) - 1,
                "recipient": parsed.recipient,
                "sent": True,
            },
            side_effect=True,
        )


class ModifyRecordTool:
    """Modify a record within an in-memory sandbox."""

    name = "modify_record"

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self._records = records

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = _validate_arguments(ModifyRecordArgs, arguments)

        if parsed.record_id not in self._records:
            raise ToolExecutionError(
                f"record does not exist: {parsed.record_id}"
            )

        self._records[parsed.record_id].update(parsed.updates)

        return ToolOutcome(
            output={
                "record_id": parsed.record_id,
                "updated_fields": sorted(parsed.updates),
                "record": dict(self._records[parsed.record_id]),
            },
            side_effect=True,
        )


class RequestHumanApprovalTool:
    """Create a pending human-approval request."""

    name = "request_human_approval"

    def __init__(self, requests: list[dict[str, Any]]) -> None:
        self._requests = requests

    def invoke(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = _validate_arguments(
            RequestHumanApprovalArgs,
            arguments,
        )

        request = {
            "request_id": parsed.request_id,
            "subject": parsed.subject,
            "action": parsed.action,
            "object_id": parsed.object_id,
            "constraints": dict(parsed.constraints),
            "status": "pending",
        }

        self._requests.append(request)

        return ToolOutcome(
            output={
                "request_id": parsed.request_id,
                "status": "pending",
            },
            side_effect=True,
        )


class ToolRegistry:
    """Private capability registry used by the governance middleware."""

    def __init__(
        self,
        tools: Iterable[ToolAdapter] = (),
    ) -> None:
        self._tools: dict[str, ToolAdapter] = {}
        self._call_counts: dict[str, int] = {}

        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolAdapter) -> None:
        """Register one tool under its stable name."""

        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")

        self._tools[tool.name] = tool
        self._call_counts[tool.name] = 0

    def available_tools(self) -> tuple[str, ...]:
        """Return registered tool names in deterministic order."""

        return tuple(sorted(self._tools))

    def call_count(self, tool_name: str) -> int:
        """Return the number of capability-invocation attempts."""

        return self._call_counts.get(tool_name, 0)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolOutcome:
        """Invoke a registered sandboxed capability."""

        tool = self._tools.get(tool_name)

        if tool is None:
            raise ToolNotFoundError(f"unknown tool: {tool_name}")

        self._call_counts[tool_name] += 1

        return tool.invoke(arguments)
