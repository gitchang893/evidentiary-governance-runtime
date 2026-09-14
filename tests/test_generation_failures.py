from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from eg_runtime.generation_failures import (
    GenerationFailureKind,
    ProposalGenerationEmptyResponseError,
    ProposalGenerationJsonParseError,
    ProposalGenerationSchemaError,
    ProposalGenerationTransportError,
    ProposalGenerationUnknownObjectError,
    ProposalGenerationUnknownSourceError,
    ProposalGenerationUnknownToolError,
)
from eg_runtime.proposal_generation import (
    build_proposal_generation_request,
)
from eg_runtime.scenarios import load_scenario


SCENARIO_PATH = Path(
    "scenarios/canonical/"
    "prompt_injection_exfiltration_unsafe.yaml"
)


def _request(
    repetition_index: int = 0,
):
    scenario = load_scenario(
        SCENARIO_PATH
    )

    return build_proposal_generation_request(
        scenario,
        experiment_id=(
            "generation-failure-test"
        ),
        repetition_index=(
            repetition_index
        ),
    )


def test_generation_failure_kinds_are_stable(
) -> None:
    assert {
        item.value
        for item in GenerationFailureKind
    } == {
        "transport_error",
        "empty_response",
        "json_parse_error",
        "schema_validation_error",
        "unknown_tool_reference",
        "unknown_object_reference",
        "unknown_source_reference",
    }


@pytest.mark.parametrize(
    (
        "error",
        "expected_kind",
        "expected_retryable",
    ),
    [
        (
            ProposalGenerationTransportError(
                "Ollama connection failed"
            ),
            GenerationFailureKind.TRANSPORT_ERROR,
            True,
        ),
        (
            ProposalGenerationEmptyResponseError(
                "The model returned no content"
            ),
            GenerationFailureKind.EMPTY_RESPONSE,
            True,
        ),
        (
            ProposalGenerationJsonParseError(
                "The response was not valid JSON"
            ),
            GenerationFailureKind.JSON_PARSE_ERROR,
            False,
        ),
        (
            ProposalGenerationSchemaError(
                "The proposal failed validation"
            ),
            (
                GenerationFailureKind
                .SCHEMA_VALIDATION_ERROR
            ),
            False,
        ),
        (
            ProposalGenerationUnknownToolError(
                "Unknown tool reference"
            ),
            (
                GenerationFailureKind
                .UNKNOWN_TOOL_REFERENCE
            ),
            False,
        ),
        (
            ProposalGenerationUnknownObjectError(
                "Unknown object reference"
            ),
            (
                GenerationFailureKind
                .UNKNOWN_OBJECT_REFERENCE
            ),
            False,
        ),
        (
            ProposalGenerationUnknownSourceError(
                "Unknown source reference"
            ),
            (
                GenerationFailureKind
                .UNKNOWN_SOURCE_REFERENCE
            ),
            False,
        ),
    ],
)
def test_failure_subclasses_map_to_kind_and_retryability(
    error,
    expected_kind: GenerationFailureKind,
    expected_retryable: bool,
) -> None:
    record = error.to_record(
        request=_request(),
        generator_name="test-generator",
    )

    assert record.kind is expected_kind

    assert (
        record.retryable
        is expected_retryable
    )


def test_failure_record_hashes_raw_response(
) -> None:
    raw_response = (
        '{"declared_plan": [], '
        '"proposal": "invalid"}'
    )

    error = ProposalGenerationSchemaError(
        "Schema validation failed",
        raw_response=raw_response,
        details={
            "field": "declared_plan",
            "reason": "too_short",
        },
    )

    record = error.to_record(
        request=_request(),
        generator_name="ollama-local",
    )

    assert record.raw_response_hash == (
        hashlib.sha256(
            raw_response.encode("utf-8")
        ).hexdigest()
    )

    assert record.details == {
        "field": "declared_plan",
        "reason": "too_short",
    }


def test_failure_identity_is_deterministic_and_trial_specific(
) -> None:
    error = ProposalGenerationUnknownSourceError(
        "Unknown instruction source",
        details={
            "source_id": "invented-source",
        },
    )

    first = error.to_record(
        request=_request(0),
        generator_name="test-generator",
    )

    repeated = error.to_record(
        request=_request(0),
        generator_name="test-generator",
    )

    next_repetition = error.to_record(
        request=_request(1),
        generator_name="test-generator",
    )

    assert (
        first.failure_id
        == repeated.failure_id
    )

    assert (
        first.failure_id
        != next_repetition.failure_id
    )


def test_failure_record_contains_no_governance_outcome(
) -> None:
    record = (
        ProposalGenerationSchemaError(
            "Schema validation failed"
        ).to_record(
            request=_request(),
            generator_name="test-generator",
        )
    )

    dumped = record.model_dump(
        mode="json"
    )

    assert set(dumped) == {
        "failure_id",
        "trial_id",
        "repetition_index",
        "generator_name",
        "kind",
        "message",
        "retryable",
        "raw_response_hash",
        "details",
    }

    assert "condition" not in dumped
    assert "decision_phase" not in dumped
    assert "disposition" not in dumped
    assert "containment_success" not in dumped
    assert "unsafe_execution" not in dumped


def test_empty_failure_message_is_rejected(
) -> None:
    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        ProposalGenerationTransportError(
            "   "
        )
