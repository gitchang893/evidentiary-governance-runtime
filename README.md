# Evidentiary Governance for LLM Agents: replication materials

This repository accompanies *Evidentiary Governance for LLM Agents: Runtime Control and Proposal-Level Evaluation* by Hiroshi G. Okuno and Mayumi J. Okuno (submitted to JURIX 2026).

**Status:** This first set of materials supports recalculation of the paper's four tables from archived summaries, execution records, and LLM-annotator judgments. The runtime code, scenario definitions, full evidence records, and instructions for rerunning E1 and E2 are being prepared separately. Their absence limits this first set to analysis of recorded outputs.

## Recalculate the tables

Download the repository with its directory structure intact and, from its root, run:

```bash
python3 reproduce_paper_tables.py
```

The script uses only the Python 3.11+ standard library. It reads the archived files without changing them, checks counts and selected SHA-256 hashes, and prints Tables 1–4. A successful run ends with `All paper-table checks passed.` The values are recalculated relative to the runtime's encoded norms; the script does not independently establish whether those norms correctly capture legal or organizational duties.

## What is included in this first set

| Location | Contents |
| --- | --- |
| `results/e1-governance-comparison-2026-07-27/` | Summary CSVs for five repetitions of 16 fixed scenarios under four runtime conditions (320 trials). These summaries reproduce Table 1 but do not allow trial-level replay. |
| `artifacts/e2-llm-main/` | Per-condition execution CSVs for 41 valid generated proposals and CSVs recording seven generation failures across three repetitions. These reproduce Table 2 when joined to the researcher key. |
| `artifacts/e2-blind-validation/` | Four batches of blinded cases, original and revised annotation instructions, six annotators' original and revised judgments on 41 cases, and `researcher_blind_key.csv`. |
| `artifacts/e2-blind-validation/clarified-spec-v2/` | The 12 selected, reblinded clarification cases, six annotators' judgments, and `researcher_v2_key.csv`, which links clarified IDs to original blind IDs. |
| `artifacts/e2-blind-validation/agreement-6models-v2-unblind/` | A manifest with checksums for selected unblinding inputs. |
| `reproduce_paper_tables.py` | Read-only recalculation and consistency checks for the four tables. |

The researcher keys were held apart from the materials shown to annotators when they judged the cases. In the 41-case comparisons, A uses the original instructions and B uses revised instructions. In the matched 12-case comparison, I uses the original judgments, II uses judgments under revised instructions, and III uses revised instructions together with clarified norm representations. The 12 cases were selected after inspection of earlier results and do not estimate a population-wide effect of clarification.

The script recalculates results from saved records. Regenerating the 41 proposals and repeating hosted LLM annotations could yield different results; neither is part of this first set. Additional runtime code, tests, configurations, and execution instructions will be documented when added.

## Citation and terms

Please cite the paper when using these materials. The paper has been submitted and a complete publication citation is not yet available. No software or data license has been granted in this repository at this stage.
