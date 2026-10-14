"""Verify that every quote in an annotation file appears verbatim in the instrument's text.

    python tools/quote_check.py annotations/<instrument_id>.json [...]

Walks the JSON for any object with a "quote" field and checks it against cache/texts/<instrument_id>.txt.
Quotes are split at elisions ("...", "[...]") and bracketed glosses; each piece is checked in two steps:
1. locate it on a lowercase word stream that ignores punctuation and 1-2 digit tokens (footnote markers and
   list numbers that extraction scatters through the text);
2. inside the matched passage, require every token of the piece, small numbers included, to appear in order.
   So "20 qualifying jobs" altered to "30 qualifying jobs" fails, while stray footnote numbers do not.
Chinese and Japanese characters are one token each (the scripts have no spaces between words). A piece is
long enough to locate on its own at 12 Latin characters or 4 CJK characters; shorter pieces are still checked,
so a quote cannot pass on its gloss alone.
"""

from __future__ import annotations

import bisect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXTS = ROOT / "cache" / "texts"
CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
TOKEN = re.compile(rf"[{CJK}]|[^\W{CJK}]+")
IS_CJK = re.compile(rf"[{CJK}]")


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def is_small(tok: str) -> bool:
    return tok.isdigit() and len(tok) <= 2


def norm(text: str) -> str:
    """Word stream without 1-2 digit tokens (kept for callers that only need coarse matching)."""
    return " ".join(t for t in tokens(text) if not is_small(t))


class Doc:
    def __init__(self, text: str):
        self.toks = tokens(text)
        self.big_idx = [i for i, t in enumerate(self.toks) if not is_small(t)]
        big = [self.toks[i] for i in self.big_idx]
        self.big_str = " ".join(big)
        self.starts, pos = [], 0
        for t in big:
            self.starts.append(pos)
            pos += len(t) + 1

    def contains(self, piece: list[str]) -> bool:
        big = [t for t in piece if not is_small(t)]
        needle = " ".join(big)
        k = self.big_str.find(needle)
        while k >= 0:
            i = bisect.bisect_left(self.starts, k)
            if i < len(self.starts) and self.starts[i] == k:
                lo = self.big_idx[i - 1] + 1 if i > 0 else 0  # include small tokens adjacent to the match
                j = i + len(big)
                hi = self.big_idx[j] if j < len(self.big_idx) else len(self.toks)
                span = self.toks[lo:hi]
                it = iter(span)
                if all(any(tok == s for s in it) for tok in piece):  # piece is an ordered subsequence of span
                    return True
            k = self.big_str.find(needle, k + 1)
        return False


def long_enough(piece: list[str]) -> bool:
    big = [t for t in piece if not is_small(t)]
    return len(" ".join(big)) >= 12 or sum(bool(IS_CJK.match(t)) for t in big) >= 4


def pieces(quote: str) -> list[list[str]]:
    """Pieces of the quote between elisions and bracketed glosses; long pieces first."""
    parts = [tokens(x) for x in re.split(r"\[[^\]]*\]|\.\.\.|…", quote)]
    parts = [p for p in parts if any(not is_small(t) for t in p)]
    long = [p for p in parts if long_enough(p)]
    return long or parts


def quote_in(quote: str, doc: Doc) -> bool:
    ps = pieces(quote)
    if not ps:
        return "[" in quote  # only a gloss, elision or bare number: nothing verbatim to check
    return all(doc.contains(p) for p in ps)


def iter_quotes(node, path="$"):
    if isinstance(node, dict):
        if isinstance(node.get("quote"), str):
            yield path, node["quote"]
        for k, v in node.items():
            yield from iter_quotes(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_quotes(v, f"{path}[{i}]")


def check_file(path: Path) -> tuple[int, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    doc = Doc((TEXTS / f"{data['instrument_id']}.txt").read_text(encoding="utf-8"))
    quotes = list(iter_quotes(data))
    misses = [f"{p}: {q[:100]}" for p, q in quotes if not quote_in(q, doc)]
    return len(quotes), misses


if __name__ == "__main__":
    bad = 0
    for arg in sys.argv[1:]:
        n, misses = check_file(Path(arg))
        print(f"{arg}: {n - len(misses)}/{n} quotes verified")
        for m in misses:
            print("  MISS", m)
        bad += len(misses)
    sys.exit(1 if bad else 0)
