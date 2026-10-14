"""Fetch official texts for registry instruments that have none yet (after connectors/agora_texts.py).

    python connectors/fetch_texts.py                 # every instrument without cache/texts/<id>.txt
    python connectors/fetch_texts.py <id> ...        # selected instruments
    python connectors/fetch_texts.py --retry-blocked # also retry records marked text_access "blocked"

For each instrument, tries urls.fulltext, then urls.official. Saves the raw file to cache/raw/<id>.html|.pdf,
extracts it with tools/extract_texts.py and writes cache/texts/<id>.meta.json. Checks:
- bot-check, captcha or error pages are rejected (bot_block);
- an HTML page with little text is a landing page: one PDF link that looks like the text is followed;
  otherwise js_rendered (a script app) or document_not_located;
- a PDF without a text layer is scanned_no_text.
Every failure is logged with tools/access_log.py (agent "fetch_texts"). Generic browser User-Agent, no personal
data in headers. One request at a time per site, with a pause; sites are fetched in parallel.
Never overwrites an existing text. Local cache only: never commit texts.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from access_log import log_issue, resolve  # noqa: E402
from extract_texts import html_to_text, pdf_to_text  # noqa: E402

RAW, TEXTS = ROOT / "cache" / "raw", ROOT / "cache" / "texts"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"}
PAUSE = 2.0          # seconds between requests to the same site
MIN_TEXT = 1500      # characters; less is a landing page, a stub or an error page
BOT = re.compile(r"captcha|are you a robot|access denied|request rejected|just a moment|attention required|"
                 r"enable javascript|verify you are human|security checkpoint|unusual traffic", re.I)
PDF_HINT = re.compile(r"texto|text|ley|law|decreto|decree|resoluci|regulation|download|descarg|baixar|[íi]ntegra|"
                      r"full|gesetz|loi|legge|全文|附件|pdf", re.I)
INSECURE = ssl.create_default_context()
INSECURE.check_hostname, INSECURE.verify_mode = False, ssl.CERT_NONE


class Fail(Exception):
    def __init__(self, error_type: str, detail: str, status: int | None = None):
        super().__init__(detail)
        self.error_type, self.detail, self.status = error_type, detail, status


ELI_TYPES = {"reg": "R", "reg_impl": "R", "reg_del": "R", "dir": "L", "dir_impl": "L", "dir_del": "L", "dec": "D",
             "dec_impl": "D", "dec_del": "D"}


def cellar(url: str) -> str | None:
    """EUR-Lex answers scripts with an empty bot challenge (HTTP 202); the Publications Office's Cellar serves the
    same document by CELEX number."""
    if "eur-lex.europa.eu" not in url:
        return None
    if m := re.search(r"CELEX[:%3A]+([0-9A-Z()]+)", url, re.I):
        return f"http://publications.europa.eu/resource/celex/{m[1]}"
    if (m := re.search(r"/eli/([a-z_]+)/(\d{4})/(\d+)", url)) and m[1] in ELI_TYPES:
        return f"http://publications.europa.eu/resource/celex/3{m[2]}{ELI_TYPES[m[1]]}{int(m[3]):04d}"
    return None


def get(url: str) -> tuple[bytes, str, str, bool]:
    """(body, content type, final url, used an unverified TLS context)."""
    extra = {"Accept-Language": "eng"} if "publications.europa.eu" in url else {}
    req = urllib.request.Request(url, headers=HEADERS | extra)
    for insecure in (False, True):  # some government sites serve incomplete certificate chains
        try:
            with urllib.request.urlopen(req, timeout=60, context=INSECURE if insecure else None) as r:
                return r.read(), r.headers.get("Content-Type", ""), r.geturl(), insecure
        except urllib.error.HTTPError as e:
            kind = "not_found" if e.code in (404, 410) else "bot_block" if e.code in (401, 403, 429, 503) else "other"
            raise Fail(kind, f"HTTP {e.code}", e.code)
        except urllib.error.URLError as e:
            if not insecure and isinstance(e.reason, ssl.SSLError):
                continue
            raise Fail("unreachable", f"{type(e.reason).__name__}: {e.reason}"[:200])
        except (TimeoutError, ConnectionError, OSError) as e:
            raise Fail("unreachable", f"{type(e).__name__}: {e}"[:200])
    raise Fail("unreachable", "TLS failure")


def is_pdf(body: bytes, ctype: str) -> bool:
    return body[:5] == b"%PDF-" or "pdf" in ctype.lower()


def pdf_text(iid: str, body: bytes) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{iid}.pdf"
    path.write_bytes(body)
    return pdf_to_text(path)


def pdf_link(body: bytes, base: str) -> str | None:
    soup = BeautifulSoup(body, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href, label = a["href"], a.get_text(" ", strip=True)
        if ".pdf" in href.lower() or "pdf" in label.lower():
            links.append((urljoin(base, href), label))
    hinted = [u for u, label in links if PDF_HINT.search(label + " " + u)]
    return (hinted or [u for u, _ in links] or [None])[0] if len(links) <= 6 else (hinted[0] if hinted else None)


def fetch_one(r: dict) -> dict:
    iid = r["instrument_id"]
    urls = [r["urls"].get("fulltext"), r["urls"].get("official")]
    urls = list(dict.fromkeys(u for u in [cellar(u or "") for u in urls] + urls if u))
    if not urls:
        log_issue(iid, "", "document_not_located", agent="fetch_texts", method="urllib", detail="no URL in registry")
        return {"id": iid, "ok": False, "why": "no url"}
    tried = []
    for url in urls:
        try:
            body, ctype, final, insecure = get(url)
            followed = None
            if is_pdf(body, ctype):
                text = pdf_text(iid, body)
                if len(text) < 200:
                    raise Fail("scanned_no_text", f"PDF without text layer ({len(text)} chars)")
            else:
                text = html_to_text(body)
                if BOT.search(text[:3000]) and len(text) < 20000:
                    raise Fail("bot_block", "bot check or block page")
                if len(text) < MIN_TEXT:
                    link = pdf_link(body, final)
                    if not link:
                        js = len(re.findall(rb"<script", body)) > 5
                        raise Fail("js_rendered" if js else "document_not_located",
                                   f"page has {len(text)} chars of text and no PDF link")
                    time.sleep(PAUSE)
                    body2, ctype2, final2, insecure2 = get(link)
                    if not is_pdf(body2, ctype2):
                        raise Fail("document_not_located", f"landing page; linked file is not a PDF: {link}")
                    text, followed, insecure = pdf_text(iid, body2), final2, insecure or insecure2
                    if len(text) < 200:
                        raise Fail("scanned_no_text", f"linked PDF without text layer: {link}")
                else:
                    RAW.mkdir(parents=True, exist_ok=True)
                    (RAW / f"{iid}.html").write_bytes(body)
            (TEXTS / f"{iid}.txt").write_text(text.strip() + "\n", encoding="utf-8")
            note = []
            if followed:
                note.append(f"PDF linked from {final}")
            if insecure:
                note.append("fetched without TLS certificate verification (site's certificate chain is incomplete)")
            meta = {"source": "official site (connectors/fetch_texts.py)", "url": followed or final,
                    "retrieved": date.today().isoformat(), "version": "as fetched", "extent": "full"}
            if note:
                meta["note"] = "; ".join(note)
            (TEXTS / f"{iid}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
            return {"id": iid, "ok": True, "chars": len(text), "url": followed or final}
        except Fail as f:
            log_issue(iid, url, f.error_type, agent="fetch_texts", method="urllib", detail=f.detail,
                      http_status=f.status, fallbacks=tried.copy())
            tried.append(f"{url}: {f.error_type} ({f.detail})")
        except Exception as e:  # extraction errors and the like
            log_issue(iid, url, "other", agent="fetch_texts", method="urllib", detail=f"{type(e).__name__}: {e}"[:200],
                      fallbacks=tried.copy())
            tried.append(f"{url}: other ({type(e).__name__})")
        time.sleep(PAUSE)
    return {"id": iid, "ok": False, "why": tried[-1] if tried else ""}


def by_site(records: list[dict]) -> list[dict]:
    out = []
    for i, r in enumerate(records):
        if i:
            time.sleep(PAUSE)
        res = fetch_one(r)
        print(("OK   " if res["ok"] else "FAIL ") + f"{res['id']}: " + (f"{res['chars']:,} chars" if res["ok"]
                                                                       else res["why"])[:160], flush=True)
        out.append(res)
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    retry_blocked = "--retry-blocked" in sys.argv
    recs = [json.loads(l) for l in (ROOT / "registry" / "instruments.jsonl").open()]
    todo = [r for r in recs if not (TEXTS / f"{r['instrument_id']}.txt").exists()
            and (not args or r["instrument_id"] in args)
            and (retry_blocked or args or r.get("text_access") != "blocked")]
    sites = defaultdict(list)
    for r in todo:
        sites[urlparse(r["urls"].get("fulltext") or r["urls"].get("official") or "").netloc].append(r)
    print(f"{len(todo)} instruments on {len(sites)} sites", flush=True)
    TEXTS.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as pool:
        results = [x for xs in pool.map(by_site, sites.values()) for x in xs]
    for r in results:  # after the threads: resolve() rewrites the log
        if r["ok"]:
            resolve(r["id"], "fetch_texts", f"text fetched from {r['url']}")
    ok = sum(r["ok"] for r in results)
    print(f"done: {ok} fetched, {len(results) - ok} failed (see registry/access_issues.jsonl; "
          f"python tools/access_log.py report)")


if __name__ == "__main__":
    main()
