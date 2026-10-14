"""Merge several runs of the same configuration into a consensus annotation (majority agreement).

    python tools/consensus.py build/pilot/<run1> build/pilot/<run2> build/pilot/<run3> --out build/pilot/<run_cons>

Fact sections: items are clustered across runs with the first matching rule of tools/score_pilot.py (same event
and date, same threshold value, same obligation provision, ...); a cluster is kept if at least `--min` runs (default
2) produced it, and its first member is the representative. Items without a matching key (e.g., undated events)
are kept only if they come from the first run. Classification lists keep labels chosen by at least `--min` runs;
summary and single-valued fields come from the first run. The log sums the cost of all runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from annotate import fix_bookkeeping  # noqa: E402
from score_pilot import SECTIONS  # noqa: E402
from validate import validate  # noqa: E402

LISTS = ["domains", "mechanisms", "decision_margins", "addressee_roles", "affected_roles"]


def merge(anns: list[dict], k: int) -> dict:
    out = json.loads(json.dumps(anns[0]))
    for sec, (keyed, rules) in SECTIONS.items():
        same = next(iter(rules.values()))
        pools = [list(a.get(sec, [])) for a in anns]
        keyed_ids = [{id(x) for x in keyed(a)} for a in anns]
        kept, used = [], [set() for _ in anns]
        for i, pool in enumerate(pools):
            for x_i, x in enumerate(pool):
                if x_i in used[i]:
                    continue
                if id(x) not in keyed_ids[i]:  # no key to match on: keep first-run items only
                    if i == 0:
                        kept.append(x)
                    continue
                members = 1
                used[i].add(x_i)
                for j in range(len(anns)):
                    if j == i:
                        continue
                    for y_i, y in enumerate(pools[j]):
                        if y_i not in used[j] and id(y) in keyed_ids[j] and same(x, y):
                            used[j].add(y_i)
                            members += 1
                            break
                if members >= k:
                    kept.append(x)
        out[sec] = kept
    c = out["classification"]
    for f in LISTS:
        votes = Counter(v for a in anns for v in set(a.get("classification", {}).get(f, [])))
        c[f] = [v for v, n in votes.items() if n >= k] or c.get(f, [])
    out["annotator"]["method"] += f"; consensus {k} of {len(anns)}"
    fix_bookkeeping(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min", type=int, default=2)
    args = ap.parse_args()
    runs, out = [Path(r) for r in args.runs], Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ids = sorted({p.stem for p in runs[0].glob("*.json") if not p.name.endswith(".log.json")
                  and not p.name.startswith("score")})
    for iid in ids:
        paths = [r / f"{iid}.json" for r in runs]
        if not all(p.exists() for p in paths):
            print(f"{iid}: missing in some runs, skipped")
            continue
        merged = merge([json.loads(p.read_text()) for p in paths], args.min)
        path = out / f"{iid}.json"
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=1))
        usd = sum(json.loads((r / f"{iid}.log.json").read_text()).get("usd", 0) for r in runs)
        log = {"instrument_id": iid, "usd": usd, "errors": validate(path), "quote_repairs": {}}
        (out / f"{iid}.log.json").write_text(json.dumps(log, indent=1))
        print(f"{iid}: ${usd:.4f}, errors {len(log['errors'])}")


if __name__ == "__main__":
    main()
