"""Causal-variation screen add-on (econ-semantic-extraction): one Haiku call per law (per part for long texts).

    python tools/causal_screen.py <ids...> --out ../econ-semantic-extraction/outputs/screen_calibration/haiku55
    python tools/causal_screen.py --batch --ids-file build/rollout/tierA.txt   # default --out: outputs/screen

Card: econ-semantic-extraction/experts/causal_screening_card.md; schema: experts/causal_screen_schema.json
(adapted for structured outputs: no maxItems, plausibility as an enum, instrument_id filled in code).
Output: <out>/<id>.json (schema-conformant) and <out>/<id>.log.json (calls, cost, quote repairs). Never written to
annotations/. Flag quotes are verified with tools/quote_check.py; failures get one repair call (corrected quote or
null) and flags whose quote still fails are dropped (screen calibration decision 2, Oct 2026). Every call goes
through the budget guard. The screen locates provisions; every flag at plausibility >= 2 goes to Opus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from annotate import CHUNK_TOKENS, REGISTRY, Annotator, est_tokens, split_text  # noqa: E402
from annotate_batch import BatchRun  # noqa: E402
from budget import BudgetExceeded  # noqa: E402
from quote_check import TEXTS, Doc, quote_in  # noqa: E402

ESE = ROOT.parent / "econ-semantic-extraction"
CARD = (ESE / "experts" / "causal_screening_card.md").read_text()
FULL_SCHEMA = json.loads((ESE / "experts" / "causal_screen_schema.json").read_text())
MODEL = "claude-haiku-5-5"


def model_schema() -> dict:
    def strip(node):
        if isinstance(node, dict):
            out = {k: strip(v) for k, v in node.items()
                   if k not in ("$schema", "title", "description", "maxItems", "minimum", "maximum")}
            return out
        if isinstance(node, list):
            return [strip(x) for x in node]
        return node
    s = strip(FULL_SCHEMA)
    s["properties"].pop("instrument_id")
    s["required"] = [r for r in s["required"] if r != "instrument_id"]
    s["properties"]["flags"]["items"]["properties"]["plausibility"] = {"type": "integer", "enum": [1, 2, 3, 4, 5]}
    return s


SCHEMA = model_schema()
REPAIR = {"type": "object", "additionalProperties": False, "required": ["fixes"],
          "properties": {"fixes": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["index", "quote"],
              "properties": {"index": {"type": "integer"}, "quote": {"type": ["string", "null"]}}}}}}


def system(iid: str, text: str, part: str) -> list:
    tag = f' part="{part}"' if part else ""
    return [{"type": "text", "text": CARD, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"<document instrument_id=\"{iid}\"{tag}>\n{text}\n</document>",
             "cache_control": {"type": "ephemeral"}}]


def prompt(iid: str, part: str) -> str:
    r = REGISTRY.get(iid, {})
    meta = {k: r.get(k) for k in ("title", "jurisdiction", "instrument_type", "status", "legislative_stage", "dates")}
    scope = (f"\nThe document is long and given in parts; this is part {part}. Screen only this part." if part else "")
    return (f"Instrument: {iid}\nRegistry metadata (may be incomplete): {json.dumps(meta, ensure_ascii=False)}{scope}"
            "\n\nScreen this instrument following the card. Return the flags (or none_reason).")


def repair_prompt(iid: str, bad: list[tuple[int, str]]) -> str:
    listing = "\n".join(f"- flag {i}: {q}" for i, q in bad)
    return (f"These flag quotes from your screen of {iid} were not found verbatim in the document:\n{listing}\n\n"
            "For each flag index, return a quote of at most 40 words copied character for character from the "
            "document that supports the same flag, or null if there is none.")


def finish(iid: str, outs: list[dict], fixes: dict[int, str], text: str, log: dict, out: Path) -> str:
    flags = [f for o in outs for f in o.get("flags", [])]
    doc = Doc(text)
    kept, dropped, fixed = [], 0, 0
    for i, f in enumerate(flags):
        if not quote_in(f["quote"], doc):
            if fixes.get(i) and quote_in(fixes[i], doc):
                f["quote"] = fixes[i]
                fixed += 1
            else:
                dropped += 1
                continue
        kept.append(f)
    reasons = [o.get("none_reason") for o in outs if o.get("none_reason")]
    res = {"instrument_id": iid, "flags": kept,
           "none_reason": None if kept else (" ".join(reasons) or "no flags with verifiable quotes")}
    (out / f"{iid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log.update({"quotes": {"flags": len(flags), "fixed": fixed, "dropped": dropped}, "flags_kept": len(kept),
                "usd": round(sum(c["usd"] for c in log["calls"]), 5), "date": date.today().isoformat()})
    (out / f"{iid}.log.json").write_text(json.dumps(log, indent=1))
    return f"{iid}: {len(kept)} flags (quotes fixed {fixed}, dropped {dropped}), ${log['usd']:.4f}"


def bad_quotes(outs: list[dict], text: str) -> list[tuple[int, str]]:
    doc = Doc(text)
    flags = [f for o in outs for f in o.get("flags", [])]
    return [(i, f["quote"]) for i, f in enumerate(flags) if not quote_in(f["quote"], doc)]


def run_sync(ann: Annotator, ids: list[str], out: Path, workers: int) -> None:
    def one(iid: str) -> str:
        text = (TEXTS / f"{iid}.txt").read_text(encoding="utf-8")
        chunks = split_text(text)
        log = {"instrument_id": iid, "model": MODEL, "effort": ann.effort, "parts": len(chunks), "calls": []}
        outs = []
        for k, c in enumerate(chunks):
            part = f"{k + 1} of {len(chunks)}" if len(chunks) > 1 else ""
            outs.append(ann.call(system(iid, c, part), prompt(iid, part), SCHEMA, log, max_tokens=32000))
        fixes = {}
        if bad := bad_quotes(outs, text):
            for k, c in enumerate(chunks):
                part = f"{k + 1} of {len(chunks)}" if len(chunks) > 1 else ""
                for f in ann.call(system(iid, c, part), repair_prompt(iid, bad), REPAIR, log, max_tokens=8000)["fixes"]:
                    fixes.setdefault(f["index"], f["quote"])
        return finish(iid, outs, fixes, text, log, out)

    def safe(iid: str) -> str:
        try:
            return one(iid)
        except BudgetExceeded:
            raise
        except Exception as e:  # recorded; the other laws go on
            (out / f"{iid}.log.json").write_text(json.dumps({"instrument_id": iid, "error": repr(e)}))
            return f"{iid}: FAILED {e!r}"

    with ThreadPoolExecutor(workers) as pool:
        for line in pool.map(safe, ids):
            print(line, flush=True)


def run_batch(ann: Annotator, ids: list[str], out: Path) -> None:
    br = BatchRun(ann)
    texts = {i: (TEXTS / f"{i}.txt").read_text(encoding="utf-8") for i in ids}
    logs = {i: {"instrument_id": i, "model": MODEL, "effort": ann.effort, "batch": True, "calls": []} for i in ids}
    jobs = {}
    for i in ids:
        chunks = split_text(texts[i])
        logs[i]["parts"] = len(chunks)
        for k, c in enumerate(chunks):
            part = f"{k + 1} of {len(chunks)}" if len(chunks) > 1 else ""
            jobs[f"s{len(jobs):06d}"] = {"iid": i, "k": k, "params": br.params(system(i, c, part), prompt(i, part),
                                                                              SCHEMA, 32000, None)}
    res = br.run("screen-" + hashlib.sha1(" ".join(ids).encode()).hexdigest()[:10], jobs, logs)
    outs = {i: [] for i in ids}
    for cid in sorted(res, key=lambda c: (jobs[c]["iid"], jobs[c]["k"])):
        if not isinstance(res[cid], Exception):
            outs[jobs[cid]["iid"]].append(res[cid])
    rjobs = {}
    bads = {i: bad_quotes(outs[i], texts[i]) for i in ids}
    for i in ids:
        if bads[i]:
            chunks = split_text(texts[i])
            for k, c in enumerate(chunks):
                part = f"{k + 1} of {len(chunks)}" if len(chunks) > 1 else ""
                rjobs[f"q{len(rjobs):06d}"] = {"iid": i, "k": k, "params": br.params(
                    system(i, c, part), repair_prompt(i, bads[i]), REPAIR, 8000, None)}
    rres = br.run("screen-repairs-" + hashlib.sha1(" ".join(ids).encode()).hexdigest()[:10], rjobs, logs)
    fixes = {i: {} for i in ids}
    for cid in sorted(rres, key=lambda c: rjobs[c]["k"]):
        if not isinstance(rres[cid], Exception):
            for f in rres[cid]["fixes"]:
                fixes[rjobs[cid]["iid"]].setdefault(f["index"], f["quote"])
    for i in ids:
        print(finish(i, outs[i], fixes[i], texts[i], logs[i], out), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--ids-file")
    ap.add_argument("--out", default=str(ESE / "outputs" / "screen"))
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.ids_file:
        args.ids += [l.strip() for l in Path(args.ids_file).read_text().splitlines() if l.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ids = [i for i in dict.fromkeys(args.ids) if (TEXTS / f"{i}.txt").exists() and not (out / f"{i}.json").exists()]
    print(f"{len(ids)} laws to screen", flush=True)
    ann = Annotator(MODEL, args.effort, "focused", out)
    try:
        run_batch(ann, ids, out) if args.batch else run_sync(ann, ids, out, args.workers)
    except BudgetExceeded as e:
        print(f"STOPPED: {e}", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
