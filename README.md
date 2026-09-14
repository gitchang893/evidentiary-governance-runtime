# Evidentiary Governance for LLM Agents: replication materials

This repository accompanies *Evidentiary Governance for LLM Agents: Runtime Control and Proposal-Level Evaluation* by Hiroshi G. Okuno and Mayumi J. Okuno (submitted to JURIX 2026).

**Status:** The repository contains the governance runtime, configurations and scenarios, archived E1 summary CSVs, E2 execution CSVs, blinded LLM-annotator judgments, and scripts for recalculating the paper's four tables. E1 can be rerun locally. Full per-trial evidence records, additional tests, and further analysis outputs are being prepared for a later addition.

## Recalculate the tables from archived results

From the repository root, run:

```bash
python3 reproduce_paper_tables.py
```

This command uses only Python 3.11+ standard-library modules. It checks counts, case mappings and selected SHA-256 digests, then prints Tables 1–4. A successful run ends with `All paper-table checks passed.` These values are relative to the runtime's encoded norms, not an independent determination that the norms represent the correct legal or organizational duties.

## Re-execute the fixed-proposal E1 experiment

Install Python 3.11 or newer and the package dependencies, then run E1 to a fresh output directory:

```bash
python3 -m pip install -e .
python3 -m eg_runtime.e1_experiment --config configs/e1-governance-comparison.json --output /tmp/egr-e1-rerun
```

On Windows, substitute a writable directory of your choice for `/tmp/egr-e1-rerun`. The configuration names 16 of the 20 canonical scenarios and runs them under C0–C3 for five repetitions (320 trials). Compare the five new `repetition-XX/summary.csv` files with those under `results/e1-governance-comparison-2026-07-27/`; trial identifiers and hashes generated per run can vary.

## Examine E2 and the blinded assessments

| Location | Contents |
| --- | --- |
| `artifacts/e2-llm-main/` | Execution CSVs for 41 executable generated proposals and generation-failure CSVs for seven failed attempts over three repetitions. |
| `artifacts/e2-blind-validation/` | Four batches of blinded cases; original and revised instructions; six annotators' original and revised judgments on 41 cases; `researcher_blind_key.csv`. |
| `artifacts/e2-blind-validation/clarified-spec-v2/` | Twelve selected, reblinded clarification cases, six annotators' judgments, and `researcher_v2_key.csv`, which links clarified IDs to original blind IDs. |
| `artifacts/e2-blind-validation/agreement-6models-v2-unblind/` | Manifest containing checksums for selected unblinding inputs. |

The researcher keys were held apart from the materials shown to the annotators. In the 41-case comparisons, A uses the original instructions and B uses revised instructions. On the matched 12 cases, I uses original judgments, II uses judgments under revised instructions, and III uses revised instructions with clarified norm representations. The 12 cases were selected after examining earlier results; their agreement rates do not estimate a population-wide effect of clarification.

The included scripts recalculate further comparisons in fresh output directories:

```bash
python3 scripts/analyze_unblind_model_agreement.py --version v1 --output-dir /tmp/egr-unblind-v1
python3 scripts/analyze_unblind_model_agreement.py --version v2 --output-dir /tmp/egr-unblind-v2
python3 scripts/analyze_alignment_predicate.py --version v2 --input /tmp/egr-unblind-v2/case_unblind_summary.csv --output-dir /tmp/egr-alignment-v2
```

The E2 generation configuration is `configs/e2-llm-main.json`. Running `python3 -m eg_runtime.e2_experiment` requires a local Ollama server and the corresponding Qwen model; the model tag and archived configuration alone do not freeze model weights or server behavior. Hosted annotator services may also change. Thus, identical E2 proposals or new LLM judgments cannot be guaranteed; recalculation from archived records does not call those services.

## Citation and terms

Please cite the paper when using these materials. A complete publication citation is not yet available. No software or data license has been granted in this repository at this stage.
