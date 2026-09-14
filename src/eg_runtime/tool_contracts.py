"""Shared tool argument contracts for proposal generation and execution."""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import BaseModel, ValidationError

from eg_runtime import tools as _tools


def _build_tool_argument_models() -> dict[str, type[BaseModel]]:
    """Discover Tool classes with matching Args models."""

    models: dict[str, type[BaseModel]] = {}

    for class_name, tool_class in vars(_tools).items():
        if not inspect.isclass(tool_class):
            continue

        if not class_name.endswith("Tool"):
            continue

        tool_name = getattr(
            tool_class,
            "name",
            None,
        )

        args_name = (
            class_name[:-4]
            + "Args"
        )

        args_model = getattr(
            _tools,
            args_name,
            None,
        )

        if (
            isinstance(tool_name, str)
            and inspect.isclass(args_model)
            and issubclass(args_model, BaseModel)
        ):
            models[tool_name] = args_model

    return models


TOOL_ARGUMENT_MODELS = _build_tool_argument_models()


def registered_tool_names() -> tuple[str, ...]:
    """Return tool names with executable argument contracts."""

    return tuple(
        sorted(TOOL_ARGUMENT_MODELS)
    )


def tool_argument_schema(
    tool_name: str,
) -> dict[str, Any] | None:
    """Return the JSON schema for one tool's arguments."""

    model = TOOL_ARGUMENT_MODELS.get(
        tool_name
    )

    if model is None:
        return None

    return model.model_json_schema()


def validate_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return JSON-safe validation errors for one tool proposal."""

    model = TOOL_ARGUMENT_MODELS.get(
        tool_name
    )

    if model is None:
        return [
            {
                "type": "missing_tool_contract",
                "loc": [],
                "msg": (
                    "no argument contract is registered "
                    f"for tool {tool_name}"
                ),
            }
        ]

    try:
        model.model_validate(arguments)
    except ValidationError as exc:
        return [
            {
                "type": str(
                    error.get(
                        "type",
                        "validation_error",
                    )
                ),
                "loc": [
                    str(part)
                    for part in error.get(
                        "loc",
                        (),
                    )
                ],
                "msg": str(
                    error.get(
                        "msg",
                        "validation failed",
                    )
                ),
            }
            for error in exc.errors(
                include_url=False
            )
        ]

    return []
