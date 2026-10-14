"""Record pages and files agents could not access, so a human can fetch them.

    python tools/access_log.py report                  # writes build/human_queue.md (open issues, grouped by site)
    python tools/access_log.py seed                    # one-off: import url_verified=false entries from the manifest
    python tools/access_log.py resolve <id> --how manual_download --note "saved PDF from browser"

From code (connectors, scouts, annotators):
    from access_log import log_issue
    log_issue("us-oh-orc-122.175", url, error_type="bot_block", http_status=403, agent="scout-us-dc",
              method="curl", detail="WAF 'Request Rejected'", fallbacks=["wayback: no snapshot"])

Human fix: save the file as cache/raw/<instrument_id>.<html|pdf>, run tools/extract_texts.py <id>, then resolve.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "registry" / "access_issues.jsonl"
ERROR_TYPES = ["bot_block", "js_rendered", "unreachable", "not_found", "paywall_login", "not_online",
               "document_not_located", "scanned_no_text", "garbled_extraction", "other"]


def _read() -> list[dict]:
    """All issues; when the same (instrument_id, url, error_type) was logged more than once, the latest row wins."""
    rows = [json.loads(l) for l in LOG.open() if l.strip()] if LOG.exists() else []
    latest = {(r["instrument_id"], r["url"], r["error_type"]): r for r in rows}
    return list(latest.values())


def _write(rows: list[dict]) -> None:
    with LOG.open("w", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _append(row: dict) -> None:
    """Append under an exclusive lock, so agents logging in parallel never overwrite each other."""
    with LOG.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def log_issue(instrument_id: str, url: str, error_type: str, agent: str, method: str, detail: str = "",
              http_status: int | None = None, fallbacks: list[str] | None = None, resolved_how: str | None = None) -> dict:
    assert error_type in ERROR_TYPES, error_type
    row = {"instrument_id": instrument_id, "url": url, "site": urlparse(url).netloc if url else "",
           "error_type": error_type, "http_status": http_status, "detail": detail, "agent": agent, "method": method,
           "fallbacks_tried": fallbacks or [], "attempted_on": date.today().isoformat(),
           "status": "resolved" if resolved_how else "open", "resolved_how": resolved_how, "resolved_on": None,
           "note": ""}
    _append(row)  # duplicates of (instrument_id, url, error_type) collapse on read; `report` and `resolve` rewrite compactly
    return row


def classify(note: str) -> tuple[str, int | None]:
    n = note.lower()
    status = int(m[1]) if (m := re.search(r"\b(4\d\d|5\d\d)\b", n)) else None
    if re.search(r"no official|not online|gazette not online", n):
        return "not_online", status
    if re.search(r"not located|could not be located|not found on", n):
        return "document_not_located", status
    if re.search(r"javascript|js[- ]rendered|js app|single-page", n):
        return "js_rendered", status
    if re.search(r"403|cloudflare|akamai|bot|waf|429|request rejected|challenge", n):
        return "bot_block", status
    if re.search(r"refused|unreachable|timeout|econnrefused|connection failure", n):
        return "unreachable", status
    return "other", status


def seed() -> None:
    added = 0
    for line in (ROOT / "registry" / "manifest_v0.jsonl").open():
        r = json.loads(line)
        if r.get("url_verified"):
            continue
        etype, status = classify(r.get("notes") or "")
        log_issue(r["id"], r.get("fulltext_url") or r.get("official_url") or "", etype, agent=f"scout:{r.get('scout')}",
                  method="curl/WebFetch", detail=(r.get("notes") or "")[:300], http_status=status)
        added += 1
    # Acquisition issues met in the econ-semantic-extraction PoC, solved by fallbacks.
    log_issue("us-ca-sb53-2025", "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=202520260SB53",
              "bot_block", agent="poc-fetch", method="curl", detail="Cloudflare bot check",
              fallbacks=["wayback 20260301 snapshot (chaptered text)"], resolved_how="wayback")
    log_issue("kr-ai-basic-act-2025", "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=268543",
              "js_rendered", agent="poc-fetch", method="curl", detail="Body loaded by JavaScript",
              fallbacks=["engLsInfoR.do endpoint"], resolved_how="alternate_endpoint")
    print(f"seeded {added} open issues from the manifest (+2 resolved PoC cases)")


def resolve(instrument_id: str, how: str, note: str) -> None:
    """Append the resolved version of each open row (the latest row wins on read), so that resolving never races
    with agents appending in parallel; `report` rewrites the file compactly."""
    for r in _read():
        if r["instrument_id"] == instrument_id and r["status"] == "open":
            _append({**r, "status": "resolved", "resolved_how": how, "resolved_on": date.today().isoformat(),
                     "note": note})


def report() -> None:
    rows = _read()
    open_rows = [r for r in rows if r["status"] == "open"]
    by_site = defaultdict(list)
    for r in open_rows:
        by_site[r["site"] or "(no URL)"].append(r)
    lines = ["# Human queue: documents agents could not access", "",
             f"Generated {date.today().isoformat()} from `registry/access_issues.jsonl`. "
             f"{len(open_rows)} open, {len(rows) - len(open_rows)} resolved.", "",
             "**How to fix one:** open the URL in a browser and save the page or PDF as "
             "`cache/raw/<instrument_id>.html` or `.pdf`. Then run `python tools/extract_texts.py <instrument_id>` "
             "and `python tools/access_log.py resolve <instrument_id> --how manual_download`. If the document "
             "is not online, note where it can be found (gazette, library) with `--how unavailable --note ...`.", ""]
    counts = defaultdict(int)
    for r in open_rows:
        counts[r["error_type"]] += 1
    lines += ["| Error type | Open |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items(), key=lambda x: -x[1])] + [""]
    for site, items in sorted(by_site.items(), key=lambda x: -len(x[1])):
        lines.append(f"## {site} ({len(items)})")
        lines += [f"- [ ] `{r['instrument_id']}` ({r['error_type']}): {r['url'] or 'no URL'}" for r in items]
        lines.append("")
    out = ROOT / "build" / "human_queue.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(open_rows)} open issues across {len(by_site)} sites -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report", "seed", "resolve"])
    ap.add_argument("instrument_id", nargs="?")
    ap.add_argument("--how", default="manual_download")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    {"report": report, "seed": seed}.get(a.cmd, lambda: resolve(a.instrument_id, a.how, a.note))()
