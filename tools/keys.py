"""Canonical instrument keys extracted from URLs and titles (step 1 of entity resolution).

A key identifies a legal instrument independently of which corpus recorded it, e.g.
    EU:REG:2024:1689        US:EO:14110        US:FR:2025-00636       US:PL:116-283
    US-CA:BILL:2025:SB53    UK:ukpga:2023:50   US:BILL:118:HR:1234

    python tools/keys.py   # prototype: match AGORA and our manifest on keys
"""

from __future__ import annotations

import json
import re
from pathlib import Path

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
SPELLED = {"Senate Bill": "SB", "House Bill": "HB", "Assembly Bill": "AB"}
CONGRESS_TYPES = {"house-bill": "HR", "senate-bill": "S", "house-joint-resolution": "HJRES",
                  "senate-joint-resolution": "SJRES"}


def keys_from_url(url: str) -> set[str]:
    url = str(url)
    keys = set()
    if m := re.search(r"CELEX[:%3A]+3(\d{4})([RLD])(\d{4})", url, re.I):
        keys.add(f"EU:{ {'R': 'REG', 'L': 'DIR', 'D': 'DEC'}[m[2].upper()] }:{m[1]}:{int(m[3])}")
    if m := re.search(r"eli/(reg|dir|dec)(?:_impl|_del)?/(\d{4})/(\d+)", url, re.I):
        keys.add(f"EU:{m[1].upper()}:{m[2]}:{int(m[3])}")
    if m := re.search(r"legislation\.gov\.uk/(ukpga|uksi|asp|ssi|wsi)/(\d{4})/(\d+)", url):
        keys.add(f"UK:{m[1]}:{m[2]}:{m[3]}")
    if m := re.search(r"congress\.gov/bill/(\d+)\w*-congress/([a-z-]+)/(\d+)", url):
        if m[2] in CONGRESS_TYPES:
            keys.add(f"US:BILL:{m[1]}:{CONGRESS_TYPES[m[2]]}:{m[3]}")
    if m := re.search(r"congress\.gov/(\d+)/bills/(hr|s|hjres|sjres)(\d+)/", url):  # bill text files
        keys.add(f"US:BILL:{m[1]}:{m[2].upper()}:{m[3]}")
    if m := re.search(r"PLAW-(\d+)publ(\d+)", url, re.I):
        keys.add(f"US:PL:{m[1]}-{m[2]}")
    if m := re.search(r"(?:federalregister\.gov/documents/\d{4}/\d\d/\d\d/|/pdf/)(\d{4}-\d{4,5})", url):
        keys.add(f"US:FR:{m[1]}")
    if m := re.search(r"bill_id=(\d{4})\d{4}\d([A-Z]+)(\d+)", url):  # leginfo.legislature.ca.gov
        keys.add(f"US-CA:BILL:{m[1]}:{m[2]}{int(m[3])}")
    return keys


def keys_from_title(title: str, jurisdiction: str = "") -> set[str]:
    t = str(title)
    keys = set()
    if m := re.search(r"(?:Executive Order|EO|E\.O\.)\s*(?:No\.\s*)?(1[34]\d{3})", t):
        keys.add(f"US:EO:{m[1]}")
    if m := re.search(r"Pub(?:lic|\.)\s*L(?:aw|\.)\s*(?:No\.\s*)?(\d{2,3})[-–](\d+)", t):
        keys.add(f"US:PL:{m[1]}-{m[2]}")
    if m := re.search(r"Regulation \(EU\) (\d{4})/(\d+)", t):
        keys.add(f"EU:REG:{m[1]}:{int(m[2])}")
    if m := re.search(r"Directive \(EU\) (\d{4})/(\d+)", t):
        keys.add(f"EU:DIR:{m[1]}:{int(m[2])}")
    state = next((code for name, code in US_STATES.items()
                  if re.search(rf"\b{name}\b", f"{jurisdiction} {t}", re.I)), None)
    if state and (m := re.search(r"\b(Senate Bill|House Bill|Assembly Bill|S\.?B\.?|H\.?B\.?|A\.?B\.?|HCR|SCR|HJR|SJR"
                                 r"|SF|HF|LB|S|A|H)\s?-?(\d{1,2}[A-Z]?-)?(\d{1,5})\b", t)):
        prefix = SPELLED.get(m[1], re.sub(r"[.\s]", "", m[1]).upper())
        year = re.search(r"\b(20[12]\d)\b", t)
        keys.add(f"US-{state}:BILL:{prefix}{int(m[3])}")  # session-free key; a year is added when known
        if year:
            keys.add(f"US-{state}:BILL:{year[1]}:{prefix}{int(m[3])}")
    return keys


CITATION = re.compile(r"\b(amending|amends|implementing|supplementing|under|pursuant to|established by|laid down in"
                      r"|on the application of|guidelines on|to Regulation|of Regulation|to Directive)\b|\(Division", re.I)


def own_title(title: str) -> str:
    """The part of a title before the first citation of another act, so that cited acts are not taken as own keys."""
    m = CITATION.search(str(title))
    return str(title)[:m.start()] if m else str(title)


def federal_bill_from_title(title: str) -> tuple[str, int] | None:
    """First US federal bill number in a title ('HR 5466', 'S. 3849', 'S3454'), as (type, number)."""
    if m := re.search(r"\b(H\.?\s?R|S)\.?\s?(\d{1,5})\b", str(title)):
        return ("HR" if m[1].startswith("H") else "S", int(m[2]))
    return None


def prototype() -> None:
    import pandas as pd

    root = Path(__file__).resolve().parents[1]
    docs = pd.read_csv(root / "cache" / "agora" / "agora" / "documents.csv", low_memory=False)
    agora = {}
    for _, r in docs.iterrows():
        k = keys_from_url(r["Link to document"]) | keys_from_title(f"{r['Official name']} | {r['Casual name']}", str(r["Authority"]))
        agora[r["AGORA ID"]] = k
    ours = {}
    for line in (root / "registry" / "manifest_v0.jsonl").open():
        o = json.loads(line)
        ours[o["id"]] = (keys_from_url(o.get("official_url")) | keys_from_url(o.get("fulltext_url"))
                         | keys_from_title(f"{o['title']} | {o.get('short_title', '')}", o.get("jurisdiction", "")))
    index: dict[str, set] = {}
    for aid, ks in agora.items():
        for k in ks:
            index.setdefault(k, set()).add(aid)
    matched = {oid: set().union(*(index.get(k, set()) for k in ks)) for oid, ks in ours.items() if ks}
    matched = {k: v for k, v in matched.items() if v}
    print(f"AGORA records with >=1 key: {sum(bool(k) for k in agora.values())}/{len(agora)}")
    print(f"manifest entries with >=1 key: {sum(bool(k) for k in ours.values())}/{len(ours)}")
    print(f"manifest entries matched to AGORA by key: {len(matched)}")
    verified = set(pd.read_csv(root / "registry" / "crosswalk" / "agora_overlap_verified.csv")["id"])
    print(f"  of the verified overlaps (agora_overlap_verified.csv), found by key: {len(verified & set(matched))}")
    print(f"  key matches not in the verified list (new or false): {sorted(set(matched) - verified)}")
    multi = {k: v for k, v in matched.items() if len(v) > 1}
    print(f"  manifest entries mapping to several AGORA records (parts/versions): {len(multi)}")


if __name__ == "__main__":
    prototype()
