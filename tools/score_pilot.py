"""Score machine annotations against the gold set (annotations/*.json written by Opus in Claude Code).

    python tools/score_pilot.py build/pilot/<run> [build/pilot/<run2> ...] [--ids id ...]

For each run directory (as written by tools/annotate.py) and each instrument with a gold file, items are matched
section by section and precision / recall are pooled across instruments (micro-averaged):

    events       same event type and same date (at their common precision: 2026-09 matches 2026-09-22); dated events only. Gold events with date_type "external" are
                 excluded (they come from browsing, which the annotator cannot do)
    thresholds   same numeric value (and value_max); "+var" also requires the same variable
    obligations  "match" (headline): same quoted passage, or same article and same obligation type.
                 "quote": quotes of the same passage (token overlap >= 60% of the shorter quote); "+type" / "+addressee" also
                 require the same obligation type / addressee role; "provision" = same article number (brittle:
                 annotators label provisions inconsistently)
    enforcement  same sanction type; amounts compared where both state one
    definitions  same term (normalised; containment counts)
    relations    same target instrument, or same relation type and overlapping target text
    classification  Jaccard overlap of domains, mechanisms, decision_margins, addressee_roles; exact
                 primary_domain and binding_force
Entities are reported as counts only: codebook v1.2 now counts designated public authorities, which most gold
files (written under v1.1) do not.

Writes <run>/score.md and prints a comparison table across runs. Matching is deliberately simple and
transparent; read the per-instrument rows before trusting small differences.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "annotations"


def norm(s) -> str:
    return " ".join(re.findall(r"\w+", str(s or "").lower()))


def main_number(prov: str) -> str | None:
    """Article- or section-level key: 'Art. 4(1)' -> '4'; 'Sec. 151.359(d)(2)' -> '151.359';
    '775 ILCS 5/2-102(L)(1)' and 'Sec. 2-102(L)' -> '2-102'; '§ 11 Abs. 1 Nr. 2' -> '11'."""
    p = re.sub(r"\([^)]*\)", "", str(prov or ""))
    toks = re.findall(r"\d+(?:[./-]\d+)*", p)
    if not toks:
        return norm(p)[:20] or None
    key = next((t for t in toks if re.search(r"[./-]", t)), toks[0])
    return key.split("/")[-1]


def overlap(a: str, b: str) -> int:
    """Shared words, plus shared character pairs for CJK text (no spaces between words)."""
    def grams(s):
        s = str(s or "").lower()
        cjk = re.findall(r"[\u4e00-\u9fff]+", s)
        return set(re.findall(r"[a-z0-9]{3,}", s)) | {c[i:i + 2] for c in cjk for i in range(len(c) - 1)}
    return len(grams(a) & grams(b))


def qtoks(q: str) -> set:
    """Words and single CJK characters of a quote, glosses in [brackets] removed."""
    q = re.sub(r"\[[^\]]*\]", " ", str(q or "").lower())
    return set(re.findall(r"[\u4e00-\u9fff]|[^\W\u4e00-\u9fff]{2,}", q))


def same_text(g: dict, p: dict, cut: float = 0.6) -> bool:
    """Do the two items quote substantially the same passage? Overlap coefficient of quote tokens."""
    a, b = qtoks(g.get("evidence", {}).get("quote")), qtoks(p.get("evidence", {}).get("quote"))
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= cut


def greedy(gold: list, pred: list, same) -> int:
    """Number of one-to-one matches between gold and predicted items."""
    used, hits = set(), 0
    for g in gold:
        for j, p in enumerate(pred):
            if j not in used and same(g, p):
                used.add(j)
                hits += 1
                break
    return hits


def same_date(a: str, b: str) -> bool:
    """Equal at their common precision: '2026-09' matches '2026-09-22'; '2026' matches any 2026 date."""
    n = min(len(a), len(b))
    return n >= 4 and a[:n] == b[:n]


def num_eq(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))


SECTIONS = {
    "events": (lambda a: [e for e in a.get("events", []) if e.get("date") and e.get("date_type") != "external"],
               {"event+date": lambda g, p: g["event"] == p["event"] and same_date(g["date"], p["date"]),
                "date": lambda g, p: same_date(g["date"], p["date"])}),
    "thresholds": (lambda a: [t for t in a.get("thresholds", []) if t.get("value") is not None],
                   {"value": lambda g, p: num_eq(g["value"], p["value"]) and num_eq(g.get("value_max"), p.get("value_max")),
                    "value+var": lambda g, p: num_eq(g["value"], p["value"]) and g["variable"] == p["variable"]}),
    "obligations": (lambda a: a.get("obligations", []),
                    {"match": lambda g, p: same_text(g, p) or (g["type"] == p["type"] and main_number(
                        g["evidence"]["provision"]) == main_number(p["evidence"]["provision"])),
                     "quote": same_text,
                     "quote+type": lambda g, p: same_text(g, p) and g["type"] == p["type"],
                     "quote+addressee": lambda g, p: same_text(g, p) and g["addressee"] == p["addressee"],
                     "provision": lambda g, p: main_number(g["evidence"]["provision"]) == main_number(p["evidence"]["provision"])}),
    "enforcement": (lambda a: a.get("enforcement", []),
                    {"type": lambda g, p: g["sanction_type"] == p["sanction_type"],
                     "type+amount": lambda g, p: g["sanction_type"] == p["sanction_type"]
                     and num_eq(g.get("max_penalty_amount"), p.get("max_penalty_amount"))}),
    "definitions": (lambda a: a.get("definitions", []),
                    {"term": lambda g, p: bool(norm(g["term"])) and (norm(g["term"]) in norm(p["term"])
                                                                   or norm(p["term"]) in norm(g["term"]))}),
    "relations": (lambda a: a.get("relations", []),
                  {"target": lambda g, p: (g.get("target_instrument_id") and g.get("target_instrument_id") == p.get("target_instrument_id"))
                   or (g["relation"] == p["relation"] and overlap(g["target_text"], p["target_text"]) >= 3)}),
}
CLASS_SETS = ["domains", "mechanisms", "decision_margins", "addressee_roles"]


def score_run(run: Path, ids: list[str] | None) -> dict:
    tally = defaultdict(lambda: [0, 0, 0])  # key -> [hits, n_gold, n_pred]
    cls = defaultdict(list)
    per_law, cost, quotes = [], 0.0, [0, 0]
    for path in sorted(run.glob("*.json")):
        if path.name.endswith(".log.json") or path.name.startswith("score"):
            continue
        iid = path.stem
        if ids and iid not in ids or not (GOLD / f"{iid}.json").exists():
            continue
        g, p = json.loads((GOLD / f"{iid}.json").read_text()), json.loads(path.read_text())
        row = {"id": iid}
        for sec, (items, rules) in SECTIONS.items():
            gi, pi = items(g), items(p)
            for rule, same in rules.items():
                h = greedy(gi, pi, same)
                t = tally[f"{sec}:{rule}"]
                t[0] += h; t[1] += len(gi); t[2] += len(pi)
            first = next(iter(rules))
            row[sec] = f"{greedy(gi, pi, rules[first])}/{len(gi)}g/{len(pi)}p"
        gc, pc = g["classification"], p.get("classification", {})
        for k in CLASS_SETS:
            a, b = set(gc.get(k, [])), set(pc.get(k, []))
            cls[k].append(len(a & b) / len(a | b) if a | b else 1.0)
        cls["primary_domain"].append(float(gc.get("primary_domain") == pc.get("primary_domain")))
        cls["binding_force"].append(float(gc.get("binding_force") == pc.get("binding_force")))
        row["entities"] = f"{len(g.get('entities', []))}g/{len(p.get('entities', []))}p"
        log = run / f"{iid}.log.json"
        if log.exists():
            lg = json.loads(log.read_text())
            cost += lg.get("usd", 0)
            qr = lg.get("quote_repairs", {})
            quotes[0] += qr.get("failed", 0); quotes[1] += qr.get("dropped", 0)
            row["errors"] = len(lg.get("errors", []))
        per_law.append(row)
    metrics = {}
    for k, (h, ng, npred) in tally.items():
        prec, rec = (h / npred if npred else float("nan")), (h / ng if ng else float("nan"))
        metrics[k] = (prec, rec, ng, npred)
    return {"run": run.name, "n": len(per_law), "metrics": metrics,
            "classification": {k: sum(v) / len(v) for k, v in cls.items() if v}, "per_law": per_law,
            "usd": cost, "quotes_failed_first_pass": quotes[0], "items_dropped": quotes[1]}


def fmt(x) -> str:
    return "–" if x != x else f"{x:.0%}"


def report(r: dict) -> str:
    lines = [f"# Pilot score: {r['run']}", "", f"{r['n']} instruments scored against gold; cost ${r['usd']:.3f}; "
             f"{r['quotes_failed_first_pass']} quotes failed the first pass, {r['items_dropped']} items dropped after repair.",
             "", "| section:rule | precision | recall | gold items | predicted |", "|---|---|---|---|---|"]
    for k, (p, rc, ng, npred) in sorted(r["metrics"].items()):
        lines.append(f"| {k} | {fmt(p)} | {fmt(rc)} | {ng} | {npred} |")
    lines += ["", "| classification | agreement |", "|---|---|"]
    lines += [f"| {k} | {v:.0%} |" for k, v in r["classification"].items()]
    lines += ["", "| instrument | " + " | ".join(SECTIONS) + " | entities | errors |", "|---" * (len(SECTIONS) + 3) + "|"]
    for row in r["per_law"]:
        lines.append(f"| {row['id']} | " + " | ".join(row.get(s, "") for s in SECTIONS)
                     + f" | {row['entities']} | {row.get('errors', '')} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = sys.argv[1:]
    ids = None
    if "--ids" in args:
        i = args.index("--ids")
        ids, args = args[i + 1:], args[:i]
    results = [score_run(Path(a), ids) for a in args]
    for a, r in zip(args, results):
        (Path(a) / "score.md").write_text(report(r))
    keys = sorted({k for r in results for k in r["metrics"]})
    print("| metric (P/R) | " + " | ".join(r["run"] for r in results) + " |")
    print("|---" * (len(results) + 1) + "|")
    for k in keys:
        print(f"| {k} | " + " | ".join(
            f"{fmt(r['metrics'][k][0])} / {fmt(r['metrics'][k][1])}" if k in r["metrics"] else "" for r in results) + " |")
    for k in ["domains", "mechanisms", "decision_margins", "addressee_roles", "primary_domain", "binding_force"]:
        print(f"| class:{k} | " + " | ".join(f"{r['classification'].get(k, float('nan')):.0%}" for r in results) + " |")
    print("| cost USD | " + " | ".join(f"{r['usd']:.3f}" for r in results) + " |")


if __name__ == "__main__":
    main()
