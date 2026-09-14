# scripts/inventory_e2_for_blind_validation.py
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
ARTIFACT_ZIP = ROOT / "e2-llm-main.zip"
SCENARIO_ZIP = ROOT / "scenarios.zip"
OUT = ROOT / "e2-blind-inventory.txt"

def summarize_json(obj: Any) -> str:
    if isinstance(obj, dict):
        return "dict keys=" + ", ".join(sorted(map(str, obj.keys())))
    if isinstance(obj, list):
        if not obj:
            return "list len=0"
        first = obj[0]
        if isinstance(first, dict):
            return (
                f"list len={len(obj)}; first keys="
                + ", ".join(sorted(map(str, first.keys())))
            )
        return f"list len={len(obj)}; first_type={type(first).__name__}"
    return type(obj).__name__

def inspect_zip(path: Path, lines: list[str]) -> None:
    if not path.exists():
        lines.append(f"MISSING: {path}")
        return

    lines.append(f"\n=== {path.name} ===")
    with zipfile.ZipFile(path) as zf:
        names = sorted(zf.namelist())
        lines.append(f"member_count={len(names)}")

        for name in names:
            if name.endswith("/"):
                continue

            lines.append(f"\n{name}")

            suffix = Path(name).suffix.lower()
            if suffix not in {".json", ".jsonl", ".yaml", ".yml"}:
                continue

            try:
                raw = zf.read(name).decode("utf-8")
            except Exception as exc:
                lines.append(f"  READ_ERROR: {exc}")
                continue

            if suffix == ".json":
                try:
                    obj = json.loads(raw)
                    lines.append("  " + summarize_json(obj))
                except Exception as exc:
                    lines.append(f"  JSON_ERROR: {exc}")

            elif suffix == ".jsonl":
                rows = [x for x in raw.splitlines() if x.strip()]
                lines.append(f"  jsonl_rows={len(rows)}")
                if rows:
                    try:
                        obj = json.loads(rows[0])
                        lines.append("  first_row: " + summarize_json(obj))
                    except Exception as exc:
                        lines.append(f"  JSONL_ERROR: {exc}")

            else:
                # YAML content itself is not printed, to avoid leaking labels/decisions.
                nonempty = [
                    ln for ln in raw.splitlines()
                    if ln.strip() and not ln.lstrip().startswith("#")
                ]
                lines.append(f"  yaml_nonempty_lines={len(nonempty)}")

def main() -> None:
    lines: list[str] = []
    lines.append("E2 blind-validation inventory")
    lines.append(f"root={ROOT}")

    inspect_zip(ARTIFACT_ZIP, lines)
    inspect_zip(SCENARIO_ZIP, lines)

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {OUT}")

if __name__ == "__main__":
    main()
