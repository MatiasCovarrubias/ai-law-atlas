"""Formal validation of machine annotations by a stronger model (protocol: docs/VALIDATION_PROTOCOL.md).

    python tools/validation.py sample  <run> --n 15 --seed 20261014 --prompt 1.3-718f7c3f
    python tools/validation.py blind   <run>            # after re-annotating the sample into validation/<run>/current/
    python tools/validation.py report  <run>            # after the checkers wrote validation/<run>/verdicts/

Layout of validation/<run>/ (committed): sample.json (frame, seed, strata, ids), blind/<id>.X.json and .Y.json
(identifying fields stripped), reference/ and verdicts/ (written by checkers), key.json (which of X/Y is which
version; kept in build/validation/<run>/ until the report is made, then copied here), report.md.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from quote_check import TEXTS  # noqa: E402

REG = {json.loads(l)["instrument_id"]: json.loads(l) for l in (ROOT / "registry" / "instruments.jsonl").open()}
ANN = ROOT / "annotations"
SECTIONS = ["thresholds", "events", "obligations", "enforcement"]
VERDICTS = ["correct", "wrong_value", "wrong_type", "wrong_addressee", "wrong_date", "unsupported", "duplicate",
            "not_this_instrument"]
STRIP_ANNOTATOR = True


def region(iid: str) -> str:
    r = REG[iid].get("priority_region", "")
    return r if r in ("P1-US", "P1-Europe", "P1-LATAM", "P1-China") else "other"


def cmd_sample(run: Path, n: int, seed: int, prompt: str, tier: str = "A") -> None:
    checked = {p.stem for p in (ROOT / "build" / "rollout" / "spotcheck").glob("*.json")}
    frame = []
    for p in sorted(ANN.glob("*.json")):
        a = json.loads(p.read_text())
        if (a["tier"] == tier and a["validation"] == "machine" and a["annotator"].get("prompt_version") == prompt
                and a["instrument_id"] not in checked):
            frame.append(a["instrument_id"])
    strata = defaultdict(list)
    for i in frame:
        size = "long" if len((TEXTS / f"{i}.txt").read_text(encoding="utf-8")) > 80_000 else "short"
        strata[(region(i), size)].append(i)
    rng = random.Random(seed)
    # proportional allocation with at least one per stratum that has 3+ laws, largest remainders
    alloc = {k: n * len(v) / len(frame) for k, v in strata.items()}
    take = {k: max(1 if len(strata[k]) >= 3 else 0, int(x)) for k, x in alloc.items()}
    while sum(take.values()) < n:
        k = max(alloc, key=lambda k: alloc[k] - take[k])
        take[k] += 1
    while sum(take.values()) > n:
        k = max(take, key=lambda k: take[k] - alloc[k])
        take[k] -= 1
    ids = sorted(i for k, v in sorted(strata.items()) for i in rng.sample(sorted(v), min(take[k], len(v))))
    run.mkdir(parents=True, exist_ok=True)
    (run / "sample.json").write_text(json.dumps({
        "created": date.today().isoformat(), "seed": seed, "n": len(ids),
        "frame": f"Tier {tier} machine annotations with prompt_version {prompt}, not previously spot-checked",
        "frame_size": len(frame), "strata": {f"{a}/{b}": {"frame": len(v), "sampled": take[(a, b)]}
                                             for (a, b), v in sorted(strata.items())},
        "ids": ids}, indent=1))
    (run / "ids.txt").write_text("\n".join(ids) + "\n")
    print(f"frame {len(frame)}; sampled {len(ids)} -> {run}/sample.json")


def blinded(a: dict) -> dict:
    b = json.loads(json.dumps(a))
    for k in ("annotator", "validation"):
        b.pop(k, None)
    b.get("text_version", {}).pop("note", None)
    for e in b.get("events", []):  # provenance of computed dates is shown to both versions alike
        pass
    return b


def cmd_blind(run: Path, seed: int) -> None:
    ids = json.loads((run / "sample.json").read_text())["ids"]
    rng = random.Random(seed + 1)
    key, out = {}, run / "blind"
    out.mkdir(exist_ok=True)
    for i in ids:
        old = json.loads((ANN / f"{i}.json").read_text())
        new = json.loads((run / "current" / f"{i}.json").read_text())
        pair = [("old", old), ("new", new)]
        rng.shuffle(pair)
        key[i] = {"X": pair[0][0], "Y": pair[1][0],
                  "versions": {"old": old["annotator"].get("prompt_version"),
                               "new": new["annotator"].get("prompt_version")}}
        for label, (_, a) in zip("XY", pair):
            (out / f"{i}.{label}.json").write_text(json.dumps(blinded(a), ensure_ascii=False, indent=1))
    hidden = ROOT / "build" / "validation" / run.name
    hidden.mkdir(parents=True, exist_ok=True)
    (hidden / "key.json").write_text(json.dumps(key, indent=1))
    print(f"{len(ids)} pairs blinded -> {out}; key kept in {hidden}/key.json until the report")


def wilson(k: int, n: int) -> str:
    if n == 0:
        return "–"
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:.0f}% [{100 * (c - h):.0f}–{100 * (c + h):.0f}]"


def cmd_report(run: Path) -> None:
    key = json.loads((ROOT / "build" / "validation" / run.name / "key.json").read_text())
    shutil.copy2(ROOT / "build" / "validation" / run.name / "key.json", run / "key.json")
    sample = json.loads((run / "sample.json").read_text())
    prec = {v: defaultdict(Counter) for v in ("old", "new")}   # version -> section -> verdict counts
    per_law = defaultdict(dict)                                 # id -> version -> (correct, judged)
    rec = {v: Counter() for v in ("old", "new")}                # version -> found / reference items
    rec_cat = {v: defaultdict(Counter) for v in ("old", "new")}
    cls = {v: Counter() for v in ("old", "new")}
    missing = []
    for i in sample["ids"]:
        vp = run / "verdicts" / f"{i}.json"
        if not vp.exists():
            missing.append(i)
            continue
        v = json.loads(vp.read_text())
        for label in "XY":
            ver = key[i][label]
            judged = v["judgments"][label]
            ok = tot = 0
            for sec in SECTIONS:
                for it in judged.get(sec, []):
                    prec[ver][sec][it["verdict"]] += 1
                    tot += 1
                    ok += it["verdict"] == "correct"
            per_law[i][ver] = (ok, tot)
            c = v.get("classification", {}).get(label, {})
            cls[ver][c.get("domains", "no")] += 1
        for r in v["reference"]:
            for label in "XY":
                ver = key[i][label]
                hit = r.get("match", {}).get(label) is not None
                rec[ver]["found"] += hit
                rec[ver]["total"] += 1
                rec_cat[ver][r["category"]]["found"] += hit
                rec_cat[ver][r["category"]]["total"] += 1
    vers = {k: key[sample["ids"][0]]["versions"][k] for k in ("old", "new")}
    lines = [f"# Validation report: {run.name}", "",
             f"Protocol `docs/VALIDATION_PROTOCOL.md`. Sample: {sample['n']} laws ({sample['frame']}; frame "
             f"{sample['frame_size']}; seed {sample['seed']}). Versions: old = `{vers['old']}`, new = `{vers['new']}`. "
             f"Checker judgments were blind to version. Missing verdicts: {', '.join(missing) or 'none'}.", "",
             "## Precision (items judged correct; 95% Wilson interval, items pooled across laws)", "",
             "| Section | old | new |", "|---|---|---|"]
    for sec in SECTIONS:
        o, n = prec["old"][sec], prec["new"][sec]
        lines.append(f"| {sec} | {wilson(o['correct'], sum(o.values()))} (n={sum(o.values())}) | "
                     f"{wilson(n['correct'], sum(n.values()))} (n={sum(n.values())}) |")
    allo = sum((prec["old"][s] for s in SECTIONS), Counter())
    alln = sum((prec["new"][s] for s in SECTIONS), Counter())
    lines.append(f"| all four | {wilson(allo['correct'], sum(allo.values()))} | {wilson(alln['correct'], sum(alln.values()))} |")
    lines += ["", "## Recall against the checker's independent reference list", "",
              "| Category | old | new |", "|---|---|---|"]
    for cat in sorted(set(rec_cat["old"]) | set(rec_cat["new"])):
        o, n = rec_cat["old"][cat], rec_cat["new"][cat]
        lines.append(f"| {cat} | {wilson(o['found'], o['total'])} (n={o['total']}) | {wilson(n['found'], n['total'])} |")
    lines.append(f"| all | {wilson(rec['old']['found'], rec['old']['total'])} | {wilson(rec['new']['found'], rec['new']['total'])} |")
    lines += ["", "## Error types (share of judged items)", "", "| Verdict | old | new |", "|---|---|---|"]
    for vd in VERDICTS[1:]:
        lines.append(f"| {vd} | {allo[vd]} ({100 * allo[vd] / max(1, sum(allo.values())):.0f}%) | "
                     f"{alln[vd]} ({100 * alln[vd] / max(1, sum(alln.values())):.0f}%) |")
    diffs = [per_law[i]["new"][0] / max(1, per_law[i]["new"][1]) - per_law[i]["old"][0] / max(1, per_law[i]["old"][1])
             for i in per_law if "new" in per_law[i] and "old" in per_law[i]]
    better = sum(d > 0.02 for d in diffs)
    worse = sum(d < -0.02 for d in diffs)
    mean = sum(diffs) / len(diffs) if diffs else 0
    lines += ["", "## Paired comparison by law", "",
              f"Precision difference new − old, per law: mean {100 * mean:+.1f} points; new better on {better}, "
              f"worse on {worse}, about equal on {len(diffs) - better - worse} of {len(diffs)} laws.", "",
              "| id | old correct/judged | new correct/judged |", "|---|---|---|"]
    for i in sorted(per_law):
        o, n = per_law[i].get("old", (0, 0)), per_law[i].get("new", (0, 0))
        lines.append(f"| {i} | {o[0]}/{o[1]} | {n[0]}/{n[1]} |")
    lines += ["", "## Classification (domains judged right / partly / wrong)", "",
              f"old: {dict(cls['old'])}; new: {dict(cls['new'])}", ""]
    (run / "report.md").write_text("\n".join(lines))
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "blind", "report"])
    ap.add_argument("run")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--prompt", default="1.3-718f7c3f")
    ap.add_argument("--tier", default="A")
    args = ap.parse_args()
    run = ROOT / "validation" / args.run
    {"sample": lambda: cmd_sample(run, args.n, args.seed, args.prompt, args.tier),
     "blind": lambda: cmd_blind(run, args.seed),
     "report": lambda: cmd_report(run)}[args.cmd]()


if __name__ == "__main__":
    main()
