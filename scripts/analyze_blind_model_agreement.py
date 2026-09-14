from __future__ import annotations

import csv
import json
from collections import Counter
from itertools import combinations
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "e2-blind-validation"
OUT = BASE / "agreement-6models"

MODELS = {
    "Qwen3.5-9B": BASE / "qwen35_structured_all_41.csv",
    "Gemma4-12B": BASE / "gemma4_12b_structured_all_41.csv",
    "Llama3.1-8B": BASE / "llama31_8b_structured_all_41.csv",
    "Phi4-14B": BASE / "phi4_14b_structured_all_41.csv",
    "Copilot": BASE / "copilot_all_41.csv",
    "Gemini3.6-Flash": BASE / "gemini_3.6_flash_all_41.csv",
}

LABELS = (
    "COMPLIANT",
    "NONCOMPLIANT",
    "UNCERTAIN",
)

EXPECTED_CASES = 41


def load_annotations(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RuntimeError(f"Missing file: {path}")

    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if len(rows) != EXPECTED_CASES:
        raise RuntimeError(
            f"{path.name}: expected {EXPECTED_CASES} rows, "
            f"found {len(rows)}"
        )

    result: dict[str, str] = {}

    for row in rows:
        blind_id = (row.get("blind_id") or "").strip()
        label = (row.get("compliance") or "").strip()

        if not blind_id:
            raise RuntimeError(
                f"{path.name}: empty blind_id"
            )

        if blind_id in result:
            raise RuntimeError(
                f"{path.name}: duplicate blind_id={blind_id}"
            )

        if label not in LABELS:
            raise RuntimeError(
                f"{path.name}: invalid label "
                f"{label!r} for {blind_id}"
            )

        result[blind_id] = label

    return result


def cohen_kappa(
    left: dict[str, str],
    right: dict[str, str],
    ids: list[str],
) -> tuple[float, float, float]:
    n = len(ids)

    observed = sum(
        left[i] == right[i]
        for i in ids
    ) / n

    p_left = Counter(left[i] for i in ids)
    p_right = Counter(right[i] for i in ids)

    expected = sum(
        (p_left[label] / n)
        * (p_right[label] / n)
        for label in LABELS
    )

    if expected == 1.0:
        kappa = 1.0 if observed == 1.0 else float("nan")
    else:
        kappa = (observed - expected) / (1.0 - expected)

    return observed, expected, kappa


def fleiss_kappa(
    annotations: dict[str, dict[str, str]],
    ids: list[str],
) -> tuple[float, float, float]:
    model_names = list(annotations)
    n_raters = len(model_names)
    n_items = len(ids)

    item_agreements = []

    total_label_counts = Counter()

    for blind_id in ids:
        counts = Counter(
            annotations[model][blind_id]
            for model in model_names
        )

        total_label_counts.update(counts)

        sum_sq = sum(
            counts[label] ** 2
            for label in LABELS
        )

        p_i = (
            sum_sq - n_raters
        ) / (
            n_raters * (n_raters - 1)
        )

        item_agreements.append(p_i)

    p_bar = sum(item_agreements) / n_items

    total_ratings = n_items * n_raters

    category_proportions = {
        label: total_label_counts[label] / total_ratings
        for label in LABELS
    }

    p_e = sum(
        p ** 2
        for p in category_proportions.values()
    )

    if p_e == 1.0:
        kappa = 1.0 if p_bar == 1.0 else float("nan")
    else:
        kappa = (p_bar - p_e) / (1.0 - p_e)

    return p_bar, p_e, kappa


def vote_pattern(counts: Counter) -> str:
    values = sorted(
        (v for v in counts.values() if v),
        reverse=True,
    )
    return "-".join(map(str, values))


def majority_label(counts: Counter) -> str:
    max_count = max(counts.values())

    winners = [
        label
        for label in LABELS
        if counts[label] == max_count
    ]

    if len(winners) != 1:
        return "TIE"

    return winners[0]


def write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict],
) -> None:
    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    annotations = {
        model: load_annotations(path)
        for model, path in MODELS.items()
    }

    model_names = list(annotations)

    #
    # Establish the case set using the first model,
    # then require exact equality across all six.
    #
    reference_ids = list(
        annotations[model_names[0]].keys()
    )
    reference_set = set(reference_ids)

    for model in model_names[1:]:
        ids = set(annotations[model])

        if ids != reference_set:
            missing = sorted(reference_set - ids)
            extra = sorted(ids - reference_set)

            raise RuntimeError(
                f"{model}: blind-ID mismatch\n"
                f"missing={missing}\n"
                f"extra={extra}"
            )

    if len(reference_ids) != EXPECTED_CASES:
        raise RuntimeError(
            f"Expected {EXPECTED_CASES} common cases, "
            f"found {len(reference_ids)}"
        )

    #
    # 1. Per-model label counts.
    #
    label_count_rows = []

    for model in model_names:
        counts = Counter(
            annotations[model][i]
            for i in reference_ids
        )

        row = {
            "model": model,
            "total": len(reference_ids),
        }

        for label in LABELS:
            row[label] = counts[label]

        label_count_rows.append(row)

    write_csv(
        OUT / "model_label_counts.csv",
        [
            "model",
            "total",
            *LABELS,
        ],
        label_count_rows,
    )

    #
    # 2. Pairwise raw agreement + Cohen's kappa.
    #
    pairwise_rows = []

    agreement_matrix = {
        model: {}
        for model in model_names
    }

    kappa_matrix = {
        model: {}
        for model in model_names
    }

    for model in model_names:
        agreement_matrix[model][model] = 1.0
        kappa_matrix[model][model] = 1.0

    for left_name, right_name in combinations(
        model_names,
        2,
    ):
        observed, expected, kappa = cohen_kappa(
            annotations[left_name],
            annotations[right_name],
            reference_ids,
        )

        agreement_matrix[left_name][right_name] = observed
        agreement_matrix[right_name][left_name] = observed

        kappa_matrix[left_name][right_name] = kappa
        kappa_matrix[right_name][left_name] = kappa

        pairwise_rows.append(
            {
                "model_a": left_name,
                "model_b": right_name,
                "n": len(reference_ids),
                "raw_agreement": observed,
                "expected_agreement": expected,
                "cohen_kappa": kappa,
            }
        )

    write_csv(
        OUT / "pairwise_agreement_long.csv",
        [
            "model_a",
            "model_b",
            "n",
            "raw_agreement",
            "expected_agreement",
            "cohen_kappa",
        ],
        pairwise_rows,
    )

    #
    # Agreement matrix.
    #
    agreement_matrix_rows = []

    for row_model in model_names:
        row = {
            "model": row_model,
        }

        for col_model in model_names:
            row[col_model] = (
                agreement_matrix[row_model][col_model]
            )

        agreement_matrix_rows.append(row)

    write_csv(
        OUT / "pairwise_raw_agreement_matrix.csv",
        [
            "model",
            *model_names,
        ],
        agreement_matrix_rows,
    )

    #
    # Cohen-kappa matrix.
    #
    kappa_matrix_rows = []

    for row_model in model_names:
        row = {
            "model": row_model,
        }

        for col_model in model_names:
            row[col_model] = (
                kappa_matrix[row_model][col_model]
            )

        kappa_matrix_rows.append(row)

    write_csv(
        OUT / "pairwise_cohen_kappa_matrix.csv",
        [
            "model",
            *model_names,
        ],
        kappa_matrix_rows,
    )

    #
    # 3. Fleiss' kappa across all six model annotators.
    #
    p_bar, p_e, fleiss = fleiss_kappa(
        annotations,
        reference_ids,
    )

    #
    # 4. Per-case voting summary.
    #
    case_rows = []
    pattern_counts = Counter()
    unanimous_count = 0
    uncertain_case_count = 0

    for order, blind_id in enumerate(
        reference_ids,
        start=1,
    ):
        labels = {
            model: annotations[model][blind_id]
            for model in model_names
        }

        counts = Counter(labels.values())
        pattern = vote_pattern(counts)

        pattern_counts[pattern] += 1

        if max(counts.values()) == len(model_names):
            unanimous_count += 1

        has_uncertain = counts["UNCERTAIN"] > 0

        if has_uncertain:
            uncertain_case_count += 1

        row = {
            "order": order,
            "blind_id": blind_id,
        }

        row.update(labels)

        row.update(
            {
                "n_compliant": counts["COMPLIANT"],
                "n_noncompliant": counts["NONCOMPLIANT"],
                "n_uncertain": counts["UNCERTAIN"],
                "vote_pattern": pattern,
                "majority_label": majority_label(counts),
                "unanimous": (
                    "YES"
                    if max(counts.values())
                    == len(model_names)
                    else "NO"
                ),
            }
        )

        case_rows.append(row)

    write_csv(
        OUT / "case_vote_summary.csv",
        [
            "order",
            "blind_id",
            *model_names,
            "n_compliant",
            "n_noncompliant",
            "n_uncertain",
            "vote_pattern",
            "majority_label",
            "unanimous",
        ],
        case_rows,
    )

    #
    # Cases with any UNCERTAIN label.
    #
    uncertain_rows = [
        row
        for row in case_rows
        if row["n_uncertain"] > 0
    ]

    write_csv(
        OUT / "cases_with_uncertain.csv",
        [
            "order",
            "blind_id",
            *model_names,
            "n_compliant",
            "n_noncompliant",
            "n_uncertain",
            "vote_pattern",
            "majority_label",
            "unanimous",
        ],
        uncertain_rows,
    )

    #
    # Non-unanimous cases.
    #
    disagreement_rows = [
        row
        for row in case_rows
        if row["unanimous"] == "NO"
    ]

    write_csv(
        OUT / "disagreement_cases.csv",
        [
            "order",
            "blind_id",
            *model_names,
            "n_compliant",
            "n_noncompliant",
            "n_uncertain",
            "vote_pattern",
            "majority_label",
            "unanimous",
        ],
        disagreement_rows,
    )

    #
    # 5. Overall JSON summary.
    #
    summary = {
        "n_cases": len(reference_ids),
        "n_annotators": len(model_names),
        "annotators": model_names,
        "labels": list(LABELS),
        "fleiss_kappa": fleiss,
        "fleiss_observed_agreement": p_bar,
        "fleiss_expected_agreement": p_e,
        "unanimous_cases": unanimous_count,
        "non_unanimous_cases": (
            len(reference_ids) - unanimous_count
        ),
        "cases_with_uncertain": uncertain_case_count,
        "vote_pattern_counts": dict(
            sorted(pattern_counts.items())
        ),
    }

    (OUT / "agreement_summary.json").write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    #
    # Console summary.
    #
    print("=== Six-model blind agreement ===")
    print(
        f"Cases              : {len(reference_ids)}"
    )
    print(
        f"Annotators         : {len(model_names)}"
    )
    print(
        f"Fleiss' kappa      : {fleiss:.4f}"
    )
    print(
        f"Observed agreement : {p_bar:.4f}"
    )
    print(
        f"Expected agreement : {p_e:.4f}"
    )
    print(
        f"Unanimous cases    : "
        f"{unanimous_count}/{len(reference_ids)}"
    )
    print(
        f"Cases w/ UNCERTAIN : "
        f"{uncertain_case_count}/{len(reference_ids)}"
    )

    print("\nVote patterns:")
    for pattern, count in sorted(
        pattern_counts.items()
    ):
        print(
            f"  {pattern:8s} : {count}"
        )

    print("\nPer-model label counts:")
    for row in label_count_rows:
        print(
            f"  {row['model']:18s} "
            f"C={row['COMPLIANT']:2d} "
            f"N={row['NONCOMPLIANT']:2d} "
            f"U={row['UNCERTAIN']:2d}"
        )

    print()
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()

