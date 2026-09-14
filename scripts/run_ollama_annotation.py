# scripts/run_ollama_annotation.py

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

import requests


MODEL = "qwen3.5:9b-q4_K_M"

# Override with --url if Ollama is running on another host.
OLLAMA_URL = "http://localhost:11434/api/chat"

DEFAULT_INSTRUCTIONS = Path(
    "artifacts/e2-blind-validation/model_annotation_prompt_v1.md"
)

VALID_COMPLIANCE = {
    "COMPLIANT",
    "NONCOMPLIANT",
    "UNCERTAIN",
}

VALID_CONFIDENCE = {
    "HIGH",
    "MEDIUM",
    "LOW",
}


def extract_blind_ids(text: str) -> list[str]:
    """
    Extract blind IDs in the exact order in which cases appear.
    """
    ids = re.findall(
        r"(?m)^## Case \d+:\s+`?(BHV-[A-Fa-f0-9]+)`?\s*$",
        text,
    )

    if not ids:
        ids = re.findall(
            r"(?m)^## Case \d+:\s+`?(CLV-[A-Fa-f0-9]+)`?\s*$",
            text,
        )

    if not ids:
        raise RuntimeError(
            "No blind IDs found in the case file."
        )

    if len(ids) != len(set(ids)):
        raise RuntimeError(
            f"Duplicate blind IDs found: {ids}"
        )

    return ids


