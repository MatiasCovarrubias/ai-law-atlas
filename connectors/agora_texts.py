"""Texts for AGORA-linked instruments, from the AGORA plaintexts in the local cache (CC BY-NC: never commit).

    python connectors/agora_texts.py            # all AGORA-linked instruments without a text yet
    python connectors/agora_texts.py <id> ...   # selected instruments

Uses registry/links.csv:
- `same`: the instrument is an AGORA record; copy its plaintext. If several AGORA records are the same
  instrument (duplicates or versions), keep one: enacted first, then the most recent activity, then the longest.
- `part_of` only: AGORA holds sections of the instrument (titles of an NDAA or an omnibus bill); concatenate them
  in AGORA ID order. The text is partial: the rest of the act is missing.
- `version_of` only: a different version; no text.

Writes cache/texts/<id>.txt and cache/texts/<id>.meta.json (source, url, retrieved, extent, agora_ids), which
tools/annotate.py copies into the annotation's text_version. Never overwrites an existing text.
A record whose AGORA name cites a part of an act ("Title IV", "Sec. 5", "Regulation 10") is marked partial too.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
AGORA = ROOT / "cache" / "agora" / "agora"
TEXTS = ROOT / "cache" / "texts"
SECTION = re.compile(r"\b(Title|Subtitle|Division|Sec\.|Sections?|Part|Chapter|Articles?|Art\.|Regulation)\s+[\dIVXL]+\b"
                     r"|,\s*(Title|Sec\.|Section|Division|Subtitle)\b", re.I)
MIN_CHARS = 300  # shorter AGORA texts are stubs (a link or a heading), not the document


def agora_links() -> dict[str, list[tuple[str, int]]]:
    out = defaultdict(list)
    for l in csv.DictReader((ROOT / "registry" / "links.csv").open()):
        if l["from"].startswith("agora:") and l["to"].startswith("instrument:"):
            out[l["to"].removeprefix("instrument:")].append((l["relation"], int(l["from"].removeprefix("agora:"))))
    return out


def build(iid: str, links: list[tuple[str, int]], docs: pd.DataFrame) -> tuple[str, dict] | None:
    same = sorted(a for r, a in links if r == "same")
    parts = sorted(a for r, a in links if r == "part_of")
    if len(same) > 1:  # AGORA duplicates or versions of one instrument: keep one, never concatenate them
        same = [max(same, key=lambda a: (str(docs.loc[a, "Most recent activity"]) == "Enacted" if a in docs.index else False,
                                         str(docs.loc[a, "Most recent activity date"]) if a in docs.index else "",
                                         (AGORA / "fulltext" / f"{a}.txt").stat().st_size
                                         if (AGORA / "fulltext" / f"{a}.txt").exists() else 0))]
    ids = same or parts
    if not ids:
        return None
    texts = []
    for a in ids:
        p = AGORA / "fulltext" / f"{a}.txt"
        t = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        if len(t) >= MIN_CHARS:
            texts.append((a, t))
    if not texts:
        return None
    rows = [docs.loc[a] for a, _ in texts if a in docs.index]
    names = [str(r["Official name"]) for r in rows]
    partial = not same or any(SECTION.search(n) for n in names)
    sep = "\n\n"
    if len(texts) > 1:  # mark each AGORA record so a reader can tell where sections join
        body = sep.join(f"[AGORA {a}: {docs.loc[a, 'Official name'] if a in docs.index else ''}]\n{t}" for a, t in texts)
    else:
        body = texts[0][1]
    retrieved = sorted({str(r["Official plaintext retrieved"]) for r in rows if pd.notna(r["Official plaintext retrieved"])})
    urls = [str(r["Official plaintext source"]) for r in rows if pd.notna(r["Official plaintext source"])]
    meta = {"source": "AGORA plaintext (ETO, CC BY-NC 4.0), local cache",
            "url": urls[0] if urls else "", "retrieved": retrieved[-1] if retrieved else "",
            "version": (ROOT / "cache" / "agora" / "VERSION").read_text().strip()
            if (ROOT / "cache" / "agora" / "VERSION").exists() else "AGORA",
            "extent": "partial" if partial else "full",
            "agora_ids": [a for a, _ in texts]}
    if partial:
        meta["note"] = ("AGORA sections of the act only: " if not same else "AGORA record covers part of an act: ") \
            + "; ".join(n[:120] for n in names)
    return body + "\n", meta


def main() -> None:
    want = set(sys.argv[1:])
    docs = pd.read_csv(AGORA / "documents.csv", low_memory=False).set_index("AGORA ID")
    links = agora_links()
    TEXTS.mkdir(parents=True, exist_ok=True)
    counts = defaultdict(int)
    for iid, ls in sorted(links.items()):
        if want and iid not in want:
            continue
        if (TEXTS / f"{iid}.txt").exists():
            counts["exists"] += 1
            continue
        res = build(iid, ls, docs)
        if res is None:
            counts["no AGORA text"] += 1
            continue
        body, meta = res
        (TEXTS / f"{iid}.txt").write_text(body, encoding="utf-8")
        (TEXTS / f"{iid}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        counts[f"written ({meta['extent']})"] += 1
    print(dict(counts))


if __name__ == "__main__":
    main()
