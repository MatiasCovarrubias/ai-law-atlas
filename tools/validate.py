"""Validate annotation files: JSON schema, codebook rules, and verbatim quotes.

    python tools/validate.py                     # all annotations/*.json
    python tools/validate.py annotations/x.json  # selected files

Exit code 1 if any file fails. Rules beyond the schema are listed in docs/CODEBOOK.md §6.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quote_check import TEXTS, Doc, iter_quotes, quote_in  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "annotation.schema.json").read_text())
TIER_SECTIONS = {
    "A": ["summary", "classification", "events", "thresholds", "obligations", "enforcement", "definitions", "relations", "entities"],
    "B": ["summary", "classification", "events", "thresholds", "relations"],
    "C": ["summary", "classification", "events"],
}
DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def rule_errors(a: dict) -> list[str]:
    errs = []
    status = a.get("section_status", {})
    for section in TIER_SECTIONS[a["tier"]]:
        present = bool(a.get(section))
        if not present and section not in status:
            errs.append(f"section '{section}' is empty and has no section_status (not_applicable / not_found)")
        if present and section in status:
            errs.append(f"section '{section}' has entries but is also marked {status[section]['status']}")
    c = a["classification"]
    if c["primary_domain"] not in c["domains"]:
        errs.append("primary_domain must be one of domains")
    for i, e in enumerate(a.get("events", [])):
        if e["date"] is not None and not DATE.match(e["date"]):
            errs.append(f"events[{i}].date '{e['date']}' is not YYYY[-MM[-DD]]")
        if e["date_type"] == "external":
            if "external_evidence" not in e:
                errs.append(f"events[{i}] is external but has no external_evidence")
        elif "evidence" not in e:
            errs.append(f"events[{i}] needs evidence (provision + quote) unless date_type is external")
        if e["date_type"] in {"absolute"} and e["date"] is None:
            errs.append(f"events[{i}] is absolute but date is null")
        if e["date_type"] in {"relative", "conditional"} and not e.get("anchor") and not e.get("depends_on"):
            errs.append(f"events[{i}] is {e['date_type']} but has no anchor/depends_on")
    for i, t in enumerate(a.get("thresholds", [])):
        if t["value"] is None and not t.get("depends_on"):
            errs.append(f"thresholds[{i}] has null value but no depends_on (e.g. 'set by decree')")
        if t["operator"] == "range" and t.get("value_max") is None:
            errs.append(f"thresholds[{i}] is a range but has no value_max")
    return errs


def validate(path: Path) -> list[str]:
    a = json.loads(path.read_text(encoding="utf-8"))
    errs = [f"schema: {'/'.join(map(str, e.path))}: {e.message}" for e in Draft202012Validator(SCHEMA).iter_errors(a)]
    if errs:
        return errs
    errs += rule_errors(a)
    if path.stem != a["instrument_id"]:
        errs.append(f"file name {path.name} must match instrument_id {a['instrument_id']}")
    text_path = TEXTS / f"{a['instrument_id']}.txt"
    if not text_path.exists():
        errs.append(f"no cached text at {text_path} (fetch it before validating quotes)")
    else:
        text = Doc(text_path.read_text(encoding="utf-8"))
        errs += [f"quote not found at {p}: {q[:90]}" for p, q in iter_quotes(a) if not quote_in(q, text)]
    return errs


if __name__ == "__main__":
    paths = [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "annotations").glob("*.json"))
    failed = 0
    for p in paths:
        errs = validate(p)
        n = len(list(iter_quotes(json.loads(p.read_text()))))
        print(f"{'OK  ' if not errs else 'FAIL'} {p.name} ({n} quotes)")
        for e in errs:
            print(f"     - {e}")
        failed += bool(errs)
    sys.exit(1 if failed else 0)