def build_schema(expected_ids: list[str]) -> dict[str, Any]:
    """
    JSON Schema supplied directly to Ollama's `format` field.
    """
    n = len(expected_ids)

    return {
        "type": "object",
        "properties": {
            "annotations": {
                "type": "array",
                "minItems": n,
                "maxItems": n,
                "items": {
                    "type": "object",
                    "properties": {
                        "blind_id": {
                            "type": "string",
                            "enum": expected_ids,
                        },
                        "compliance": {
                            "type": "string",
                            "enum": [
                                "COMPLIANT",
                                "NONCOMPLIANT",
                                "UNCERTAIN",
                            ],
                        },
                        "violated_norm_labels": {
                            "type": "string",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": [
                                "HIGH",
                                "MEDIUM",
                                "LOW",
                            ],
                        },
                        "brief_reason": {
                            "type": "string",
                        },
                    },
                    "required": [
                        "blind_id",
                        "compliance",
                        "violated_norm_labels",
                        "confidence",
                        "brief_reason",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": [
            "annotations",
        ],
        "additionalProperties": False,
    }


def validate_annotations(
    annotations: Any,
    expected_ids: list[str],
) -> list[dict[str, str]]:
    """
    Reject the whole run unless every expected case has exactly one
    structurally valid annotation in the original case order.
    """
    if not isinstance(annotations, list):
        raise RuntimeError(
            "'annotations' is not a list."
        )

    if len(annotations) != len(expected_ids):
        raise RuntimeError(
            f"Expected {len(expected_ids)} annotations, "
            f"got {len(annotations)}."
        )

    cleaned: list[dict[str, str]] = []

    for index, row in enumerate(annotations):
        if not isinstance(row, dict):
            raise RuntimeError(
                f"Annotation {index + 1} is not an object."
            )

        required = {
            "blind_id",
            "compliance",
            "violated_norm_labels",
            "confidence",
            "brief_reason",
        }

        missing = required - set(row)
        if missing:
            raise RuntimeError(
                f"Annotation {index + 1} missing fields: "
                f"{sorted(missing)}"
            )

        cleaned_row = {
            key: str(row.get(key) or "").strip()
            for key in required
        }

        blind_id = cleaned_row["blind_id"]
        compliance = cleaned_row["compliance"]
        violated = cleaned_row["violated_norm_labels"]
        confidence = cleaned_row["confidence"]
        reason = cleaned_row["brief_reason"]

        expected_id = expected_ids[index]

        if blind_id != expected_id:
            raise RuntimeError(
                f"Case-order mismatch at position {index + 1}: "
                f"expected {expected_id}, got {blind_id}"
            )

        if compliance not in VALID_COMPLIANCE:
            raise RuntimeError(
                f"{blind_id}: invalid compliance "
                f"{compliance!r}"
            )

        if confidence not in VALID_CONFIDENCE:
            raise RuntimeError(
                f"{blind_id}: invalid confidence "
                f"{confidence!r}"
            )

        if not reason:
            raise RuntimeError(
                f"{blind_id}: empty brief_reason."
            )

        if compliance == "NONCOMPLIANT" and not violated:
            raise RuntimeError(
                f"{blind_id}: NONCOMPLIANT without "
                "violated_norm_labels."
            )

        if compliance == "COMPLIANT" and violated:
            raise RuntimeError(
                f"{blind_id}: COMPLIANT but "
                f"violated_norm_labels={violated!r}"
            )

        cleaned.append(
            {
                "blind_id": blind_id,
                "compliance": compliance,
                "violated_norm_labels": violated,
                "confidence": confidence,
                "brief_reason": reason,
            }
        )

    actual_ids = [x["blind_id"] for x in cleaned]

    if len(actual_ids) != len(set(actual_ids)):
        raise RuntimeError(
            "Duplicate blind IDs in model output."
        )

    if set(actual_ids) != set(expected_ids):
        raise RuntimeError(
            "Output blind-ID set does not match input cases."
        )

    return cleaned


def write_csv(
    path: Path,
    annotations: list[dict[str, str]],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "blind_id",
                "compliance",
                "violated_norm_labels",
                "confidence",
                "brief_reason",
            ],
        )
        writer.writeheader()
        writer.writerows(annotations)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "cases",
        type=Path,
        help="Blind case Markdown file.",
    )

    parser.add_argument(
        "--instructions",
        type=Path,
        default=DEFAULT_INSTRUCTIONS,
        help="Annotation instruction Markdown file.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output CSV path.",
    )

    parser.add_argument(
        "--model",
        default=MODEL,
    )

    parser.add_argument(
        "--url",
        default=OLLAMA_URL,
    )

    parser.add_argument(
    "--num-ctx",
    type=int,
    default=32768,
    )

    parser.add_argument(
        "--num-predict",
        type=int,
        default=8192,
    )

    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--thinking",
        choices=["off", "on", "omit"],
    default="off",
        help=(
            "off: send think=false; "
            "on: send think=true; "
            "omit: do not send a think parameter."
        ),
    )

    args = parser.parse_args()

    if not args.instructions.exists():
        raise SystemExit(
            f"Missing instructions: {args.instructions}"
        )

    if not args.cases.exists():
        raise SystemExit(
            f"Missing cases: {args.cases}"
        )

    instructions = args.instructions.read_text(
        encoding="utf-8"
    )

    cases = args.cases.read_text(
        encoding="utf-8"
    )

    expected_ids = extract_blind_ids(cases)

    schema = build_schema(expected_ids)

    #
    # Important:
    # model_annotation_prompt_v1.md specifies CSV serialization.
    # For Ollama structured-output runs ONLY, the semantic annotation
    # rules are preserved but serialization is delegated to the API's
    # JSON Schema.
    #
    wrapper = """
IMPORTANT OUTPUT-TRANSPORT NOTE FOR THIS RUN:

Follow all substantive annotation rules in the instructions below
exactly.

The sections concerning CSV serialization and CSV output formatting are
superseded ONLY for this Ollama API run by the JSON Schema enforced by
the API.

This changes only the serialization format. It does not change the
annotation task, label definitions, decision criteria, independence
requirements, or permitted information.

Return exactly one annotation object for every supplied blind_id, in the
same order as the cases. Do not omit, duplicate, merge, or reorder
cases.

Do not include chain-of-thought, self-reflection, re-evaluation prose,
commentary, or text outside the required structured response.
""".strip()

    schema_text = json.dumps(
        schema,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    user_prompt = f"""\
{wrapper}

=== ANNOTATION INSTRUCTIONS ===

{instructions}

=== BLINDED CASES FOR THIS RUN ===

{cases}

=== REQUIRED JSON SCHEMA ===

{schema_text}

=== END OF INPUT ===

Evaluate every supplied case independently.

Return exactly one annotation for each supplied blind_id, preserving
the case order.
"""

    payload = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": user_prompt,
            }
        ],
        "stream": False,
        "format": schema,
        "options": {
            "temperature": args.temperature,
            "seed": args.seed,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
        },
    }
    
    if args.thinking == "off":
        payload["think"] = False
    elif args.thinking == "on":
        payload["think"] = True
        
    print(
        f"Model       : {args.model}"
    )
    print(
        f"Cases       : {len(expected_ids)}"
    )
    print(
        f"Case file   : {args.cases}"
    )
    print(
        f"Ollama URL  : {args.url}"
    )
    print()

    response = requests.post(
        args.url,
        json=payload,
        timeout=None,
    )

    response.raise_for_status()

    data = response.json()

    message = data.get("message") or {}
    content = message.get("content") or ""

    #
    # Always preserve raw response BEFORE parsing/validation.
    #
    raw_path = args.output.with_suffix(
        args.output.suffix + ".raw.json"
    )

    raw_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw_path.write_text(
        content.rstrip() + "\n",
        encoding="utf-8",
    )

    #
    # Also preserve separate thinking output if Ollama returns any,
    # even though think=False.
    #
    thinking = message.get("thinking")

    thinking_path = args.output.with_suffix(
        args.output.suffix + ".thinking.txt"
    )

    if thinking:
        thinking_path.write_text(
            str(thinking).rstrip() + "\n",
            encoding="utf-8",
        )

    #
    # Parse structured response.
    #
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Ollama did not return valid JSON. "
            f"Raw response preserved at {raw_path}"
        ) from exc

    annotations = validate_annotations(
        parsed.get("annotations"),
        expected_ids,
    )

    #
    # Only write the final CSV AFTER complete validation succeeds.
    #
    write_csv(
        args.output,
        annotations,
    )

    metadata_path = args.output.with_suffix(
        args.output.suffix + ".meta.json"
    )

    metadata = {
        "model": data.get("model"),
        "created_at": data.get("created_at"),
        "done": data.get("done"),
        "done_reason": data.get("done_reason"),
        "total_duration": data.get("total_duration"),
        "load_duration": data.get("load_duration"),
        "prompt_eval_count": data.get(
            "prompt_eval_count"
        ),
        "prompt_eval_cached_count": data.get(
            "prompt_eval_cached_count"
        ),
        "prompt_eval_duration": data.get(
            "prompt_eval_duration"
        ),
        "eval_count": data.get("eval_count"),
        "eval_duration": data.get("eval_duration"),
        "annotation_count": len(annotations),
        "expected_blind_ids": expected_ids,
        "settings": {
            "temperature": args.temperature,
            "seed": args.seed,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
            "thinking": args.thinking,
            "stream": False,
            "structured_output": True,
        },
        "input_files": {
            "instructions": str(
                args.instructions
            ),
            "cases": str(args.cases),
        },
        "output_files": {
            "csv": str(args.output),
            "raw_json": str(raw_path),
            "thinking": (
                str(thinking_path)
                if thinking
                else None
            ),
        },
    }

    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"VALID: {len(annotations)} annotations"
    )
    print(
        f"Output      : {args.output}"
    )
    print(
        f"Raw JSON    : {raw_path}"
    )
    print(
        f"Metadata    : {metadata_path}"
    )

    if thinking:
        print(
            f"Thinking    : {thinking_path}"
        )

    print()
    print(
        f"done        = {data.get('done')}"
    )
    print(
        f"done_reason = {data.get('done_reason')}"
    )
    print(
        f"input toks  = {data.get('prompt_eval_count')}"
    )
    print(
        f"output toks = {data.get('eval_count')}"
    )


if __name__ == "__main__":
    main()

    
