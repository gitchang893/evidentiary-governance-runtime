from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eg_runtime.generation_failures import (
    ProposalGenerationEmptyResponseError,
    ProposalGenerationJsonParseError,
    ProposalGenerationSchemaError,
    ProposalGenerationTransportError,
    ProposalGenerationUnknownSourceError,
    ProposalGenerationUnknownToolError,
)
from eg_runtime.ollama_adapter import (
    OllamaProposalGenerator,
    OllamaProposalGeneratorConfig,
)
from eg_runtime.proposal_generation import (
    build_proposal_generation_request,
)
from eg_runtime.scenarios import (
    load_scenario,
)


SCENARIO_PATH = Path(
    "scenarios/canonical/"
    "prompt_injection_exfiltration_unsafe.yaml"
)


class FakeOllamaClient:
    def __init__(
        self,
        *,
        response: Any = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[
            dict[str, Any]
        ] = []

    def chat(
        self,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(kwargs)

        if self._error is not None:
            raise self._error

        return self._response


def _scenario():
    return load_scenario(
        SCENARIO_PATH
    )


def _request(
    repetition_index: int = 0,
):
    return build_proposal_generation_request(
        _scenario(),
        experiment_id="ollama-adapter-test",
        repetition_index=(
            repetition_index
        ),
    )


def _valid_content(
    *,
    proposal=None,
) -> str:
    scenario = _scenario()

    selected_proposal = (
        scenario.proposal
        if proposal is None
        else proposal
    )

    return json.dumps(
        {
            "declared_plan": [
                "Inspect the visible sources.",
                "Construct one action proposal.",
            ],
            "proposal": (
                selected_proposal
                .model_dump(mode="json")
            ),
        },
        ensure_ascii=False,
    )


def _generator(
    client: FakeOllamaClient,
) -> OllamaProposalGenerator:
    return OllamaProposalGenerator(
        OllamaProposalGeneratorConfig(
            model="test-model",
            temperature=0.0,
            seed=17,
        ),
        client=client,
    )


def test_adapter_uses_structured_output_schema(
) -> None:
    client = FakeOllamaClient(
        response={
            "message": {
                "content": _valid_content(),
            },
        }
    )

    result = _generator(
        client
    ).generate_request(
        _request()
    )

    assert len(client.calls) == 1

    call = client.calls[0]

    assert call["model"] == "test-model"
    assert call["stream"] is False

    assert call["options"] == {
        "temperature": 0.0,
        "seed": 17,
    }

    schema = call["format"]

    assert schema["type"] == "object"

    assert set(
        schema["required"]
    ) >= {
        "declared_plan",
        "proposal",
    }

    messages = call["messages"]

    assert len(messages) == 2

    serialized_messages = json.dumps(
        messages,
        ensure_ascii=False,
    )

    assert (
        "expected_disposition"
        not in serialized_messages
    )

    assert (
        "trusted_instruction_source_ids"
        not in serialized_messages
    )

    assert result.generator_name == (
        "ollama:test-model"
    )

    assert result.proposal == (
        _scenario().proposal
    )


def test_generation_identity_is_trial_specific(
) -> None:
    client = FakeOllamaClient(
        response={
            "message": {
                "content": _valid_content(),
            },
        }
    )

    generator = _generator(client)

    first = generator.generate_request(
        _request(0)
    )

    repeated = generator.generate_request(
        _request(0)
    )

    next_repetition = (
        generator.generate_request(
            _request(1)
        )
    )

    assert (
        first.generation_id
        == repeated.generation_id
    )

    assert (
        first.generation_id
        != next_repetition.generation_id
    )


def test_transport_error_is_classified(
) -> None:
    generator = _generator(
        FakeOllamaClient(
            error=ConnectionError(
                "connection refused"
            )
        )
    )

    with pytest.raises(
        ProposalGenerationTransportError,
    ):
        generator.generate_request(
            _request()
        )


def test_empty_response_is_classified(
) -> None:
    generator = _generator(
        FakeOllamaClient(
            response={
                "message": {
                    "content": "   ",
                },
            }
        )
    )

    with pytest.raises(
        ProposalGenerationEmptyResponseError,
    ):
        generator.generate_request(
            _request()
        )


def test_invalid_json_is_classified(
) -> None:
    generator = _generator(
        FakeOllamaClient(
            response={
                "message": {
                    "content": "{invalid",
                },
            }
        )
    )

    with pytest.raises(
        ProposalGenerationJsonParseError,
    ):
        generator.generate_request(
            _request()
        )


def test_schema_failure_is_classified(
) -> None:
    generator = _generator(
        FakeOllamaClient(
            response={
                "message": {
                    "content": json.dumps(
                        {
                            "declared_plan": [],
                            "proposal": {},
                        }
                    ),
                },
            }
        )
    )

    with pytest.raises(
        ProposalGenerationSchemaError,
    ):
        generator.generate_request(
            _request()
        )


def test_unknown_tool_is_rejected(
) -> None:
    scenario = _scenario()

    proposal = (
        scenario.proposal.model_copy(
            update={
                "tool": "invented_tool",
            },
            deep=True,
        )
    )

    generator = _generator(
        FakeOllamaClient(
            response={
                "message": {
                    "content": (
                        _valid_content(
                            proposal=proposal
                        )
                    ),
                },
            }
        )
    )

    with pytest.raises(
        ProposalGenerationUnknownToolError,
    ):
        generator.generate_request(
            _request()
        )


def test_unknown_instruction_source_is_rejected(
) -> None:
    scenario = _scenario()

    proposal = (
        scenario.proposal.model_copy(
            update={
                "instruction_source_ids": [
                    "invented-source",
                ],
            },
            deep=True,
        )
    )

    generator = _generator(
        FakeOllamaClient(
            response={
                "message": {
                    "content": (
                        _valid_content(
                            proposal=proposal
                        )
                    ),
                },
            }
        )
    )

    with pytest.raises(
        ProposalGenerationUnknownSourceError,
    ):
        generator.generate_request(
            _request()
        )
