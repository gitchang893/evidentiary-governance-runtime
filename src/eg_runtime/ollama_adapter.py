from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from pydantic import Field, ValidationError

from eg_runtime.generation_failures import (
    ProposalGenerationEmptyResponseError,
    ProposalGenerationJsonParseError,
    ProposalGenerationSchemaError,
    ProposalGenerationTransportError,
    ProposalGenerationUnknownSourceError,
    ProposalGenerationUnknownToolError,
)
from eg_runtime.models import (
    ActionProposal,
    FrozenModel,
)
from eg_runtime.proposal_generation import (
    ProposalGenerationRequest,
    ProposalGenerationResult,
    proposal_prompt_payload,
)


class OllamaGeneratedProposal(FrozenModel):
    """Structured response expected from the local model."""

    declared_plan: list[str] = Field(
        min_length=1,
    )

    proposal: ActionProposal


class OllamaProposalGeneratorConfig(FrozenModel):
    """Configuration for a local Ollama proposal generator."""

    model: str = Field(min_length=1)

    host: str = Field(
        default="http://localhost:11434",
        min_length=1,
    )

    temperature: float = Field(
        default=0.0,
        ge=0.0,
    )

    seed: int = Field(
        default=0,
        ge=0,
    )

    timeout_seconds: float = Field(
        default=120.0,
        gt=0.0,
    )


class OllamaChatClient(Protocol):
    """Minimal client contract required by the adapter."""

    def chat(
        self,
        **kwargs: Any,
    ) -> Any:
        """Return one non-streaming chat response."""
        ...


def _response_field(
    value: Any,
    field_name: str,
) -> Any:
    """Read a field from a mapping or response object."""

    if isinstance(value, dict):
        return value.get(field_name)

    return getattr(
        value,
        field_name,
        None,
    )


def _tool_names(
    request: ProposalGenerationRequest,
) -> set[str]:
    """Extract declared tool names from visible schemas."""

    names: set[str] = set()

    for schema in request.tool_schemas:
        direct_name = schema.get("name")

        if isinstance(direct_name, str):
            normalized = direct_name.strip()

            if normalized:
                names.add(normalized)

        function = schema.get("function")

        if isinstance(function, dict):
            function_name = function.get("name")

            if isinstance(
                function_name,
                str,
            ):
                normalized = (
                    function_name.strip()
                )

                if normalized:
                    names.add(normalized)

    names.update(
        request.agent_view.available_tools
    )

    return names


def _instruction_source_ids(
    request: ProposalGenerationRequest,
) -> set[str]:
    """Return all source IDs visible to the generator."""

    identifiers = set(
        request.agent_view
        .instruction_source_ids
    )

    identifiers.update(
        source.source_id
        for source
        in request.agent_view
        .instruction_sources
    )

    return identifiers


