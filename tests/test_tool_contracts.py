from eg_runtime.tool_contracts import (
    registered_tool_names,
    tool_argument_schema,
    validate_tool_arguments,
)


def test_send_message_contract_is_registered() -> None:
    assert "send_message" in registered_tool_names()

    schema = tool_argument_schema(
        "send_message"
    )

    assert schema is not None

    assert {
        "recipient",
        "subject",
        "body",
    }.issubset(
        schema["properties"]
    )


def test_send_message_contract_rejects_missing_arguments() -> None:
    errors = validate_tool_arguments(
        "send_message",
        {},
    )

    assert errors


def test_modify_record_contract_accepts_updates_mapping() -> None:
    errors = validate_tool_arguments(
        "modify_record",
        {
            "record_id": "R-17",
            "updates": {
                "status": "reviewed",
            },
        },
    )

    assert errors == []
