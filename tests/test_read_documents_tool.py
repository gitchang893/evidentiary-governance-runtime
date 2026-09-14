from __future__ import annotations

import pytest

from eg_runtime.tools import (
    ReadDocumentsTool,
    ToolArgumentError,
    ToolExecutionError,
)


def make_documents() -> dict[str, str]:
    return {
        "D-01": "First organizational document.",
        "D-02": "Second organizational document.",
        "D-03": "Third organizational document.",
    }


def test_reads_multiple_documents_in_requested_order() -> None:
    tool = ReadDocumentsTool(make_documents())

    outcome = tool.invoke(
        {
            "document_ids": [
                "D-02",
                "D-01",
            ],
        }
    )

    assert outcome.side_effect is False
    assert outcome.output == {
        "document_count": 2,
        "documents": [
            {
                "document_id": "D-02",
                "content": "Second organizational document.",
            },
            {
                "document_id": "D-01",
                "content": "First organizational document.",
            },
        ],
    }


def test_missing_document_rejects_entire_batch() -> None:
    tool = ReadDocumentsTool(make_documents())

    with pytest.raises(
        ToolExecutionError,
        match="document does not exist: D-99",
    ):
        tool.invoke(
            {
                "document_ids": [
                    "D-01",
                    "D-99",
                ],
            }
        )


def test_empty_document_list_is_rejected() -> None:
    tool = ReadDocumentsTool(make_documents())

    with pytest.raises(ToolArgumentError):
        tool.invoke(
            {
                "document_ids": [],
            }
        )


def test_duplicate_document_ids_are_rejected() -> None:
    tool = ReadDocumentsTool(make_documents())

    with pytest.raises(
        ToolArgumentError,
        match="document_ids must be unique",
    ):
        tool.invoke(
            {
                "document_ids": [
                    "D-01",
                    "D-01",
                ],
            }
        )
