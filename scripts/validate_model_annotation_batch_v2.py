# scripts/validate_model_annotation_batch.py

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    case_text = args.cases.read_text(encoding="utf-8")

    expected_ids = re.findall(
        r"(?m)^## Case \d+:\s+(`?)(CLV-[A-Fa-f0-9]+)\1\s*$",
        case_text,
    )
    expected_ids = [x[1] for x in expected_ids]

    if not expected_ids:
        raise SystemExit("No blind IDs found in case file.")

    with args.output.open(
        encoding="utf-8",
        newline="",
    ) as f:
        rows = list(csv.DictReader(f))

    actual_ids = [
        row.get("blind_id", "").strip()
        for row in rows
    ]

    print(f"Expected cases : {len(expected_ids)}")
    print(f"Output rows    : {len(rows)}")

    missing = [
        blind_id
        for blind_id in expected_ids
        if blind_id not in actual_ids
    ]

    unexpected = [
        blind_id
        for blind_id in actual_ids
        if blind_id not in expected_ids
    ]

    duplicates = sorted({
        blind_id
        for blind_id in actual_ids
        if actual_ids.count(blind_id) > 1
    })

    invalid = []

    for row in rows:
        blind_id = (row.get("blind_id") or "").strip()
        compliance = (row.get("compliance") or "").strip()
        confidence = (row.get("confidence") or "").strip()
        reason = (row.get("brief_reason") or "").strip()
        violated_norm_labels = (row.get("violated_norm_labels") or "").strip()

        problems = []

        extra_fields = row.get(None)

        if extra_fields:
            problems.append(
                f"extra CSV fields={extra_fields!r}"
            )

        if compliance not in VALID_COMPLIANCE:
            problems.append(
                f"bad compliance={compliance!r}"
            )

        if confidence not in VALID_CONFIDENCE:
            problems.append(
                f"bad confidence={confidence!r}"
            )

        if not reason:
            problems.append("empty brief_reason")

        if (
            compliance == "NONCOMPLIANT"
            and not row.get(
                "violated_norm_labels", ""
            ).strip()
        ):
            problems.append(
                "NONCOMPLIANT without violated norm"
            )

        if problems:
            invalid.append(
                (blind_id, problems)
            )

    print(f"Missing        : {missing}")
    print(f"Unexpected     : {unexpected}")
    print(f"Duplicates     : {duplicates}")
    print(f"Invalid rows   : {invalid}")

    ok = (
        len(rows) == len(expected_ids)
        and not missing
        and not unexpected
        and not duplicates
        and not invalid
        and actual_ids == expected_ids
    )

    print()
    print("VALID" if ok else "INVALID")

    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
    
