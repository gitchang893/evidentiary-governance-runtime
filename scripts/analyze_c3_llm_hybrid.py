from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "e2-blind-validation"
INFILE = (
    BASE
    / "agreement-6models-unblind"
    / "case_unblind_summary.csv"
)
OUT = (
    BASE
    / "agreement-6models-unblind"
    / "c3-llm-hybrid"
)

SIX_ANNOTATORS = [
    "Qwen3.5-9B",
    "Gemma4-12B",
    "Llama3.1-8B",
    "Phi4-14B",
    "Microsoft365-Copilot",
    "Gemini3.6-Flash",
]

# Removing either Copilot or Gemini gives the same
# compliance-label sensitivity result because their
# 41-label vectors are identical.
FIVE_ANNOTATORS = [
    "Qwen3.5-9B",
    "Gemma4-12B",
    "Llama3.1-8B",
    "Phi4-14B",
    "Microsoft365-Copilot",
]

CONFIGS = {
    "six_annotators": SIX_ANNOTATORS,
    "five_annotator_sensitivity": FIVE_ANNOTATORS,
}


def clean(x):
    return str(x or "").strip()


def write_csv(path, fields, rows):
    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        w.writeheader()
        w.writerows(rows)


def vote_summary(row, annotators):
    labels = [
        clean(row[f"{a}_label"])
        for a in annotators
    ]

    counts = Counter(labels)

    n = len(annotators)
    top_votes = max(counts.values())

    winners = [
        label
        for label, count in counts.items()
        if count == top_votes
    ]

    top_label = (
        winners[0]
        if len(winners) == 1
        else "TIE"
    )

    strict_majority = (
        len(winners) == 1
        and top_votes > n / 2
    )

    strict_label = (
        top_label
        if strict_majority
        else "NONE"
    )

    return {
        "n_annotators": n,
        "n_compliant": counts["COMPLIANT"],
        "n_noncompliant": counts["NONCOMPLIANT"],
        "n_uncertain": counts["UNCERTAIN"],
        "top_label": top_label,
        "top_votes": top_votes,
        "strict_majority": strict_majority,
        "strict_majority_label": strict_label,
    }


def classify(c3_label, votes):
    llm = votes["strict_majority_label"]

    if c3_label == "NONCOMPLIANT":
        action = "BLOCK"

        if llm == "NONCOMPLIANT":
            relation = "CONCORDANT_BLOCK"

        elif llm == "COMPLIANT":
            relation = "C3_BLOCK_LLM_DISSENT"

        else:
            relation = "C3_BLOCK_LLM_INDETERMINATE"

    elif c3_label == "COMPLIANT":

        if llm == "COMPLIANT":
            action = "AUTO_EXECUTE"
            relation = "CONCORDANT_PERMIT"

        elif llm == "NONCOMPLIANT":
            action = "REVIEW"
            relation = "LLM_DISSENT_ON_C3_PERMIT"

        else:
            action = "REVIEW"
            relation = "C3_PERMIT_LLM_INDETERMINATE"

    else:
        raise ValueError(
            f"Unknown C3 label: {c3_label}"
        )

    return action, relation


