"""Copy machine annotations from a rollout run into annotations/.

    python tools/promote.py build/rollout/tierA [--dry-run] [--replace-versions V1,V2]

First re-applies, in place in the run, the current date rules (annotate.recompute_dates; machine files only)
and the deterministic fixes of annotate.fix_bookkeeping. Copies
<run>/<id>.json to annotations/<id>.json when: the file validates with no errors (tools/validate.py), and
annotations/<id>.json does not exist yet. Existing annotations (the gold set, earlier promotions, hand edits) are
never overwritten, except with --replace-versions: then an existing annotation is replaced if it is a machine
annotation (validation "machine") whose prompt_version is in the list. Gold, spot-checked and validated files are
never replaced. Prints what was copied and what was held back, and why.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from annotate import REGISTRY, fix_bookkeeping, recompute_dates  # noqa: E402
from validate import validate  # noqa: E402

ANN = ROOT / "annotations"


def main() -> None:
    run = Path(sys.argv[1])
    dry = "--dry-run" in sys.argv
    replace = set(sys.argv[sys.argv.index("--replace-versions") + 1].split(",")) if "--replace-versions" in sys.argv else set()
    counts, held = Counter(), []
    for path in sorted(run.glob("*.json")):
        if path.name.endswith(".log.json") or path.name.startswith(("score", "_")):
            continue
        dest = ANN / path.name
        if dest.exists():
            old = json.loads(dest.read_text())
            if not (old.get("validation") == "machine" and old["annotator"].get("prompt_version") in replace):
                counts["exists, kept"] += 1
                continue
            counts["replaced"] += 1
        a = json.loads(path.read_text())
        before = json.dumps(a, sort_keys=True)
        if a.get("validation") == "machine":  # spot-checked files keep the checker's dates
            recompute_dates(a, REGISTRY.get(a["instrument_id"], {}))
        fix_bookkeeping(a)
        if json.dumps(a, sort_keys=True) != before and not dry:  # deterministic fixes added after the run was made
            path.write_text(json.dumps(a, ensure_ascii=False, indent=1))
        errs = validate(path)
        if errs:
            counts["validation errors"] += 1
            held.append(f"{path.stem}: {errs[0]}" + (f" (+{len(errs) - 1} more)" if len(errs) > 1 else ""))
            continue
        if not dry:
            shutil.copy2(path, dest)
        counts["copied" if not dry else "would copy"] += 1
    print(dict(counts))
    for h in held:
        print("  held:", h)


if __name__ == "__main__":
    main()
