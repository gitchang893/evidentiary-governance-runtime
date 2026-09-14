from __future__ import annotations

import csv
import json
import re
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "e2-blind-validation"
OUT = BASE / "agreement-6models-multilevel"

MODELS = {
    "Qwen3.5-9B": BASE / "qwen35_structured_all_41.csv",
    "Gemma4-12B": BASE / "gemma4_12b_structured_all_41.csv",
    "Llama3.1-8B": BASE / "llama31_8b_structured_all_41.csv",
    "Phi4-14B": BASE / "phi4_14b_structured_all_41.csv",
    "Microsoft365-Copilot": BASE / "copilot_all_41.csv",
    "Gemini3.6-Flash": BASE / "gemini_3.6_flash_all_41.csv",
}

COMPLIANCE_LABELS = [
    "COMPLIANT",
    "NONCOMPLIANT",
    "UNCERTAIN",
]

CONFIDENCE_LABELS = [
    "LOW",
    "MEDIUM",
    "HIGH",
]

EXPECTED_CASES = 41


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_reason(value: str) -> str:
    return " ".join(clean(value).lower().split())


def parse_norm_labels(value: str) -> frozenset[str]:
    """
    Norm labels are case-local.
    Extract N1, N2, ... robustly from e.g.:
      N1
      N1;N3
      N1, N3
    """
    return frozenset(
        re.findall(r"\bN\d+\b", clean(value).upper())
    )