def main():
    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    with INFILE.open(
        encoding="utf-8",
        newline="",
    ) as f:
        cases = list(csv.DictReader(f))

    all_rows = []
    summaries = {}

    for config_name, annotators in CONFIGS.items():
        config_rows = []

        for row in cases:
            votes = vote_summary(
                row,
                annotators,
            )

            c3_label = clean(
                row["runtime_label"]
            )

            action, relation = classify(
                c3_label,
                votes,
            )

            out = {
                "config": config_name,
                "blind_id": clean(row["blind_id"]),
                "scenario_id":
                    clean(row["scenario_id"]),
                "family":
                    clean(row["family"]),
                "scenario_kind":
                    clean(row["scenario_kind"]),
                "alignment":
                    clean(row["alignment"]),
                "failed_predicate_types":
                    clean(
                        row["failed_predicate_types"]
                    ),

                "c3_label": c3_label,

                "n_annotators":
                    votes["n_annotators"],
                "n_compliant":
                    votes["n_compliant"],
                "n_noncompliant":
                    votes["n_noncompliant"],
                "n_uncertain":
                    votes["n_uncertain"],

                "llm_top_label":
                    votes["top_label"],
                "llm_top_votes":
                    votes["top_votes"],

                "llm_strict_majority":
                    (
                        "YES"
                        if votes["strict_majority"]
                        else "NO"
                    ),

                "llm_strict_majority_label":
                    votes[
                        "strict_majority_label"
                    ],

                "hybrid_relation":
                    relation,

                "hybrid_action":
                    action,
            }

            config_rows.append(out)
            all_rows.append(out)

        relations = Counter(
            r["hybrid_relation"]
            for r in config_rows
        )

        actions = Counter(
            r["hybrid_action"]
            for r in config_rows
        )

        strict_cases = [
            r
            for r in config_rows
            if (
                r["llm_strict_majority"]
                == "YES"
            )
        ]

        strict_agree = sum(
            r["llm_strict_majority_label"]
            == r["c3_label"]
            for r in strict_cases
        )

        summaries[config_name] = {
            "n_cases": len(config_rows),

            "strict_majority_cases":
                len(strict_cases),

            "strict_majority_agreement_with_c3_n":
                strict_agree,

            "strict_majority_agreement_with_c3_rate":
                (
                    strict_agree
                    / len(strict_cases)
                    if strict_cases
                    else None
                ),

            "relations": dict(relations),
            "actions": dict(actions),
        }

    fields = list(all_rows[0].keys())

    write_csv(
        OUT / "hybrid_case_analysis.csv",
        fields,
        all_rows,
    )

    #
    # Only the cases where C3 and the LLM advisory
    # layer do not cleanly agree.
    #
    disagreement = [
        r
        for r in all_rows
        if r["hybrid_relation"]
        not in {
            "CONCORDANT_BLOCK",
            "CONCORDANT_PERMIT",
        }
    ]

    write_csv(
        OUT / "hybrid_review_cases.csv",
        fields,
        disagreement,
    )

    #
    # Threshold sweep.
    #
    threshold_rows = []

    for config_name, annotators in CONFIGS.items():
        n = len(annotators)

        config_cases = [
            r
            for r in all_rows
            if r["config"] == config_name
        ]

        c3_permitted = [
            r
            for r in config_cases
            if r["c3_label"] == "COMPLIANT"
        ]

        c3_blocked = [
            r
            for r in config_cases
            if r["c3_label"] == "NONCOMPLIANT"
        ]

        for threshold in range(1, n + 1):

            #
            # NONCOMPLIANT votes only.
            #
            review_n = sum(
                r["n_noncompliant"]
                >= threshold
                for r in c3_permitted
            )

            corroborated_block_n = sum(
                r["n_noncompliant"]
                >= threshold
                for r in c3_blocked
            )

            threshold_rows.append(
                {
                    "config": config_name,
                    "vote_definition":
                        "NONCOMPLIANT_ONLY",
                    "threshold": threshold,

                    "c3_permitted_n":
                        len(c3_permitted),

                    "c3_permitted_flagged_for_review_n":
                        review_n,

                    "c3_permitted_flagged_for_review_rate":
                        review_n
                        / len(c3_permitted),

                    "c3_blocked_n":
                        len(c3_blocked),

                    "c3_blocked_corroborated_n":
                        corroborated_block_n,

                    "c3_blocked_corroborated_rate":
                        corroborated_block_n
                        / len(c3_blocked),
                }
            )

            #
            # NONCOMPLIANT or UNCERTAIN counts as
            # advisory dissent.
            #
            review_n = sum(
                (
                    r["n_noncompliant"]
                    + r["n_uncertain"]
                )
                >= threshold
                for r in c3_permitted
            )

            corroborated_n = sum(
                (
                    r["n_noncompliant"]
                    + r["n_uncertain"]
                )
                >= threshold
                for r in c3_blocked
            )

            threshold_rows.append(
                {
                    "config": config_name,
                    "vote_definition":
                        "NONCOMPLIANT_OR_UNCERTAIN",
                    "threshold": threshold,

                    "c3_permitted_n":
                        len(c3_permitted),

                    "c3_permitted_flagged_for_review_n":
                        review_n,

                    "c3_permitted_flagged_for_review_rate":
                        review_n
                        / len(c3_permitted),

                    "c3_blocked_n":
                        len(c3_blocked),

                    "c3_blocked_corroborated_n":
                        corroborated_n,

                    "c3_blocked_corroborated_rate":
                        corroborated_n
                        / len(c3_blocked),
                }
            )

    threshold_fields = list(
        threshold_rows[0].keys()
    )

    write_csv(
        OUT / "hybrid_threshold_sweep.csv",
        threshold_fields,
        threshold_rows,
    )

    #
    # Aggregate relation table.
    #
    relation_rows = []

    for config_name, summary in summaries.items():
        for relation, count in sorted(
            summary["relations"].items()
        ):
            relation_rows.append(
                {
                    "config": config_name,
                    "hybrid_relation": relation,
                    "n_cases": count,
                    "rate":
                        count
                        / summary["n_cases"],
                }
            )

    write_csv(
        OUT / "hybrid_relation_summary.csv",
        [
            "config",
            "hybrid_relation",
            "n_cases",
            "rate",
        ],
        relation_rows,
    )

    (OUT / "hybrid_summary.json").write_text(
        json.dumps(
            summaries,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    #
    # Console
    #
    print("=== C3 + LLM annotator hybrid analysis ===")

    for config_name, summary in summaries.items():
        print()
        print(config_name)

        print(
            "  strict-majority cases: "
            f"{summary['strict_majority_cases']}/41"
        )

        print(
            "  strict-majority agreement with C3: "
            f"{summary['strict_majority_agreement_with_c3_n']}/"
            f"{summary['strict_majority_cases']} "
            f"("
            f"{100 * summary['strict_majority_agreement_with_c3_rate']:.1f}%"
            f")"
        )

        print("  relations:")

        for relation, n in sorted(
            summary["relations"].items()
        ):
            print(
                f"    {relation:32s} {n:2d}"
            )

        print("  hybrid actions:")

        for action, n in sorted(
            summary["actions"].items()
        ):
            print(
                f"    {action:16s} {n:2d}"
            )

    print()
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()

    
