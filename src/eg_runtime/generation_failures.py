from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import Field

from eg_runtime.models import FrozenModel
from eg_runtime.proposal_generation import (
    ProposalGenerationRequest,
)


class GenerationFailureKind(StrEnum):
    """Failure classes arising before governance evaluation."""

    TRANSPORT_ERROR = "transport_error"
    EMPTY_RESPONSE = "empty_response"
    JSON_PARSE_ERROR = "json_parse_error"
    SCHEMA_VALIDATION_ERROR = (
        "schema_validation_error"
    )
    UNKNOWN_TOOL_REFERENCE = (
        "unknown_tool_reference"
    )
    UNKNOWN_OBJECT_REFERENCE = (
        "unknown_object_reference"
    )
    UNKNOWN_SOURCE_REFERENCE = (
        "unknown_source_reference"
    )


class GenerationFailureRecord(FrozenModel):
    """Auditable record of a failed proposal-generation attempt."""

    failure_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    trial_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    repetition_index: int = Field(ge=0)

    generator_name: str = Field(
        min_length=1,
    )

    kind: GenerationFailureKind

    message: str = Field(
        min_length=1,
    )

    retryable: bool

    raw_response_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    details: dict[str, str] = Field(
        default_factory=dict,
    )


class ProposalGenerationError(Exception):
    """Base exception for failures before governance evaluation."""

    kind = GenerationFailureKind.TRANSPORT_ERROR
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        normalized_message = message.strip()

        if not normalized_message:
            raise ValueError(
                "generation failure message "
                "must not be empty"
            )

        super().__init__(
            normalized_message
        )

        self.message = normalized_message
        self.raw_response = raw_response

        self.details = {
            str(key): str(value)
            for key, value in (
                details or {}
            ).items()
        }

    def to_record(
        self,
        *,
        request: ProposalGenerationRequest,
        generator_name: str,
    ) -> GenerationFailureRecord:
        """Convert this exception into an immutable audit record."""

        normalized_generator_name = (
            generator_name.strip()
        )

        if not normalized_generator_name:
            raise ValueError(
                "generator_name must not be empty"
            )

        raw_response_hash = (
            hashlib.sha256(
                self.raw_response.encode(
                    "utf-8"
                )
            ).hexdigest()
            if self.raw_response is not None
            else None
        )

        identity_payload = json.dumps(
            {
                "trial_id": request.trial_id,
                "repetition_index": (
                    request.repetition_index
                ),
                "generator_name": (
                    normalized_generator_name
                ),
                "kind": self.kind.value,
                "message": self.message,
                "retryable": self.retryable,
                "raw_response_hash": (
                    raw_response_hash
                ),
                "details": self.details,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        failure_id = hashlib.sha256(
            identity_payload.encode(
                "utf-8"
            )
        ).hexdigest()

        return GenerationFailureRecord(
            failure_id=failure_id,
            trial_id=request.trial_id,
            repetition_index=(
                request.repetition_index
            ),
            generator_name=(
                normalized_generator_name
            ),
            kind=self.kind,
            message=self.message,
            retryable=self.retryable,
            raw_response_hash=(
                raw_response_hash
            ),
            details=dict(self.details),
        )


class ProposalGenerationTransportError(
    ProposalGenerationError
):
    """The model service could not be reached or completed."""

    kind = GenerationFailureKind.TRANSPORT_ERROR
    retryable = True


class ProposalGenerationEmptyResponseError(
    ProposalGenerationError
):
    """The model service returned no usable content."""

    kind = GenerationFailureKind.EMPTY_RESPONSE
    retryable = True


class ProposalGenerationJsonParseError(
    ProposalGenerationError
):
    """The returned content was not valid JSON."""

    kind = GenerationFailureKind.JSON_PARSE_ERROR
    retryable = False


class ProposalGenerationSchemaError(
    ProposalGenerationError
):
    """The parsed response failed the proposal schema."""

    kind = (
        GenerationFailureKind
        .SCHEMA_VALIDATION_ERROR
    )
    retryable = False


class ProposalGenerationUnknownToolError(
    ProposalGenerationError
):
    """The proposal referenced a tool absent from the request."""

    kind = (
        GenerationFailureKind
        .UNKNOWN_TOOL_REFERENCE
    )
    retryable = False


class ProposalGenerationUnknownObjectError(
    ProposalGenerationError
):
    """The proposal referenced an unavailable object."""

    kind = (
        GenerationFailureKind
        .UNKNOWN_OBJECT_REFERENCE
    )
    retryable = False


class ProposalGenerationUnknownSourceError(
    ProposalGenerationError
):
    """The proposal referenced an undeclared instruction source."""

    kind = (
        GenerationFailureKind
        .UNKNOWN_SOURCE_REFERENCE
    )
    retryable = False
