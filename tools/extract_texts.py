"""Convert downloaded raw files (cache/raw/<id>.html|.pdf) into plain text (cache/texts/<id>.txt).

    python tools/extract_texts.py                  # raw files that have no text yet
    python tools/extract_texts.py <id> ...         # selected ids (still skips existing texts)
    python tools/extract_texts.py --force <id> ... # overwrite: annotations quoting the old text may break

Existing texts are never overwritten by default: annotations are validated against them, and some were built
with one-off extractors (noted in each annotation's text_version.note).

HTML: drop scripts/navigation, keep block structure. PDF: pdftotext (reading order, no layout,
which handles Federal Register three-column pages). Site-specific trims live in TRIMS.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW, TEXTS = ROOT / "cache" / "raw", ROOT / "cache" / "texts"

# id -> (start marker, end marker); text outside is dropped (site chrome, other sections of a code).
TRIMS = {
    "us-tx-tax-151.359": ("Sec. 151.359.", "Sec. 151.3595."),
    "de-enefg-2023": ("Gesetz zur Steigerung", "zum Seitenanfang"),
    "us-ca-sb53-2025": ("The people of the State of California do enact as follows", None),
    "it-garante-2023-chatgpt-limitation": ("IL GARANTE PER LA PROTEZIONE DEI DATI PERSONALI", "\nScheda\n"),
}
# Keep the legislative digest of SB 53 too: it starts before the enacting clause.
KEEP_FROM = {"us-ca-sb53-2025": "LEGISLATIVE COUNSEL'S DIGEST"}


BLOCKS = ["p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
          "article", "blockquote", "pre", "dt", "dd", "ul", "ol", "hr"]


def html_to_text(raw: bytes) -> str:
    """Line breaks only at block elements, so inline spans (EUR-Lex, legislature XHTML) don't split words."""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form"]):
        tag.decompose()
    sups = soup.find_all("sup") + soup.find_all(class_="oj-super")  # EUR-Lex marks exponents with a class
    for sup in sups:  # 10<sup>26</sup> -> 10^26
        sup.replace_with(f"^{sup.get_text()}")
    for tag in soup.find_all(BLOCKS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text("")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def pdf_to_text(path: Path) -> str:
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, check=True)
    text = re.sub(r"-\n(?=[a-z])", "", out.stdout)  # de-hyphenate line breaks
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def trim(doc_id: str, text: str) -> str:
    if doc_id in TRIMS:
        start, end = TRIMS[doc_id]
        start = KEEP_FROM.get(doc_id, start)
        i = text.find(start)
        text = text[i:] if i >= 0 else text
        if end and (j := text.find(end, len(start))) >= 0:
            text = text[:j]
    return text.strip()


def extract(path: Path) -> None:
    doc_id = path.stem
    if path.suffix == ".pdf":
        text = pdf_to_text(path)
    else:
        raw = path.read_bytes()
        text = html_to_text(raw)
    text = trim(doc_id, text)
    TEXTS.mkdir(exist_ok=True)
    (TEXTS / f"{doc_id}.txt").write_text(text, encoding="utf-8")
    print(f"{doc_id}: {len(text):,} chars (~{len(text) / 3.6 / 1000:.0f}k tokens)")


if __name__ == "__main__":
    force = "--force" in sys.argv
    ids = {a for a in sys.argv[1:] if a != "--force"}
    for path in sorted(RAW.iterdir()):
        if path.suffix in {".html", ".pdf"} and (not ids or path.stem in ids):
            if (TEXTS / f"{path.stem}.txt").exists() and not force:
                print(f"{path.stem}: text exists, skipped (use --force to overwrite)")
                continue
            extract(path)
