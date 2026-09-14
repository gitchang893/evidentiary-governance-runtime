# scripts/inventory_e2_blind_structure.py
from __future__ import annotations

import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(".").resolve()
E2_ZIP = ROOT / "e2-llm-main.zip"
SCENARIOS_ZIP = ROOT / "scenarios.zip"
OUT = ROOT / "e2-blind-structure.txt"

COMPLIANCE_SUFFIX = "artifacts/e2-llm-main/e2-proposal-compliance.json"


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def collect_paths(
    value: Any,
    prefix: str,
    out: dict[str, set[str]],
    *,
    max_depth: int = 8,
    depth: int = 0,
) -> None:
    if depth > max_depth:
        out[prefix].add("<max-depth>")
        return

    out[prefix].add(type_name(value))

    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            collect_paths(
                child,
                path,
                out,
                max_depth=max_depth,
                depth=depth + 1,
            )

    elif isinstance(value, list):
        item_path = f"{prefix}[]"
        if not value:
            out[item_path].add("<empty>")
            return

        for child in value:
            collect_paths(
                child,
                item_path,
                out,
                max_depth=max_depth,
                depth=depth + 1,
            )


def render_paths(title: str, paths: dict[str, set[str]], lines: list[str]) -> None:
    lines.append("")
    lines.append(f"=== {title} ===")
    for path in sorted(paths):
        types = ",".join(sorted(paths[path]))
        lines.append(f"{path}: {types}")


def inspect_e2(lines: list[str]) -> None:
    if not E2_ZIP.exists():
        raise SystemExit(f"Missing: {E2_ZIP}")

    with zipfile.ZipFile(E2_ZIP) as zf:
        names = zf.namelist()
        matches = [n for n in names if n.endswith(COMPLIANCE_SUFFIX)]

        if len(matches) != 1:
            raise SystemExit(
                f"Expected exactly one compliance JSON, found {len(matches)}: {matches}"
            )

        obj = json.loads(zf.read(matches[0]).decode("utf-8"))

    if not isinstance(obj, dict):
        raise SystemExit("Compliance artifact is not a JSON object.")

    lines.append("=== E2 COMPLIANCE TOP LEVEL ===")
    for key in sorted(obj):
        value = obj[key]
        if isinstance(value, list):
            lines.append(f"{key}: list len={len(value)}")
        elif isinstance(value, dict):
            lines.append(f"{key}: dict keys={len(value)}")
        else:
            lines.append(f"{key}: {type_name(value)}")

    proposals = obj.get("proposals")
    if not isinstance(proposals, list):
        raise SystemExit("Expected top-level 'proposals' list.")

    proposal_paths: dict[str, set[str]] = defaultdict(set)
    for proposal in proposals:
        collect_paths(proposal, "proposal", proposal_paths)

    render_paths(
        f"E2 PROPOSALS STRUCTURE (n={len(proposals)})",
        proposal_paths,
        lines,
    )


def inspect_scenarios(lines: list[str]) -> None:
    if not SCENARIOS_ZIP.exists():
        raise SystemExit(f"Missing: {SCENARIOS_ZIP}")

    all_paths: dict[str, set[str]] = defaultdict(set)
    per_file_top_keys: dict[str, list[str]] = {}

    with zipfile.ZipFile(SCENARIOS_ZIP) as zf:
        yaml_names = sorted(
            n
            for n in zf.namelist()
            if n.startswith("scenarios/canonical/")
            and n.endswith((".yaml", ".yml"))
        )

        if not yaml_names:
            raise SystemExit("No canonical scenario YAML files found.")

        for name in yaml_names:
            obj = yaml.safe_load(zf.read(name).decode("utf-8"))

            if not isinstance(obj, dict):
                raise SystemExit(f"{name}: expected YAML mapping.")

            per_file_top_keys[name] = sorted(map(str, obj.keys()))
            collect_paths(obj, "scenario", all_paths)

    lines.append("")
    lines.append("=== SCENARIO TOP-LEVEL KEYS BY FILE ===")
    for name, keys in per_file_top_keys.items():
        lines.append(f"{name}")
        lines.append("  " + ", ".join(keys))

    render_paths(
        f"ALL SCENARIO KEY PATHS (files={len(per_file_top_keys)})",
        all_paths,
        lines,
    )


def main() -> None:
    lines: list[str] = [
        "E2 blind-validation structure inventory",
        f"root={ROOT}",
        "",
        "NOTE: This report contains key names and value types only.",
        "No scalar values are intentionally emitted.",
    ]

    inspect_e2(lines)
    inspect_scenarios(lines)

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    main()
