"""Triage (PLAN.md section 4): gates, content scores and decision margins per instrument, then tiers in code.

    python tools/triage.py                    # every registry instrument not yet in registry/triage.jsonl
    python tools/triage.py <id> ...           # selected instruments (re-scored)
    python tools/triage.py --retier           # no API calls: recompute tiers from stored scores (after a rule change)
    python tools/triage.py --stats            # tier counts by region and stage
    python tools/triage.py --export A build/rollout/tierA.txt   # ids to annotate, with a cost estimate

One Haiku 5.5 call per instrument (medium effort, structured output) on: registry metadata, AGORA's summary when
the instrument is AGORA-linked, and the opening of the cached text plus an outline of its headings (about 3K
tokens). The model scores content as if enacted; the legislative stage comes from the registry, not the model.
Tier rules, the family rule and human overrides (registry/triage_overrides.jsonl: {"instrument_id", "tier",
"by", "reason"}) are applied in code by `tier_of`. Every call goes through the budget guard (tools/budget.py).
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from annotate import REGISTRY, Annotator, est_tokens, outline  # noqa: E402
from budget import BudgetExceeded  # noqa: E402
from quote_check import TEXTS  # noqa: E402

OUT = ROOT / "registry" / "triage.jsonl"
OVERRIDES = ROOT / "registry" / "triage_overrides.jsonl"
MODEL = "claude-haiku-5-5"
EXCERPT_TOKENS = 2200
LIVE = {"adopted", "final_passage", "advanced"}
MARGINS = json.loads((ROOT / "schemas" / "triage.schema.json").read_text())["properties"]["decision_margins"]["items"]["enum"]

INSTRUCTIONS = """You triage legal instruments for ai-law-atlas, a database of AI-relevant law (AI, data, compute,
chips, data centers, energy for computing, platforms). The question is whether the instrument changes what a firm
or household decides. Judge from the material given (metadata, a summary and the opening of the text); do not
read deeply and do not use outside knowledge beyond recognising what kind of instrument it is.

Score the CONTENT AS IF ENACTED: a bill or draft is scored on what it would do if adopted. Its stage is recorded
separately and must not lower the scores.

Gates (true/false):
- binding: a statute, regulation, decree, binding order or decision, ordinance, code with legal effect, or a
  proposal for any of these. Strategies, white papers, principles, non-binding guidance and resolutions that only
  express views fail.
- private_reach: firms or households are (or would be) addressees, or are directly affected through prices,
  subsidies, taxes or market access. Rules that only organise government internally fail; procurement rules pass
  only if they set terms for vendors; funding programmes pass if private actors can receive the money.

Content scores (0, 1 or 2):
- decision_margin: 0 procedural or reporting only; 1 product or process requirements, disclosure duties;
  2 changes prices, taxes or subsidies, market access or licensing, prohibitions, or restrictions on inputs
  (compute, chips, data, energy).
- bite: 0 no sanction; 1 modest fines or cure periods; 2 turnover-based fines, licence denial, export bans,
  large sums at stake (tax exemptions and subsidies with large money count as 2).
- reach: 0 narrow niche; 1 one sector or one mid-size jurisdiction; 2 economy-wide in a large jurisdiction
  (US federal, EU, China, a large US state such as CA, TX, NY), or extraterritorial.
- specificity: 0 principles only; 1 some operative rules with details delegated; 2 concrete thresholds, rates,
  dates or obligations.
If the material is too thin to judge a criterion, score what the material supports and set confidence low.

Also give: decision_margins (the firm or household margins the instrument acts on; empty if none);
globally_consequential (true if the instrument sets a reference others follow or binds actors abroad, e.g. the EU
AI Act, US export controls, China's generative AI measures); framework_law (true if key parameters are delegated
to implementing acts); confidence (low, medium, high); rationale (at most 40 words, neutral, in English).

