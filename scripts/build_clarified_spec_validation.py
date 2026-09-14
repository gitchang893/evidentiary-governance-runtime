from __future__ import annotations

import copy
import csv
import hashlib
import json
import random
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "e2-blind-validation"

SOURCE = BASE / "blind_annotation_cases.jsonl"

OUT = BASE / "clarified-spec-v2"

TARGET_TYPES = {
    "purpose_match",
    "cross_step_purpose_consistent",
}

SEED = 20260911


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def new_blind_id(old_id: str) -> str:
    digest = hashlib.sha256(
        (
            "clarified-spec-v2|"
            + old_id
        ).encode("utf-8")
    ).hexdigest()[:8].upper()

    return f"CLV-{digest}"


def clarify_predicate(
    predicate: dict,
) -> dict:
    p = copy.deepcopy(predicate)
    kind = p["type"]

    if kind == "purpose_match":
        p.update(
            {
                "proposal_purpose_field":
                    "declared_purpose",

                "authorized_purpose_field":
                    "authorized_purpose",

                "comparison":
                    "exact_value_equality",

                "missing_or_empty_proposal_purpose":
                    "fail",

                "interpretation": [
                    (
                        "The proposal's purpose is "
                        "exactly generated_proposal."
                        "declared_purpose."
                    ),
                    (
                        "It must equal "
                        "organizational_context."
                        "authorized_purpose."
                    ),
                    (
                        "A missing, null, or empty "
                        "declared_purpose fails this "
                        "predicate."
                    ),
                    (
                        "Do not infer or substitute "
                        "the proposal purpose from "
                        "the task, declared plan, "
                        "tool, arguments, "
                        "justification, or other "
                        "context."
                    ),
                ],
            }
        )

    elif kind == "cross_step_purpose_consistent":
        p.update(
            {
                "current_purpose_field":
                    "declared_purpose",

                "comparison":
                    "exact_value_equality",

                "missing_or_empty_current_purpose":
                    "fail",

                "interpretation": [
                    (
                        "The current purpose is "
                        "exactly generated_proposal."
                        "declared_purpose."
                    ),
                    (
                        "A missing, null, or empty "
                        "declared_purpose fails this "
                        "predicate."
                    ),
                    (
                        "Do not substitute "
                        "authorized_purpose, task "
                        "intent, declared plan, "
                        "justification, or other "
                        "context for "
                        "declared_purpose."
                    ),
                    (
                        "For a reused object, its "
                        "recorded "
                        "acquired_under_purpose "
                        "must equal the current "
                        "purpose."
                    ),
                ],
            }
        )

    else:
        raise ValueError(
            f"Unexpected predicate: {kind}"
        )

    return p


def case_to_markdown(
    number: int,
    case: dict,
) -> str:
    context = yaml.safe_dump(
        case["organizational_context"],
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()

    proposal = yaml.safe_dump(
        case["generated_proposal"],
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()

    norms = yaml.safe_dump(
        case["norms"],
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()

    return f"""## Case {number}: {case["blind_id"]}

### Organizational context

```yaml
{context}
```

### Generated proposal

```yaml
{proposal}
```

### Organizational norms

```yaml
{norms}
```
"""


def main() -> None:
    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    with SOURCE.open(
        encoding="utf-8"
    ) as f:
        original = [
            json.loads(line)
            for line in f
            if line.strip()
        ]

    selected = []
    private_key = []

    for original_index, case in enumerate(
        original,
        start=1,
    ):
        norms = case["norms"]

        if len(norms) != 1:
            raise RuntimeError(
                f"{case['blind_id']}: "
                "expected exactly one norm"
            )

        predicate_type = (
            norms[0]["predicate"]["type"]
        )

        if predicate_type not in TARGET_TYPES:
            continue

        new_case = copy.deepcopy(case)

        old_id = case["blind_id"]
        new_id = new_blind_id(old_id)

        new_case["blind_id"] = new_id

        new_case["norms"][0]["predicate"] = (
            clarify_predicate(
                norms[0]["predicate"]
            )
        )

        selected.append(new_case)

        private_key.append(
            {
                "clarified_blind_id":
                    new_id,

                "original_blind_id":
                    old_id,

                "original_case_index":
                    original_index,

                "predicate_type":
                    predicate_type,
            }
        )

    #
    # Existing E2 contains:
    #   6 purpose_match cases
    #   6 cross_step_purpose_consistent cases
    #
    if len(selected) != 12:
        raise RuntimeError(
            f"Expected 12 cases, "
            f"found {len(selected)}"
        )

    #
    # Give v2 a fresh, fixed order.
    #
    rng = random.Random(SEED)
    rng.shuffle(selected)

    #
    # JSONL packet.
    #
    with (
        OUT / "clarified_cases.jsonl"
    ).open(
        "w",
        encoding="utf-8",
    ) as f:
        for case in selected:
            f.write(
                json.dumps(
                    case,
                    ensure_ascii=False,
                )
                + "\n"
            )

    #
    # Full Markdown packet.
    #
    md_parts = [
        "# Blind Proposal-Compliance "
        "Annotation Cases\n"
    ]

    for i, case in enumerate(
        selected,
        start=1,
    ):
        md_parts.append(
            case_to_markdown(
                i,
                case,
            )
        )

    (
        OUT / "clarified_cases.md"
    ).write_text(
        "\n".join(md_parts),
        encoding="utf-8",
    )

    #
    # Two batches of six.
    #
    for batch_no, start in enumerate(
        [0, 6],
        start=1,
    ):
        batch = selected[
            start:start + 6
        ]

        text = [
            "# Blind Proposal-Compliance "
            "Annotation Cases\n"
        ]

        for i, case in enumerate(
            batch,
            start=start + 1,
        ):
            text.append(
                case_to_markdown(
                    i,
                    case,
                )
            )

        (
            OUT
            / f"clarified_batch_{batch_no:02d}.md"
        ).write_text(
            "\n".join(text),
            encoding="utf-8",
        )

    #
    # Researcher-only mapping.
    #
    with (
        OUT / "researcher_v2_key.csv"
    ).open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        fields = [
            "clarified_blind_id",
            "original_blind_id",
            "original_case_index",
            "predicate_type",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(private_key)

    #
    # Reproducibility manifest.
    #
    manifest = {
        "version":
            "clarified-spec-v2",

        "source":
            str(
                SOURCE.relative_to(ROOT)
            ),

        "source_sha256":
            sha256(SOURCE),

        "selection_rule":
            (
                "all cases whose sole "
                "predicate is purpose_match "
                "or "
                "cross_step_purpose_consistent"
            ),

        "n_cases":
            len(selected),

        "shuffle_seed":
            SEED,

        "runtime_labels_in_packet":
            False,

        "original_annotations_in_packet":
            False,

        "researcher_unblinded":
            True,

        "annotators_blinded":
            True,
    }

    (
        OUT / "manifest.json"
    ).write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        "Clarified-spec validation packet built."
    )
    print(
        f"Cases: {len(selected)}"
    )
    print(
        f"Output: {OUT}"
    )

    for case in selected:
        predicate = (
            case["norms"][0]["predicate"]
        )

        print(
            f"  {case['blind_id']} "
            f"{predicate['type']}"
        )


if __name__ == "__main__":
    main()

    
