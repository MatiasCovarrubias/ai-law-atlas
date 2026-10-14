"""Trim Federal Register texts to the target document.

    python tools/trim_fr.py [--dry-run] [<id> ...]

A Federal Register PDF (govinfo FR-<date>/pdf/<doc>.pdf) prints the pages the document is on, so it often starts
with the end of the previous rule and ends with the start of the next one (spot-check, Oct 2026: one annotation
took 3 of its 4 obligations from an unrelated rule on the same pages). For each cached text that names more than
one "[FR Doc." number, this keeps the text from the end of the preceding document to the target's own
"[FR Doc. <number> Filed ...]" line. The target number comes from the text's meta URL or the registry URLs. The
untrimmed text is kept as cache/texts/<id>.untrimmed.txt.bak and the meta note records the trim.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXTS = ROOT / "cache" / "texts"
REG = {json.loads(l)["instrument_id"]: json.loads(l) for l in (ROOT / "registry" / "instruments.jsonl").open()}
DOC = re.compile(r"\[FR Doc\.?\s*((?:E\d[-–])?\d{2,4}[-–]\d{3,6})")
URLNUM = re.compile(r"(?<![\d.])((?:E\d-)?\d{4}-\d{4,6})(?:\.pdf|\.htm|/|$)")


def norm(n: str) -> str:
    return n.replace("–", "-")


def target(iid: str) -> str | None:
    urls = []
    meta = TEXTS / f"{iid}.meta.json"
    if meta.exists():
        urls.append(json.loads(meta.read_text()).get("url", ""))
    urls += list(REG.get(iid, {}).get("urls", {}).values())
    for u in urls:
        if m := URLNUM.search(u or ""):
            return m[1]
    return None


def trim(text: str, num: str) -> str | None:
    hits = [(m.start(), norm(m[1])) for m in DOC.finditer(text)]
    own = [p for p, n in hits if n == num]
    if not own:
        return None
    end_line = text.find("\n", own[0])
    end = len(text) if end_line < 0 else end_line
    if (b := re.match(r"\s*BILLING CODE[^\n]*", text[end:])):
        end += b.end()
    before = [p for p, n in hits if n != num and p < own[0]]
    start = 0
    if before:
        start = text.find("\n", before[-1]) + 1
        if (b := re.match(r"\s*BILLING CODE[^\n]*\n", text[start:])):
            start += b.end()
    return text[start:end].strip() + "\n"


def main() -> None:
    dry = "--dry-run" in sys.argv
    ids = [a for a in sys.argv[1:] if not a.startswith("--")]
    paths = [TEXTS / f"{i}.txt" for i in ids] if ids else sorted(TEXTS.glob("*.txt"))
    for p in paths:
        iid = p.stem
        text = p.read_text(encoding="utf-8")
        nums = {norm(m[1]) for m in DOC.finditer(text)}
        if len(nums) < 2:
            continue
        num = target(iid)
        new = trim(text, num) if num else None
        if not new:
            print(f"{iid}: {len(nums)} FR docs, target {num!r} not found: skipped")
            continue
        print(f"{iid}: FR Doc {num}: {len(text):,} -> {len(new):,} chars")
        if dry:
            continue
        (TEXTS / f"{iid}.untrimmed.txt.bak").write_text(text, encoding="utf-8")
        p.write_text(new, encoding="utf-8")
        meta = TEXTS / f"{iid}.meta.json"
        m = json.loads(meta.read_text()) if meta.exists() else {}
        m["note"] = (m.get("note", "") + " " if m.get("note") else "") + (
            f"Trimmed to FR Doc. {num} (the PDF pages also carried neighbouring documents); untrimmed copy kept.")
        meta.write_text(json.dumps(m, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