text_quality, about the text opening only (not the instrument): operative_text (the instrument's own legal text,
e.g. articles, sections, a decree, an order, a bill text); partial_text (part of the instrument's text, or the text
mixed with heavy website navigation); not_the_text (a press release, news item, landing or search page, summary,
FAQ, consultation notice without the draft, or another document); no_text (no text was given)."""

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["binding", "private_reach", "decision_margin", "bite", "reach", "specificity",
                       "decision_margins", "globally_consequential", "framework_law", "confidence", "rationale",
                       "text_quality"],
          "properties": {"binding": {"type": "boolean"}, "private_reach": {"type": "boolean"},
                         **{k: {"type": "integer", "enum": [0, 1, 2]}
                            for k in ("decision_margin", "bite", "reach", "specificity")},
                         "decision_margins": {"type": "array", "items": {"type": "string", "enum": MARGINS}},
                         "globally_consequential": {"type": "boolean"}, "framework_law": {"type": "boolean"},
                         "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                         "rationale": {"type": "string"},
                         "text_quality": {"type": "string",
                                          "enum": ["operative_text", "partial_text", "not_the_text", "no_text"]}}}


def agora_summaries() -> dict[str, str]:
    import pandas as pd
    path = ROOT / "cache" / "agora" / "agora" / "documents.csv"
    if not path.exists():
        return {}
    docs = pd.read_csv(path, low_memory=False).set_index("AGORA ID")
    out = defaultdict(list)
    for l in csv.DictReader((ROOT / "registry" / "links.csv").open()):
        if l["from"].startswith("agora:") and l["to"].startswith("instrument:") and l["relation"] in ("same", "part_of"):
            a = int(l["from"].removeprefix("agora:"))
            if a in docs.index and isinstance(docs.loc[a, "Short summary"], str):
                out[l["to"].removeprefix("instrument:")].append(f"{docs.loc[a, 'Official name']}: "
                                                                 f"{docs.loc[a, 'Short summary']}")
    return {k: "\n".join(v[:6]) for k, v in out.items()}


def material(iid: str, summaries: dict) -> tuple[str, str]:
    r = REGISTRY[iid]
    meta = {k: r.get(k) for k in ("title", "short_title", "jurisdiction", "level", "instrument_type", "status",
                                  "legislative_stage", "dates", "domains", "language")}
    parts = [f"Instrument {iid}\nRegistry metadata: {json.dumps(meta, ensure_ascii=False)}"]
    if s := summaries.get(iid):
        parts.append(f"<agora_summary note=\"third-party summary, may include machine output\">\n{s}\n</agora_summary>")
    path = TEXTS / f"{iid}.txt"
    basis = "metadata only"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        cut = len(text) if est_tokens(text) <= EXCERPT_TOKENS else int(len(text) * EXCERPT_TOKENS / est_tokens(text))
        excerpt = text[:cut]
        heads = outline(text, max_tokens=800) if cut < len(text) else ""
        parts.append(f"<text_opening tokens_total=\"{est_tokens(text)}\">\n{excerpt}\n</text_opening>")
        if heads:
            parts.append(f"<outline>\n{heads}\n</outline>")
        basis = "text excerpt" if cut < len(text) else "full text"
    elif summaries.get(iid):
        basis = "metadata and AGORA summary"
    return "\n\n".join(parts), basis


def families() -> dict[str, list[str]]:
    out = defaultdict(list)
    for l in (ROOT / "registry" / "families.jsonl").open():
        f = json.loads(l)
        for m in f.get("members", []):
            out[m if isinstance(m, str) else m.get("instrument_id")].append(f["family_id"])
    return out


def tier_of(rec: dict, fam: dict, overrides: dict) -> dict:
    """PLAN.md section 4 tier rules. Returns the fields to store: tier, content_score, family_rule, override."""
    iid, r = rec["instrument_id"], REGISTRY.get(rec["instrument_id"], {})
    s = rec.get("scores", {})
    score = sum(s.values()) if s else 0
    stage, region = rec["legislative_stage"], r.get("priority_region", "")
    p1_or_global = region.startswith("P1") or rec.get("globally_consequential", False)
    gates = rec["gates"]["binding"] and rec["gates"]["private_reach"]
    if not gates:
        t = "C"
    elif score >= 6 and p1_or_global and stage in LIVE:
        t = "A"
    elif (3 <= score and stage in LIVE) or (score >= 6 and stage == "introduced"):
        t = "B"
    else:
        t = "C"
    fam_rule = bool(fam.get(iid)) and rec["gates"]["binding"] and t == "C"
    if fam_rule:
        t = "B"
    out = {"tier": t, "content_score": score, "family_rule": fam_rule, "families": fam.get(iid, [])}
    if iid in overrides:
        o = overrides[iid]
        out["override"] = {"tier": o["tier"], "by": o.get("by", ""), "reason": o.get("reason", "")}
        out["tier"] = o["tier"]
    return out


def load() -> dict[str, dict]:
    return {json.loads(l)["instrument_id"]: json.loads(l) for l in OUT.open() if l.strip()} if OUT.exists() else {}


def save_all(recs: dict[str, dict]) -> None:
    OUT.write_text("".join(json.dumps(recs[k], ensure_ascii=False) + "\n" for k in sorted(recs)))


def stats(recs: dict[str, dict]) -> None:
    by = Counter((r["tier"], r["legislative_stage"]) for r in recs.values())
    print("tier counts:", dict(Counter(r["tier"] for r in recs.values())))
    for t in "ABC":
        print(f"  {t}:", {s: n for (tt, s), n in sorted(by.items()) if tt == t})
    reg = Counter((r["tier"], REGISTRY.get(r["instrument_id"], {}).get("priority_region")) for r in recs.values())
    for t in "AB":
        print(f"  {t} by region:", {g: n for (tt, g), n in sorted(reg.items(), key=str) if tt == t})


def estimate_usd(iid: str, tier: str) -> float:
    """Batch-price estimate from the Oct 2026 pilot (sync, focused, high): about $0.015 + $0.0009 per 1,000 characters,
    halved by the Batch API; Tier B (light mode, 4 of 6 calls) about two thirds of that."""
    n = len((TEXTS / f"{iid}.txt").read_text(encoding="utf-8"))
    return 0.5 * (0.015 + 0.0009 * n / 1000) * (1 if tier == "A" else 0.67)


def export(recs: dict[str, dict], tier: str, path: str) -> None:
    """Ids of `tier` with a usable text (not gold, text present, triage did not judge it not_the_text)."""
    ids, skipped = [], Counter()
    for iid, r in sorted(recs.items()):
        if r["tier"] != tier:
            continue
        if (ROOT / "annotations" / f"{iid}.json").exists():
            skipped["gold annotation exists"] += 1
        elif not (TEXTS / f"{iid}.txt").exists():
            skipped["no text"] += 1
        elif r.get("text_quality") in ("not_the_text", "no_text"):
            skipped["text is not the instrument"] += 1
        else:
            ids.append(iid)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(i + "\n" for i in ids))
    est = sum(estimate_usd(i, tier) for i in ids)
    print(f"tier {tier}: {len(ids)} ids -> {path}; skipped {dict(skipped)}; estimated batch cost ${est:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--retier", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--export", nargs=2, metavar=("TIER", "FILE"))
    args = ap.parse_args()
    recs, fam = load(), families()
    overrides = ({json.loads(l)["instrument_id"]: json.loads(l) for l in OVERRIDES.open() if l.strip()}
                 if OVERRIDES.exists() else {})
    if args.export:
        export(recs, *args.export)
        return
    if args.retier or args.stats:
        if args.retier:
            for rec in recs.values():  # the stage may have changed in the registry since scoring
                rec["legislative_stage"] = REGISTRY.get(rec["instrument_id"], {}).get("legislative_stage",
                                                                                      rec["legislative_stage"])
                rec.update(tier_of(rec, fam, overrides))
            save_all(recs)
        stats(recs)
        return
    todo = args.ids or [i for i in REGISTRY if i not in recs]
    summaries = agora_summaries()
    ann = Annotator(MODEL, args.effort, "focused", ROOT / "build" / "triage")
    print(f"{len(todo)} instruments to triage", flush=True)

    def run(iid: str) -> str:
        doc, basis = material(iid, summaries)
        log = {"calls": []}
        o = ann.call([{"type": "text", "text": INSTRUCTIONS}], doc, SCHEMA, log, max_tokens=8000)
        rec = {"instrument_id": iid, "date": date.today().isoformat(), "model": f"{MODEL}, effort {args.effort}",
               "legislative_stage": REGISTRY[iid].get("legislative_stage", "introduced"),
               "gates": {"binding": o["binding"], "private_reach": o["private_reach"]},
               "scores": {k: o[k] for k in ("decision_margin", "bite", "reach", "specificity")},
               "decision_margins": o["decision_margins"], "globally_consequential": o["globally_consequential"],
               "framework_law": o["framework_law"], "confidence": o["confidence"], "rationale": o["rationale"],
               "text_quality": o["text_quality"], "basis": basis, "usd": round(sum(c["usd"] for c in log["calls"]), 6)}
        rec.update(tier_of(rec, fam, overrides))
        with OUT.open("a", encoding="utf-8") as fh:  # append as we go, so an interrupted run resumes
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return f"{iid}: {rec['tier']} (score {rec['content_score']}, {basis})"

    pool = ThreadPoolExecutor(args.workers)
    try:
        for i, line in enumerate(pool.map(run, todo)):
            if i % 50 == 0 or args.ids:
                print(line, flush=True)
    except BudgetExceeded as e:
        print(f"STOPPED: {e}", flush=True)
        sys.exit(2)
    finally:
        pool.shutdown(cancel_futures=True)
    recs = load()  # re-scored ids appear twice: the last row wins
    save_all(recs)
    stats(recs)


if __name__ == "__main__":
    main()
