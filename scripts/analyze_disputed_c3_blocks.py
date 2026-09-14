from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "e2-blind-validation"
HYBRID = (
    BASE / "agreement-6models-unblind"
    / "c3-llm-hybrid"
    / "hybrid_case_analysis.csv"
)
OUT = (
    BASE / "agreement-6models-unblind"
    / "c3-llm-hybrid"
    / "disputed-c3-blocks"
)

ANNOTATOR_FILES = {
    "Qwen3.5-9B": BASE / "qwen35_structured_all_41.csv",
    "Gemma4-12B": BASE / "gemma4_12b_structured_all_41.csv",
    "Llama3.1-8B": BASE / "llama31_8b_structured_all_41.csv",
    "Phi4-14B": BASE / "phi4_14b_structured_all_41.csv",
    "Microsoft365-Copilot": BASE / "copilot_all_41.csv",
    "Gemini3.6-Flash": BASE / "gemini_3.6_flash_all_41.csv",
}

ANNOTATORS = list(ANNOTATOR_FILES)


def clean(x):
    return str(x or "").strip()


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    hybrid = read_csv(HYBRID)

    six = {
        clean(r["blind_id"]): r
        for r in hybrid
        if r["config"] == "six_annotators"
    }

    five = {
        clean(r["blind_id"]): r
        for r in hybrid
        if r["config"] == "five_annotator_sensitivity"
    }

    annotations = {}

    for annotator, path in ANNOTATOR_FILES.items():
        annotations[annotator] = {
            clean(r["blind_id"]): r
            for r in read_csv(path)
        }

    #
    # C3 blocked, but six-annotator layer did NOT
    # produce a strict NONCOMPLIANT majority.
    #
    disputed = [
        r for r in six.values()
        if (
            r["c3_label"] == "NONCOMPLIANT"
            and r["hybrid_relation"] != "CONCORDANT_BLOCK"
        )
    ]

    if len(disputed) != 9:
        raise RuntimeError(
            f"Expected 9 disputed C3 blocks, found {len(disputed)}"
        )

    detail_rows = []
    long_rows = []

    for r in disputed:
        bid = clean(r["blind_id"])

        compliant = []
        noncompliant = []
        uncertain = []

        for annotator in ANNOTATORS:
            a = annotations[annotator][bid]
            label = clean(a["compliance"])

            if label == "COMPLIANT":
                compliant.append(annotator)
            elif label == "NONCOMPLIANT":
                noncompliant.append(annotator)
            elif label == "UNCERTAIN":
                uncertain.append(annotator)

            long_rows.append({
                "blind_id": bid,
                "six_annotator_relation":
                    r["hybrid_relation"],
                "family": r["family"],
                "alignment": r["alignment"],
                "failed_predicate_types":
                    r["failed_predicate_types"],
                "annotator": annotator,
                "compliance_label": label,
                "confidence": clean(a["confidence"]),
                "brief_reason": clean(a["brief_reason"]),
            })

        five_r = five[bid]

        detail_rows.append({
            "blind_id": bid,
            "family": r["family"],
            "alignment": r["alignment"],
            "failed_predicate_types":
                r["failed_predicate_types"],

            "runtime_derived_label":
                r["c3_label"],

            "six_annotator_relation":
                r["hybrid_relation"],

            "n_compliant":
                r["n_compliant"],
            "n_noncompliant":
                r["n_noncompliant"],
            "n_uncertain":
                r["n_uncertain"],

            "compliant_annotators":
                ";".join(compliant),
            "noncompliant_annotators":
                ";".join(noncompliant),
            "uncertain_annotators":
                ";".join(uncertain),

            "five_annotator_relation":
                five_r["hybrid_relation"],

            "five_n_compliant":
                five_r["n_compliant"],
            "five_n_noncompliant":
                five_r["n_noncompliant"],
            "five_n_uncertain":
                five_r["n_uncertain"],
        })

    write_csv(
        OUT / "disputed_c3_blocks_detail.csv",
        list(detail_rows[0]),
        detail_rows,
    )

    write_csv(
        OUT / "disputed_c3_blocks_annotations.csv",
        list(long_rows[0]),
        long_rows,
    )

    #
    # Summaries.
    #
    for field in [
        "failed_predicate_types",
        "alignment",
        "family",
        "six_annotator_relation",
    ]:
        counts = Counter(
            r[field] for r in detail_rows
        )

        rows = [
            {
                field: value,
                "n_cases": n,
                "rate": n / len(detail_rows),
            }
            for value, n in sorted(counts.items())
        ]

        write_csv(
            OUT / f"by_{field}.csv",
            [field, "n_cases", "rate"],
            rows,
        )

    #
    # How often each annotator supports C3's block
    # versus challenges it.
    #
    annotator_rows = []

    for annotator in ANNOTATORS:
        labels = [
            annotations[annotator][r["blind_id"]]["compliance"]
            for r in detail_rows
        ]

        c = labels.count("COMPLIANT")
        n = labels.count("NONCOMPLIANT")
        u = labels.count("UNCERTAIN")

        annotator_rows.append({
            "annotator": annotator,
            "n_disputed_cases": len(detail_rows),
            "supports_c3_block_n": n,
            "supports_c3_block_rate":
                n / len(detail_rows),
            "challenges_c3_block_n": c,
            "challenges_c3_block_rate":
                c / len(detail_rows),
            "uncertain_n": u,
        })

    write_csv(
        OUT / "annotator_positions.csv",
        list(annotator_rows[0]),
        annotator_rows,
    )

    #
    # Console report.
    #
    print("=== Disputed C3 blocks ===")
    print(f"Cases: {len(detail_rows)}")
    print()

    print(
        f"{'ID':14s} "
        f"{'Relation':29s} "
        f"{'Alignment':25s} "
        f"{'Predicate':31s} "
        f"{'C/N/U':7s}"
    )
    print("-" * 112)

    for r in detail_rows:
        votes = (
            f"{r['n_compliant']}/"
            f"{r['n_noncompliant']}/"
            f"{r['n_uncertain']}"
        )

        print(
            f"{r['blind_id']:14s} "
            f"{r['six_annotator_relation']:29s} "
            f"{r['alignment']:25s} "
            f"{r['failed_predicate_types']:31s} "
            f"{votes:7s}"
        )

    print()
    print("=== By failed predicate ===")

    pred = Counter(
        r["failed_predicate_types"]
        for r in detail_rows
    )

    for p, n in pred.most_common():
        print(
            f"  {p:34s} {n}/9 ({100*n/9:.1f}%)"
        )

    print()
    print("=== By alignment ===")

    ali = Counter(
        r["alignment"]
        for r in detail_rows
    )

    for a, n in ali.most_common():
        print(
            f"  {a:28s} {n}/9 ({100*n/9:.1f}%)"
        )

    print()
    print("=== Annotator positions on the 9 cases ===")

    for r in annotator_rows:
        print(
            f"  {r['annotator']:24s} "
            f"C3-support N={r['supports_c3_block_n']}/9, "
            f"challenge C={r['challenges_c3_block_n']}/9, "
            f"uncertain={r['uncertain_n']}"
        )

    print()
    print("=== Six vs five annotators ===")

    transitions = Counter(
        (
            r["six_annotator_relation"],
            r["five_annotator_relation"],
        )
        for r in detail_rows
    )

    for (a, b), n in transitions.items():
        print(f"  {a} -> {b}: {n}")

    print()
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()

    
