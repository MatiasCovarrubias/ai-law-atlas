"""Re-apply the current deterministic rules to machine annotations of a run, in place.

    python tools/refix.py build/rollout/tierA [build/rollout/tierB ...]

For files with validation "machine": annotate.recompute_dates (current date rules) and annotate.fix_bookkeeping.
Spot-checked or validated files are left alone (a checker's edits win). Prints files changed and any that still
fail validation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotate import REGISTRY, fix_bookkeeping, recompute_dates  # noqa: E402
from validate import validate  # noqa: E402


def main() -> None:
    for run in map(Path, sys.argv[1:]):
        changed, bad = 0, []
        for p in sorted(run.glob("*.json")):
            if p.name.endswith(".log.json") or p.name.startswith(("score", "_")):
                continue
            a = json.loads(p.read_text())
            if a.get("validation") != "machine":
                continue
            before = json.dumps(a, sort_keys=True)
            recompute_dates(a, REGISTRY.get(a["instrument_id"], {}))
            fix_bookkeeping(a)
            if json.dumps(a, sort_keys=True) != before:
                p.write_text(json.dumps(a, ensure_ascii=False, indent=1))
                changed += 1
            if errs := validate(p):
                bad.append(f"{p.stem}: {errs[0]}")
        print(f"{run}: {changed} changed, {len(bad)} invalid")
        for b in bad:
            print("  ", b)


if __name__ == "__main__":
    main()