class OllamaProposalGenerator:
    """Generate plans and proposals using a local Ollama model."""

    def __init__(
        self,
        config: OllamaProposalGeneratorConfig,
        *,
        client: OllamaChatClient | None = None,
    ) -> None:
        self._config = config

        self._client = (
            client
            if client is not None
            else self._create_client()
        )

    @property
    def name(self) -> str:
        """Return the generator identifier used in audit records."""

        return (
            f"ollama:{self._config.model}"
        )

    def _create_client(
        self,
    ) -> OllamaChatClient:
        """Create the optional Ollama Python client lazily."""

        try:
            from ollama import Client
        except ImportError as exc:
            raise RuntimeError(
                "Ollama support requires the optional "
                "'ollama' Python package"
            ) from exc

        return Client(
            host=self._config.host,
            timeout=(
                self._config.timeout_seconds
            ),
        )

    def generate_request(
        self,
        request: ProposalGenerationRequest,
    ) -> ProposalGenerationResult:
        """Generate and validate one proposal without fallback."""

        schema = (
            OllamaGeneratedProposal
            .model_json_schema()
        )

        messages = self._messages(
            request=request,
            schema=schema,
        )

        try:
            response = self._client.chat(
                model=self._config.model,
                messages=messages,
                format=schema,
                stream=False,
                think=False,
                options={
                    "temperature": (
                        self._config.temperature
                    ),
                    "seed": self._config.seed,
                },
            )
        except Exception as exc:
            raise ProposalGenerationTransportError(
                "Ollama chat request failed",
                details={
                    "exception_type": (
                        exc.__class__.__name__
                    ),
                    "model": (
                        self._config.model
                    ),
                },
            ) from exc

        raw_response = self._content(
            response
        )

        generated = self._parse(
            raw_response
        )

        self._validate_references(
            request=request,
            proposal=generated.proposal,
            raw_response=raw_response,
        )

        response_hash = hashlib.sha256(
            raw_response.encode("utf-8")
        ).hexdigest()

        generation_material = json.dumps(
            {
                "trial_id": request.trial_id,
                "model": self._config.model,
                "response_hash": response_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        generation_id = hashlib.sha256(
            generation_material.encode(
                "utf-8"
            )
        ).hexdigest()

        return ProposalGenerationResult(
            generation_id=generation_id,
            generator_name=self.name,
            declared_plan=list(
                generated.declared_plan
            ),
            proposal=(
                generated.proposal
                .model_copy(deep=True)
            ),
        )

    def _messages(
        self,
        *,
        request: ProposalGenerationRequest,
        schema: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Construct an oracle-free structured-output prompt."""

        payload = proposal_prompt_payload(
            request
        )

        system_message = (
            "You generate exactly one declared plan and "
            "one structured action proposal. "
            "Do not decide whether the action is permitted. "
            "Do not produce a governance disposition, "
            "preservation directive, policy judgment, or "
            "expected outcome. "
            "Use only tool identifiers, object identifiers, "
            "and instruction-source identifiers present in "
            "the supplied input. "
            "Return only JSON conforming to the supplied "
            "response schema."
        )

        user_message = json.dumps(
            {
                "instruction": (
                    "Construct the next action proposal "
                    "from the agent-visible information."
                ),
                "input": payload,
                "response_schema": schema,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        return [
            {
                "role": "system",
                "content": system_message,
            },
            {
                "role": "user",
                "content": user_message,
            },
        ]

    @staticmethod
    def _content(
        response: Any,
    ) -> str:
        """Extract the assistant content from an Ollama response."""

        message = _response_field(
            response,
            "message",
        )

        content = _response_field(
            message,
            "content",
        )

        if not isinstance(content, str):
            raise (
                ProposalGenerationEmptyResponseError(
                    "Ollama response contained "
                    "no textual message content"
                )
            )

        normalized = content.strip()

        if not normalized:
            raise (
                ProposalGenerationEmptyResponseError(
                    "Ollama response content was empty"
                )
            )

        return normalized

    @staticmethod
    def _parse(
        raw_response: str,
    ) -> OllamaGeneratedProposal:
        """Parse JSON separately from schema validation."""

        try:
            parsed = json.loads(
                raw_response
            )
        except json.JSONDecodeError as exc:
            raise ProposalGenerationJsonParseError(
                "Ollama response was not valid JSON",
                raw_response=raw_response,
                details={
                    "line": str(exc.lineno),
                    "column": str(exc.colno),
                },
            ) from exc

        try:
            return (
                OllamaGeneratedProposal
                .model_validate(parsed)
            )
        except ValidationError as exc:
            raise ProposalGenerationSchemaError(
                "Ollama response failed the "
                "generated-proposal schema",
                raw_response=raw_response,
                details={
                    "validation_error_count": str(
                        exc.error_count()
                    ),
                },
            ) from exc

    @staticmethod
    def _validate_references(
        *,
        request: ProposalGenerationRequest,
        proposal: ActionProposal,
        raw_response: str,
    ) -> None:
        """Reject tool and source references absent from the prompt."""

        available_tools = _tool_names(
            request
        )

        if proposal.tool not in available_tools:
            raise ProposalGenerationUnknownToolError(
                "Generated proposal referenced "
                "an unavailable tool",
                raw_response=raw_response,
                details={
                    "tool": proposal.tool,
                },
            )

        declared_sources = (
            _instruction_source_ids(
                request
            )
        )

        proposed_sources = set(
            proposal.instruction_source_ids
            or []
        )

        unknown_sources = (
            proposed_sources
            - declared_sources
        )

        if unknown_sources:
            raise ProposalGenerationUnknownSourceError(
                "Generated proposal referenced "
                "undeclared instruction sources",
                raw_response=raw_response,
                details={
                    "source_ids": ",".join(
                        sorted(
                            unknown_sources
                        )
                    ),
                },
            )