def load_model(path: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not path.exists():
        raise RuntimeError(f"Missing: {path}")

    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if len(rows) != EXPECTED_CASES:
        raise RuntimeError(
            f"{path.name}: expected {EXPECTED_CASES} rows, "
            f"found {len(rows)}"
        )

    order: list[str] = []
    data: dict[str, dict[str, Any]] = {}

    for row in rows:
        blind_id = clean(row.get("blind_id"))
        compliance = clean(row.get("compliance"))
        confidence = clean(row.get("confidence"))

        if not blind_id:
            raise RuntimeError(f"{path.name}: empty blind_id")

        if blind_id in data:
            raise RuntimeError(
                f"{path.name}: duplicate blind_id={blind_id}"
            )

        if compliance not in COMPLIANCE_LABELS:
            raise RuntimeError(
                f"{path.name}: bad compliance={compliance!r}"
            )

        if confidence not in CONFIDENCE_LABELS:
            raise RuntimeError(
                f"{path.name}: bad confidence={confidence!r}"
            )

        norms = parse_norm_labels(
            row.get("violated_norm_labels", "")
        )

        if compliance == "NONCOMPLIANT" and not norms:
            raise RuntimeError(
                f"{path.name}: {blind_id}: "
                "NONCOMPLIANT without norm label"
            )

        data[blind_id] = {
            "compliance": compliance,
            "norms": norms,
            "confidence": confidence,
            "reason": clean(row.get("brief_reason")),
        }

        order.append(blind_id)

    return order, data


def raw_agreement(
    a: list[str],
    b: list[str],
) -> float:
    return sum(x == y for x, y in zip(a, b)) / len(a)


def cohen_kappa(
    a: list[str],
    b: list[str],
    categories: list[str],
) -> float:
    n = len(a)

    observed = raw_agreement(a, b)

    ca = Counter(a)
    cb = Counter(b)

    expected = sum(
        (ca[c] / n) * (cb[c] / n)
        for c in categories
    )

    if expected == 1.0:
        return 1.0 if observed == 1.0 else float("nan")

    return (observed - expected) / (1.0 - expected)


def linear_weighted_kappa(
    a: list[str],
    b: list[str],
    categories: list[str],
) -> float:
    """
    Linear weighted Cohen's kappa for ordinal confidence:
    LOW < MEDIUM < HIGH.
    """
    k = len(categories)
    idx = {c: i for i, c in enumerate(categories)}
    n = len(a)

    observed_disagreement = 0.0

    for x, y in zip(a, b):
        observed_disagreement += (
            abs(idx[x] - idx[y]) / (k - 1)
        )

    observed_disagreement /= n

    ca = Counter(a)
    cb = Counter(b)

    expected_disagreement = 0.0

    for x in categories:
        for y in categories:
            weight = abs(idx[x] - idx[y]) / (k - 1)

            expected_disagreement += (
                weight
                * (ca[x] / n)
                * (cb[y] / n)
            )

    if expected_disagreement == 0.0:
        return (
            1.0
            if observed_disagreement == 0.0
            else float("nan")
        )

    return 1.0 - (
        observed_disagreement / expected_disagreement
    )


def jaccard(
    a: frozenset[str],
    b: frozenset[str],
) -> float:
    if not a and not b:
        return 1.0

    return len(a & b) / len(a | b)


def decision_signature(row: dict[str, Any]) -> tuple:
    return (
        row["compliance"],
        tuple(sorted(row["norms"])),
    )


def full_signature(row: dict[str, Any]) -> tuple:
    return (
        row["compliance"],
        tuple(sorted(row["norms"])),
        row["confidence"],
    )


def write_csv(
    path: Path,
    fields: list[str],
    rows: list[dict[str, Any]],
) -> None:
    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(rows)


def majority_of_others(
    model: str,
    blind_id: str,
    annotations: dict[str, dict[str, dict]],
) -> str | None:
    labels = [
        model_data[blind_id]["compliance"]
        for other_model, model_data in annotations.items()
        if other_model != model
    ]

    counts = Counter(labels)
    max_count = max(counts.values())

    winners = [
        label
        for label, count in counts.items()
        if count == max_count
    ]

    if len(winners) != 1:
        return None

    return winners[0]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    model_names = list(MODELS)

    annotations: dict[
        str,
        dict[str, dict[str, Any]],
    ] = {}

    reference_order: list[str] | None = None

    for model, path in MODELS.items():
        order, data = load_model(path)

        if reference_order is None:
            reference_order = order
        else:
            if set(order) != set(reference_order):
                raise RuntimeError(
                    f"{model}: blind-ID set mismatch"
                )

        annotations[model] = data

    assert reference_order is not None
    ids = reference_order

    #
    # Pairwise multilevel analysis
    #
    pairwise_rows = []

    for ma, mb in combinations(model_names, 2):
        da = annotations[ma]
        db = annotations[mb]

        comp_a = [da[i]["compliance"] for i in ids]
        comp_b = [db[i]["compliance"] for i in ids]

        conf_a = [da[i]["confidence"] for i in ids]
        conf_b = [db[i]["confidence"] for i in ids]

        comp_raw = raw_agreement(comp_a, comp_b)

        comp_kappa = cohen_kappa(
            comp_a,
            comp_b,
            COMPLIANCE_LABELS,
        )

        conf_raw = raw_agreement(conf_a, conf_b)

        conf_weighted_kappa = linear_weighted_kappa(
            conf_a,
            conf_b,
            CONFIDENCE_LABELS,
        )

        #
        # Conditional normative-basis agreement:
        # only cases where BOTH annotators say NONCOMPLIANT.
        #
        both_n_ids = [
            i
            for i in ids
            if (
                da[i]["compliance"] == "NONCOMPLIANT"
                and
                db[i]["compliance"] == "NONCOMPLIANT"
            )
        ]

        if both_n_ids:
            norm_exact_n = sum(
                da[i]["norms"] == db[i]["norms"]
                for i in both_n_ids
            )

            norm_exact_rate = (
                norm_exact_n / len(both_n_ids)
            )

            mean_norm_jaccard = sum(
                jaccard(
                    da[i]["norms"],
                    db[i]["norms"],
                )
                for i in both_n_ids
            ) / len(both_n_ids)

        else:
            norm_exact_n = 0
            norm_exact_rate = None
            mean_norm_jaccard = None

        #
        # Joint outcome + normative basis.
        #
        decision_exact_n = sum(
            decision_signature(da[i])
            == decision_signature(db[i])
            for i in ids
        )

        full_exact_n = sum(
            full_signature(da[i])
            == full_signature(db[i])
            for i in ids
        )

        #
        # Exact free-text identity is NOT a semantic agreement metric.
        # It is included only to establish whether outputs are literally
        # identical.
        #
        reason_exact_n = sum(
            normalize_reason(da[i]["reason"])
            == normalize_reason(db[i]["reason"])
            for i in ids
        )

        same_compliance_ids = [
            i
            for i in ids
            if da[i]["compliance"] == db[i]["compliance"]
        ]

        same_comp_same_conf_n = sum(
            da[i]["confidence"] == db[i]["confidence"]
            for i in same_compliance_ids
        )

        pairwise_rows.append(
            {
                "model_a": ma,
                "model_b": mb,
                "n_cases": len(ids),

                "compliance_raw_agreement":
                    comp_raw,

                "compliance_cohen_kappa":
                    comp_kappa,

                "confidence_exact_agreement":
                    conf_raw,

                "confidence_linear_weighted_kappa":
                    conf_weighted_kappa,

                "same_compliance_n":
                    len(same_compliance_ids),

                "same_compliance_same_confidence_n":
                    same_comp_same_conf_n,

                "same_compliance_same_confidence_rate":
                    (
                        same_comp_same_conf_n
                        / len(same_compliance_ids)
                        if same_compliance_ids
                        else None
                    ),

                "both_noncompliant_n":
                    len(both_n_ids),

                "violated_norm_exact_n":
                    norm_exact_n,

                "violated_norm_exact_rate":
                    norm_exact_rate,

                "violated_norm_mean_jaccard":
                    mean_norm_jaccard,

                "decision_basis_exact_n":
                    decision_exact_n,

                "decision_basis_exact_rate":
                    decision_exact_n / len(ids),

                "full_signature_exact_n":
                    full_exact_n,

                "full_signature_exact_rate":
                    full_exact_n / len(ids),

                "brief_reason_literal_exact_n":
                    reason_exact_n,

                "brief_reason_literal_exact_rate":
                    reason_exact_n / len(ids),
            }
        )

    pairwise_fields = [
        "model_a",
        "model_b",
        "n_cases",
        "compliance_raw_agreement",
        "compliance_cohen_kappa",
        "confidence_exact_agreement",
        "confidence_linear_weighted_kappa",
        "same_compliance_n",
        "same_compliance_same_confidence_n",
        "same_compliance_same_confidence_rate",
        "both_noncompliant_n",
        "violated_norm_exact_n",
        "violated_norm_exact_rate",
        "violated_norm_mean_jaccard",
        "decision_basis_exact_n",
        "decision_basis_exact_rate",
        "full_signature_exact_n",
        "full_signature_exact_rate",
        "brief_reason_literal_exact_n",
        "brief_reason_literal_exact_rate",
    ]

    write_csv(
        OUT / "pairwise_multilevel_agreement.csv",
        pairwise_fields,
        pairwise_rows,
    )

    #
    # Combined compliance matrix:
    # upper triangle = raw agreement
    # lower triangle = Cohen's kappa
    #
    pair_lookup = {}

    for row in pairwise_rows:
        pair_lookup[
            frozenset(
                [row["model_a"], row["model_b"]]
            )
        ] = row

    matrix_rows = []

    for r, row_model in enumerate(model_names):
        row = {
            "model": row_model,
        }

        for c, col_model in enumerate(model_names):
            if r == c:
                value = "?"

            else:
                pair = pair_lookup[
                    frozenset(
                        [row_model, col_model]
                    )
                ]

                if c > r:
                    value = (
                        f"{100 * pair['compliance_raw_agreement']:.1f}%"
                    )
                else:
                    value = (
                        f"{pair['compliance_cohen_kappa']:.3f}"
                    )

            row[col_model] = value

        matrix_rows.append(row)

    write_csv(
        OUT / "compliance_upper_raw_lower_kappa.csv",
        [
            "model",
            *model_names,
        ],
        matrix_rows,
    )

    #
    # Case-level multilevel agreement
    #
    case_rows = []

    for order, blind_id in enumerate(ids, start=1):
        rows = {
            model: annotations[model][blind_id]
            for model in model_names
        }

        comp_counts = Counter(
            row["compliance"]
            for row in rows.values()
        )

        conf_counts = Counter(
            row["confidence"]
            for row in rows.values()
        )

        decision_sigs = {
            decision_signature(row)
            for row in rows.values()
        }

        full_sigs = {
            full_signature(row)
            for row in rows.values()
        }

        noncompliant_norm_sets = {
            tuple(sorted(row["norms"]))
            for row in rows.values()
            if row["compliance"] == "NONCOMPLIANT"
        }

        max_comp = max(comp_counts.values())

        comp_winners = [
            label
            for label, count in comp_counts.items()
            if count == max_comp
        ]

        majority = (
            comp_winners[0]
            if len(comp_winners) == 1
            else "TIE"
        )

        out = {
            "order": order,
            "blind_id": blind_id,
            "majority_compliance": majority,
            "n_compliant":
                comp_counts["COMPLIANT"],
            "n_noncompliant":
                comp_counts["NONCOMPLIANT"],
            "n_uncertain":
                comp_counts["UNCERTAIN"],
            "n_high":
                conf_counts["HIGH"],
            "n_medium":
                conf_counts["MEDIUM"],
            "n_low":
                conf_counts["LOW"],
            "unique_compliance_labels":
                len(comp_counts),
            "unique_decision_basis_signatures":
                len(decision_sigs),
            "unique_full_signatures":
                len(full_sigs),
            "unique_noncompliant_norm_sets":
                len(noncompliant_norm_sets),
            "all_same_compliance":
                "YES"
                if len(comp_counts) == 1
                else "NO",
            "all_same_decision_basis":
                "YES"
                if len(decision_sigs) == 1
                else "NO",
            "all_same_full_signature":
                "YES"
                if len(full_sigs) == 1
                else "NO",
        }

        for model in model_names:
            out[f"{model}_compliance"] = (
                rows[model]["compliance"]
            )
            out[f"{model}_norms"] = (
                ";".join(
                    sorted(rows[model]["norms"])
                )
            )
            out[f"{model}_confidence"] = (
                rows[model]["confidence"]
            )

        case_rows.append(out)

    case_fields = [
        "order",
        "blind_id",
        "majority_compliance",
        "n_compliant",
        "n_noncompliant",
        "n_uncertain",
        "n_high",
        "n_medium",
        "n_low",
        "unique_compliance_labels",
        "unique_decision_basis_signatures",
        "unique_full_signatures",
        "unique_noncompliant_norm_sets",
        "all_same_compliance",
        "all_same_decision_basis",
        "all_same_full_signature",
    ]

    for model in model_names:
        case_fields.extend(
            [
                f"{model}_compliance",
                f"{model}_norms",
                f"{model}_confidence",
            ]
        )

    write_csv(
        OUT / "case_multilevel_summary.csv",
        case_fields,
        case_rows,
    )

    #
    # Leave-one-out majority agreement.
    # This avoids counting the target model's own vote in its reference.
    #
    loo_rows = []

    for model in model_names:
        comparable = 0
        agrees = 0
        disagreements = []
        ties = []

        for blind_id in ids:
            majority = majority_of_others(
                model,
                blind_id,
                annotations,
            )

            if majority is None:
                ties.append(blind_id)
                continue

            comparable += 1

            own = annotations[model][blind_id][
                "compliance"
            ]

            if own == majority:
                agrees += 1
            else:
                disagreements.append(blind_id)

        loo_rows.append(
            {
                "model": model,
                "comparable_cases": comparable,
                "agreement_n": agrees,
                "agreement_rate": (
                    agrees / comparable
                    if comparable
                    else None
                ),
                "excluded_other_rater_ties":
                    len(ties),
                "disagreement_n":
                    len(disagreements),
                "disagreement_blind_ids":
                    ";".join(disagreements),
            }
        )

    write_csv(
        OUT / "leave_one_out_majority_agreement.csv",
        [
            "model",
            "comparable_cases",
            "agreement_n",
            "agreement_rate",
            "excluded_other_rater_ties",
            "disagreement_n",
            "disagreement_blind_ids",
        ],
        loo_rows,
    )

    #
    # Focused Copilot vs Gemini report
    #
    focus_pair = next(
        row
        for row in pairwise_rows
        if {
            row["model_a"],
            row["model_b"],
        }
        == {
            "Microsoft365-Copilot",
            "Gemini3.6-Flash",
        }
    )

    (OUT / "copilot_gemini_summary.json").write_text(
        json.dumps(
            focus_pair,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=== Multilevel six-model agreement ===")
    print(f"Cases: {len(ids)}")
    print()

    print("Copilot vs Gemini:")
    print(
        "  compliance raw       = "
        f"{focus_pair['compliance_raw_agreement']:.4f}"
    )
    print(
        "  compliance kappa     = "
        f"{focus_pair['compliance_cohen_kappa']:.4f}"
    )
    print(
        "  confidence exact     = "
        f"{focus_pair['confidence_exact_agreement']:.4f}"
    )
    print(
        "  confidence weighted È= "
        f"{focus_pair['confidence_linear_weighted_kappa']:.4f}"
    )
    print(
        "  both NONCOMPLIANT    = "
        f"{focus_pair['both_noncompliant_n']}"
    )
    print(
        "  norm-set exact       = "
        f"{focus_pair['violated_norm_exact_n']}/"
        f"{focus_pair['both_noncompliant_n']}"
    )
    print(
        "  norm-set Jaccard     = "
        f"{focus_pair['violated_norm_mean_jaccard']}"
    )
    print(
        "  decision+basis exact = "
        f"{focus_pair['decision_basis_exact_n']}/41"
    )
    print(
        "  full signature exact = "
        f"{focus_pair['full_signature_exact_n']}/41"
    )
    print(
        "  literal reason exact = "
        f"{focus_pair['brief_reason_literal_exact_n']}/41"
    )

    print()
    print("Leave-one-out majority:")
    for row in loo_rows:
        print(
            f"  {row['model']:24s} "
            f"{row['agreement_n']:2d}/"
            f"{row['comparable_cases']:2d} "
            f"({100 * row['agreement_rate']:.1f}%)"
        )

    print()
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()

    
