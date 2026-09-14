from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "e2-blind-validation"
INFILE = BASE / "agreement-6models-unblind" / "case_unblind_summary.csv"
OUT = BASE / "agreement-6models-unblind" / "alignment-predicate"

ANNOTATORS = [
    "Qwen3.5-9B",
    "Gemma4-12B",
    "Llama3.1-8B",
    "Phi4-14B",
    "Microsoft365-Copilot",
    "Gemini3.6-Flash",
]

TARGET_ALIGNMENTS = [
    "aligned_unsafe",
    "model_induced_violation",
]


def clean(x):
    return str(x or "").strip()


def read_rows():
    with INFILE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def split_predicates(value):
    return [
        x.strip()
        for x in clean(value).split(";")
        if x.strip()
    ]


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def unique_majority(labels):
    counts = Counter(labels)
    top = max(counts.values())
    winners = [
        label for label, n in counts.items()
        if n == top
    ]
    return winners[0] if len(winners) == 1 else "TIE"


def main():
    global INFILE, OUT
    parser = argparse.ArgumentParser(description="Break down runtime alignment by failed predicate.")
    parser.add_argument("--version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.version == "v2":
        parent = BASE / "agreement-6models-v2-unblind"
        INFILE = parent / "case_unblind_summary.csv"
        OUT = parent / "alignment-predicate"
    if args.input is not None:
        INFILE = args.input.resolve()
    if args.output_dir is not None:
        OUT = args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=True)

    rows = read_rows()

    runtime_n = [
        r for r in rows
        if clean(r["runtime_label"]) == "NONCOMPLIANT"
    ]

    print(f"Runtime-NONCOMPLIANT cases: {len(runtime_n)}")

    #
    # Explode cases by failed predicate.
    #
    exploded = []

    for r in runtime_n:
        predicates = split_predicates(
            r["failed_predicate_types"]
        )

        if not predicates:
            predicates = ["<NONE>"]

        for predicate in predicates:
            exploded.append(
                {
                    "blind_id": clean(r["blind_id"]),
                    "alignment": clean(r["alignment"]),
                    "predicate": predicate,
                    "row": r,
                }
            )

    #
    # Predicate composition by alignment.
    #
    composition = defaultdict(Counter)

    for x in exploded:
        composition[x["alignment"]][x["predicate"]] += 1

    composition_rows = []

    all_predicates = sorted(
        {x["predicate"] for x in exploded}
    )

    for alignment in sorted(composition):
        total = sum(composition[alignment].values())

        for predicate in all_predicates:
            n = composition[alignment][predicate]

            if n == 0:
                continue

            composition_rows.append(
                {
                    "alignment": alignment,
                    "predicate_type": predicate,
                    "n_cases": n,
                    "share_within_alignment":
                        n / total if total else None,
                }
            )

    write_csv(
        OUT / "predicate_composition_by_alignment.csv",
        [
            "alignment",
            "predicate_type",
            "n_cases",
            "share_within_alignment",
        ],
        composition_rows,
    )

    #
    # Cell-level analysis:
    # alignment x predicate.
    #
    cells = defaultdict(list)

    for x in exploded:
        cells[
            (x["alignment"], x["predicate"])
        ].append(x)

    cell_rows = []

    for (alignment, predicate), items in sorted(cells.items()):
        n_cases = len(items)

        total_judgments = n_cases * len(ANNOTATORS)
        matching_judgments = 0
        noncompliant_votes = 0
        compliant_votes = 0
        uncertain_votes = 0

        per_annotator = {}

        majority_matches = 0
        majority_ties = 0

        for item in items:
            row = item["row"]

            labels = [
                clean(row[f"{a}_label"])
                for a in ANNOTATORS
            ]

            counts = Counter(labels)

            noncompliant_votes += counts["NONCOMPLIANT"]
            compliant_votes += counts["COMPLIANT"]
            uncertain_votes += counts["UNCERTAIN"]

            matching_judgments += counts["NONCOMPLIANT"]

            maj = unique_majority(labels)

            if maj == "NONCOMPLIANT":
                majority_matches += 1
            elif maj == "TIE":
                majority_ties += 1

        for annotator in ANNOTATORS:
            judgments = [
                clean(
                    item["row"][f"{annotator}_label"]
                )
                for item in items
            ]

            agree_n = sum(
                label == "NONCOMPLIANT"
                for label in judgments
            )

            per_annotator[annotator] = (
                agree_n,
                agree_n / n_cases,
            )

        out = {
            "alignment": alignment,
            "predicate_type": predicate,
            "n_cases": n_cases,
            "n_annotator_judgments": total_judgments,
            "runtime_agreement_n": matching_judgments,
            "runtime_agreement_rate":
                matching_judgments / total_judgments,
            "noncompliant_votes": noncompliant_votes,
            "compliant_votes": compliant_votes,
            "uncertain_votes": uncertain_votes,
            "unique_majority_matches_runtime_n":
                majority_matches,
            "unique_majority_matches_runtime_rate":
                majority_matches / n_cases,
            "majority_tie_n": majority_ties,
        }

        for annotator in ANNOTATORS:
            n, rate = per_annotator[annotator]

            out[f"{annotator}_agreement_n"] = n
            out[f"{annotator}_agreement_rate"] = rate

        cell_rows.append(out)

    cell_fields = [
        "alignment",
        "predicate_type",
        "n_cases",
        "n_annotator_judgments",
        "runtime_agreement_n",
        "runtime_agreement_rate",
        "noncompliant_votes",
        "compliant_votes",
        "uncertain_votes",
        "unique_majority_matches_runtime_n",
        "unique_majority_matches_runtime_rate",
        "majority_tie_n",
    ]

    for annotator in ANNOTATORS:
        cell_fields.extend(
            [
                f"{annotator}_agreement_n",
                f"{annotator}_agreement_rate",
            ]
        )

    write_csv(
        OUT / "alignment_predicate_cells.csv",
        cell_fields,
        cell_rows,
    )

    #
    # Direct comparison:
    # within the SAME predicate,
    # aligned_unsafe vs model_induced_violation.
    #
    lookup = {
        (r["alignment"], r["predicate_type"]): r
        for r in cell_rows
    }

    comparison_rows = []

    for predicate in all_predicates:
        a = lookup.get(
            ("aligned_unsafe", predicate)
        )
        m = lookup.get(
            ("model_induced_violation", predicate)
        )

        if not a or not m:
            continue

        comparison_rows.append(
            {
                "predicate_type": predicate,

                "aligned_unsafe_n_cases":
                    a["n_cases"],

                "aligned_unsafe_agreement_rate":
                    a["runtime_agreement_rate"],

                "model_induced_n_cases":
                    m["n_cases"],

                "model_induced_agreement_rate":
                    m["runtime_agreement_rate"],

                "difference_model_induced_minus_aligned_unsafe":
                    (
                        m["runtime_agreement_rate"]
                        - a["runtime_agreement_rate"]
                    ),
            }
        )

    write_csv(
        OUT / "within_predicate_alignment_comparison.csv",
        [
            "predicate_type",
            "aligned_unsafe_n_cases",
            "aligned_unsafe_agreement_rate",
            "model_induced_n_cases",
            "model_induced_agreement_rate",
            "difference_model_induced_minus_aligned_unsafe",
        ],
        comparison_rows,
    )

    #
    # Friendly console output.
    #
    print()
    print("=== Predicate composition by alignment ===")

    for alignment in TARGET_ALIGNMENTS:
        print()
        print(alignment)

        total = sum(composition[alignment].values())

        for predicate, n in sorted(
            composition[alignment].items()
        ):
            print(
                f"  {predicate:32s} "
                f"{n:2d}/{total:2d} "
                f"({100*n/total:5.1f}%)"
            )

    print()
    print("=== Alignment x predicate agreement ===")
    print(
        f"{'Alignment':25s} "
        f"{'Predicate':32s} "
        f"{'n':>3s} "
        f"{'Agreement':>10s}"
    )
    print("-" * 76)

    for r in cell_rows:
        if r["alignment"] not in TARGET_ALIGNMENTS:
            continue

        print(
            f"{r['alignment']:25s} "
            f"{r['predicate_type']:32s} "
            f"{r['n_cases']:3d} "
            f"{100*r['runtime_agreement_rate']:9.1f}%"
        )

    print()
    print("=== Same-predicate comparison ===")
    print(
        f"{'Predicate':32s} "
        f"{'Aligned unsafe':>15s} "
        f"{'Model-induced':>15s} "
        f"{'Difference':>12s}"
    )
    print("-" * 80)

    if not comparison_rows:
        print(
            "No predicate occurs in both alignment groups."
        )

    for r in comparison_rows:
        print(
            f"{r['predicate_type']:32s} "
            f"{100*r['aligned_unsafe_agreement_rate']:14.1f}% "
            f"{100*r['model_induced_agreement_rate']:14.1f}% "
            f"{100*r['difference_model_induced_minus_aligned_unsafe']:+11.1f} pp"
        )

    print()
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()

    
