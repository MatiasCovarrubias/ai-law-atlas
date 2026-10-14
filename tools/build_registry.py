"""Build registry v1: canonical instruments, typed links, families and a coverage report.

Inputs (in git, except the AGORA cache):
    registry/manifest_v0.jsonl                      seed records from the Oct 2026 scouting (frozen)
    registry/additions.jsonl                        instruments added later by agents (instrument schema); optional
    registry/overrides.jsonl                        manual corrections: {"instrument_id", "set", "unset", "reason", "source"}
    registry/crosswalk/agora_overlap_verified.csv   adjudicated manifest <-> AGORA relations (Oct 2026 crosswalk)
    registry/crosswalk/agora_manual.csv             later adjudications: duplicates, drafts (version_of), groups
    registry/links_manual.csv                       adjudicated relations between instruments; optional
    cache/agora/agora/documents.csv                 AGORA (run connectors/agora.py first)

Outputs (generated; never edit by hand, change the inputs and rebuild):
    registry/instruments.jsonl, registry/links.csv, registry/families.jsonl, registry/coverage.md,
    registry/crosswalk/agora_excluded.csv

    python tools/build_registry.py

AGORA is CC BY-NC 4.0. From it we keep only facts needed to identify an instrument (names, links, dates,
Enacted/Proposed/Defunct) and never its summaries, tags or segments. Types, stages and domains of AGORA-only
instruments are inferred by the rules below and labelled as such in `provenance`.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pandas as pd
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from keys import US_STATES, federal_bill_from_title, keys_from_title, keys_from_url, own_title  # noqa: E402

REG = ROOT / "registry"
AGORA = ROOT / "cache" / "agora" / "agora"
TODAY = date.today().isoformat()
VOCAB = json.loads((ROOT / "schemas" / "vocab.json").read_text())

# Regions (PLAN.md section 2)
P1_EUROPE = {"EU", "DE", "FR", "IT", "ES", "NL", "IE", "SE", "DK", "FI", "NO", "IS", "GB"}
P1_LATAM = {"BR", "MX", "CL", "CO", "AR", "PE", "UY"}
P2 = {"CA", "JP", "KR", "IN", "SG", "AU", "CH", "AE", "SA"}
EU_EEA = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
          "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO"}
REGIONS = ["P1-US", "P1-Europe", "P1-LATAM", "P1-China", "P2", "P3"]
STAGES = VOCAB["legislative_stages"]

ADOPTED = {"adopted", "in_force", "partially_in_force", "adopted_not_yet_in_force", "suspended", "repealed", "struck_down"}
DEAD = {"failed", "vetoed", "withdrawn", "lapsed"}

TOPIC_DOMAINS = {
    "data_protection": ["data_protection"], "data_centers": ["compute_data_centers"], "ai_general": ["ai_general"],
    "genai_content": ["generative_ai"], "platforms_competition": ["platforms_competition"],
    "compute_energy": ["compute_data_centers", "energy_grid_water"], "ai_sector": ["ai_sectoral"],
    "semiconductors_export": ["chips_export_controls"], "algorithms": ["algorithmic_decision_systems"],
    "cybersecurity": ["cybersecurity"], "biometrics": ["biometrics"], "data_access": ["data_access_sharing"],
}
# Title keywords -> domains, for instruments without scout topics (AGORA-only). Coarse by design; the annotation
# pass assigns domains from the text.
DOMAIN_RULES = [
    ("synthetic_media_deepfakes", r"deepfake|synthetic (media|content)|deceptive media|digital(ly)? (altered|replica)|likeness|deep synthesis|manipulated media"),
    ("elections_political_ads", r"election|campaign|political|candidate|electoral|voter"),
    ("minors_child_safety", r"\bchild|\bminor|student|youth|school|\bkids?\b|teen"),
    ("employment_labor", r"employ|hiring|worker|workforce|\blabou?r\b|\bjobs?\b|interview"),
    ("generative_ai", r"generative|chatbot|companion|large language|foundation model|frontier"),
    ("algorithmic_decision_systems", r"algorithm|automated decision|high-risk|consequential decision|profiling"),
    ("biometrics", r"biometric|facial recognition|face recognition"),
    ("data_protection", r"privacy|personal data|personal information|data protection"),
    ("cybersecurity", r"cyber"),
    ("chips_export_controls", r"export|\bchips?\b|semiconductor|diffusion"),
    ("investment_screening", r"outbound investment|inbound investment|\bICTS\b|CFIUS|foreign adversar"),
    ("compute_data_centers", r"data cent(er|re)|\bcompute\b|computing|\bcloud\b|IaaS"),
    ("energy_grid_water", r"energy|\bgrid\b|electric|utilit|\bwater\b"),
    ("ip_training_data", r"copyright|intellectual property|training data|inventorship|patent"),
    ("consumer_protection", r"consumer|unfair|deceptive (trade|practice)"),
    ("public_sector_ai", r"government|agenc(y|ies)|federal use|state use|public sector|procurement|acquisition|city use|public service"),
    ("ai_sectoral", r"health|medical|insurance|credit|financ|housing|transport|vehicle|drone|aviation|defen[cs]e|military|weapon|police|law enforcement|education|agricultur"),
]

CONNECTORS = [("eur-lex.europa.eu", "eurlex"), ("legislation.gov.uk", "legislation_uk"),
              ("federalregister.gov", "federal_register"), ("govinfo.gov", "govinfo"), ("congress.gov", "congress"),
              ("law.go.kr", "law_go_kr"), ("lexml.gov.br", "lexml")]
LANG = {"US": "en", "GB": "en", "CA": "en", "AU": "en", "NZ": "en", "SG": "en", "INT": "en", "CN": "zh", "BR": "pt",
        "TR": "tr", "IL": "he", "UA": "uk", "AE": "ar", "GH": "en"}

# AGORA authority -> place
STATE_ISO = {name.title(): f"US-{code}" for name, code in US_STATES.items()} | {"District Of Columbia": "US-DC"}
GOV_ISO = {"Turkey": "TR", "Canada": "CA", "Singapore": "SG", "New Zealand": "NZ", "the United Arab Emirates": "AE",
           "Australia": "AU", "Israel": "IL", "the United Kingdom": "GB"}
ISO_NAME = {"US": "United States", "CN": "China", "EU": "European Union", "INT": "International", "BR": "Brazil",
            "UA": "Ukraine", "GH": "Ghana", "AE-DU": "United Arab Emirates (Dubai, DIFC)"} | {v: k.removeprefix("the ") for k, v in GOV_ISO.items()}
NAME_ISO = [(r"\bBrazil", "BR"), (r"\bUkraine", "UA"), (r"\bGhana", "GH"), (r"\bU\.S\.|\bUnited States", "US"),
            (r"\bChina\b|Chinese|TC260", "CN")]
AGORA_ISO_BY_ID = {"1390": "CN", "1373": "CN"}  # CESI and TC260 white papers whose titles do not name the country
CN_PROVINCES = [(r"Shenzhen|Guangdong", "CN-GD", "China (Guangdong)"), (r"Shanghai", "CN-SH", "China (Shanghai)"),
                (r"Beijing", "CN-BJ", "China (Beijing)")]
MULTINATIONAL = {"United Nations", "OECD", "Other multinational"}

# AGORA name -> instrument type (first match wins; see agora_type)
EO_RE = re.compile(r"Executive Order|\bEO\b|Executive Directive|Administrative Order|Mayor'?s Order", re.I)
BILL_RE = re.compile(r"\bAct\b|\bBill\b|\bLaw\b|\b(H|S|A)\.?\s?B?\.?\s?\d|\b(HF|SF|LB)\s?\d|Amendment S\.", re.I)
TYPE_RULES = [
    ("other", r"Scholars' Draft|Position Paper|Remarks by|Concept Paper|Consensus Statement|IDAIS|White Paper|Companion document|Interim Response"),
    ("treaty", r"\bConvention\b"),
    ("administrative_rule", r"Proposed Rule|\bCFR\b|§|\bRule\b|Rules Amendment|Part \d+:|Safety Requirements"),  # US only
    ("decision", r"\bDecision\b"),
    ("ordinance", r"Ordinance|Local Law|Council No\.|Board Bill|Proposal No\."),
    ("bill", r"\bAct\b|\bBill\b|\bLaw\b"),
    ("regulation", r"Measures|Provisions|Regulations?\b"),
    ("strategy", r"Strateg|Action Plan|Development Plan|Roadmap|Blueprint|\bPlan\b|Initiative\b"),
    ("guidance", r"Guid|Framework|Principles|Polic|Code of (Practice|Conduct)|Norms|Opinions?\b|Notice|Memo|Advice|Standard|Requirements|Declaration|Resolution|Statement|Commitments|Recommendation|Approach|Directive on"),
]
DRAFT_RE = re.compile(r"Draft for (Comment|Feedback)|Proposed Rule|Exposure Draft|Pre-Publication|\(Draft", re.I)
# Section markers in AGORA names: "..., Section 6601", "... (Title IX ...", "(\"Part U, ..."
SECTION_RE = re.compile(r"[,(]\s*[\"“]?\s*(Sec\.?|SEC\.|Section|Title|Division|Subtitle|Part)\s*[\dIVXLA-Z]")
SECTION_CUT = re.compile(r"\s*(?:,\s*|\(\s*[\"“]?\s*)(?:Sec\.?|SEC\.|Section|Title|Division|Subtitle|Part)\s*[\dIVXLA-Z].*$")
OMNIBUS_RE = re.compile(r"Appropriations Act|National Defense Authorization Act")
SESSION_FREE = re.compile(r"^US-[A-Z]{2}:BILL:[A-Z]+\d+$")

FAMILIES = [
    dict(family_id="us-state-comprehensive-privacy", title="US state comprehensive consumer privacy laws and bills",
         scope="us_state", pattern=r"consumer (data )?privacy|data privacy|personal data protection|privacy act|privacy protection act|consumer data protection|personal information protection",
         also_pattern=r"information protection act|digital bill of rights",
         exclude=r"student|children|\bchild|health|biometric|genetic|broadband|driver|employee"),
    dict(family_id="us-state-election-deepfakes", title="US state laws and bills on AI and deepfakes in elections",
         scope="us_state", pattern=r"election|campaign|political|candidate|electoral|voter",
         also=r"deepfake|deceptive|synthetic|artificial intelligence|\bAI\b|manipulated|digital(ly)? (altered|content)"),
    dict(family_id="us-state-intimate-deepfakes", title="US state laws and bills on AI-generated intimate or sexual images",
         scope="us_state", pattern=r"intimate|sexual|explicit|nude|pornograph|child sexual abuse",
         also=r"deepfake|synthetic|artificial|\bAI\b|generated|altered|morphed|digiti"),
    dict(family_id="us-state-digital-replicas", title="US state laws and bills on digital replicas and likeness",
         scope="us_state", pattern=r"digital replica|likeness|\bELVIS\b|right of publicity|voice and image"),
    dict(family_id="us-state-high-risk-ai", title="US state high-risk AI and algorithmic discrimination laws and bills (Colorado model)",
         scope="us_state", pattern=r"high-risk|algorithmic discrimination|consequential decision|automated decision",
         include=["us-co-sb24-205", "us-co-sb25b-004"]),
    dict(family_id="us-state-frontier-ai-safety", title="US state frontier-model safety laws and bills",
         scope="us_state", pattern=r"frontier|foundation model|\bRAISE\b|large developer|critical (safety )?risk|Safe and Secure Innovation"),
    dict(family_id="us-state-ai-companions-chatbots", title="US state laws and bills on AI chatbots and companions",
         scope="us_state", pattern=r"chatbot|companion"),
    dict(family_id="us-state-data-center-tax", title="US state data-center tax treatment: exemptions, abatements and special taxes",
         scope="us_state", domain="compute_data_centers", pattern=r"\btax|exempt|incentive|abatement|credit|sales and use"),
    dict(family_id="europe-national-ai-laws", title="European national AI laws and bills (EU/EEA members and the UK), including AI Act implementation acts",
         scope="europe_national", types={"statute", "bill", "regulation", "decision", "implementing_act", "administrative_rule"},
         pattern=r"AI Act|2024/1689|\bKI\b|KI-|künstliche Intelligenz|intelligenza artificiale|inteligencia artificial|intelligence artificielle|artificial intelligence|kunstmatige intelligentie|tekoäly|kunstig intelligens|artificiell intelligens|sztuczn"),
]


# ---------------------------------------------------------------- helpers

def clean(x):
    """Drop None, empty strings, lists and dicts, recursively."""
    if isinstance(x, dict):
        x = {k: clean(v) for k, v in x.items()}
        return {k: v for k, v in x.items() if v not in (None, "", [], {})}
    return x


def priority_region(iso: str) -> str:
    c = iso.split("-")[0]
    return ("P1-US" if c == "US" else "P1-Europe" if c in P1_EUROPE else "P1-LATAM" if c in P1_LATAM
            else "P1-China" if c == "CN" else "P2" if c in P2 else "P3")


def stage_of(status: str) -> str:
    return "adopted" if status in ADOPTED else "dead" if status in DEAD else "introduced"


def connector(*urls) -> str | None:
    for u in urls:
        for host, name in CONNECTORS:
            if host in (u or ""):
                return name
    return None


def title_domains(text: str) -> list[str]:
    found = [d for d, pat in DOMAIN_RULES if re.search(pat, text, re.I)]
    return found or ["ai_general"]


def norm_link(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.strip().lower().split("#")[0]).rstrip("/")


def norm_title(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", t.lower()))


def slug(text: str, n: int = 6) -> str:
    stop = {"the", "of", "and", "for", "on", "in", "a", "an", "to", "by", "with"}
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop]
    return "-".join(words[:n])


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open() if line.strip()] if path.exists() else []


def read_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open())) if path.exists() else []


def set_path(rec: dict, path: str, value) -> None:
    *head, last = path.split(".")
    for k in head:
        rec = rec.setdefault(k, {})
    rec[last] = value


def unset_path(rec: dict, path: str) -> None:
    *head, last = path.split(".")
    for k in head:
        rec = rec.get(k, {})
    rec.pop(last, None)


# ---------------------------------------------------------------- our records

def manifest_keys(o: dict) -> set[str]:
    ks = keys_from_url(o.get("official_url", "")) | keys_from_url(o.get("fulltext_url", ""))
    return ks | keys_from_title(own_title(o["title"]), o.get("jurisdiction", ""))


def from_manifest(o: dict) -> dict:
    status = {"proposed": "pending"}.get(o["status"], o["status"])
    dates = {}
    if o.get("date_adopted"):  # scouts used date_adopted for proposal and veto dates too
        dates["adopted" if status in ADOPTED else "proposed" if status == "pending" else "last_action"] = o["date_adopted"]
    if o.get("date_effective") and status in ADOPTED:
        dates["entry_into_force"] = o["date_effective"]
    urls = {k: o.get(f) for k, f in (("official", "official_url"), ("fulltext", "fulltext_url"), ("english", "english_url"))}
    return clean({
        "instrument_id": o["id"], "title": o["title"], "short_title": o.get("short_title"),
        "jurisdiction": o["jurisdiction"], "iso": o["iso"], "level": o["level"],
        "priority_region": priority_region(o["iso"]),
        "instrument_type": "bill" if o["instrument_type"] == "statute" and status not in ADOPTED else o["instrument_type"],
        "status": status,
        "legislative_stage": stage_of(status), "dates": dates,
        "domains": sorted({d for t in o.get("topics", []) for d in TOPIC_DOMAINS.get(t, [])}),
        "keys": sorted(manifest_keys(o)), "language": o.get("language"), "urls": urls,
        "text_connector": connector(o.get("fulltext_url"), o.get("official_url")) or "generic",
        "provenance": {
            "added_by": f"scout:{o['scout']}", "added_on": "2026-10-11", "sources": [o.get("official_url")],
            "verified": bool(o.get("url_verified")), "notes": o.get("notes"),
            "stage_basis": "default" if status == "pending" else "status", "type_basis": "scout",
            "domains_basis": "scout_topics"},
    })


# ---------------------------------------------------------------- AGORA

def agora_place(r: dict, parent: dict) -> tuple[str, str, str] | None:
    a, name, aid = r["Authority"], f"{r['Casual name']} {r['Official name']}", r["AGORA ID"]
    if a == "Private-sector companies":
        return None
    if re.search(r"\bDIFC\b", name):  # filed under the UK government in AGORA
        return "AE-DU", "subnational", ISO_NAME["AE-DU"]
    if aid in AGORA_ISO_BY_ID:
        iso = AGORA_ISO_BY_ID[aid]
        return iso, "national", ISO_NAME[iso]
    if a.title() in STATE_ISO:
        return STATE_ISO[a.title()], "subnational", a
    if m := re.fullmatch(r"(.+), ([A-Z]{2})", a):
        return f"US-{m[2]}", "municipal", a
    if a in ("Federal government", "United States Congress") or parent.get(a) == "Federal government":
        return "US", "national", "United States"
    if a == "Chinese central government":
        return "CN", "national", "China"
    if a.startswith("Chinese provincial"):
        for pat, iso, label in CN_PROVINCES:
            if re.search(pat, name):
                return iso, "subnational", label
        return "CN", "subnational", "China (provincial)"
    if a == "European Union":
        return "EU", "supranational", "European Union"
    if a.startswith("Government of "):
        iso = GOV_ISO[a.removeprefix("Government of ")]
        return iso, "national", ISO_NAME[iso]
    if a == "Other authorities":
        for pat, iso in NAME_ISO:
            if re.search(pat, name):
                return iso, "national", ISO_NAME[iso]
    return "INT", "supranational", "International"


def agora_type(name: str, authority: str, enacted: bool, iso: str) -> str:
    if EO_RE.search(name):
        return "executive_order"
    legislature = authority.title() in STATE_ISO or authority == "United States Congress" or (
        authority == "Federal government" and re.search(r"\bAct\b", name))
    if legislature:
        return "statute" if enacted else "bill"
    for itype, pat in TYPE_RULES:
        if itype == "administrative_rule" and not iso.startswith("US"):
            continue
        if re.search(pat, name, re.I):
            return ("statute" if enacted else "bill") if itype == "bill" else itype
    return "other"


def agora_status(activity: str, itype: str, name: str) -> tuple[str, str, str]:
    """(status, legislative_stage, stage_basis) from AGORA's Enacted / Proposed / Defunct."""
    draft = itype in ("regulation", "administrative_rule", "decision", "guidance") and DRAFT_RE.search(name)
    if activity == "Enacted":
        return "adopted", "adopted", "agora_status"
    if activity == "Proposed":
        return ("pending", "advanced", "agora_status+title") if draft else ("pending", "introduced", "agora_status")
    if itype in ("bill", "statute"):
        return "failed", "dead", "agora_status"
    if draft:
        return "withdrawn", "dead", "agora_status+title"
    return "repealed", "adopted", "agora_status"  # a defunct executive order, rule or guidance was adopted, then revoked


def group_key(r: dict, review: dict) -> tuple[str, set[str]]:
    """Records that share a canonical key (or, failing that, a link) are parts of one instrument.

    AGORA's link, plaintext source and name sometimes cite different federal bills (two GUARD Act bills share
    one link). The bill named by at least two of the three wins; on a tie the link wins.
    """
    ks = keys_from_url(r["Link to document"])
    bill = next((k for k in sorted(ks) if k.startswith("US:BILL:")), None)
    if bill:
        text = next((k for k in sorted(keys_from_url(r["Official plaintext source"])) if k.startswith("US:BILL:")), None)
        fb = federal_bill_from_title(r["Casual name"])
        name = f"US:BILL:{bill.split(':')[2]}:{fb[0]}:{fb[1]}" if fb else None
        votes = Counter(k for k in (bill, text, name) if k)
        winner, n = votes.most_common(1)[0]
        winner = winner if n >= 2 else bill
        if len(votes) > 1:
            review["AGORA records whose link, plaintext source and name cite different bills"].append(
                f"agora:{r['AGORA ID']} {r['Casual name'][:50]} | link {bill} | text {text} | name {name} -> {winner}")
        if winner != bill:
            ks = {winner}
    return (min(ks), ks) if ks else (norm_link(r["Link to document"]), ks)


def base_title(name: str, multi: bool) -> str:
    b = SECTION_CUT.sub("", name).strip(" ,;")
    if multi:
        b = re.sub(r"\s*\([^()]*\)\s*$", "", b) or b
    return b


def group_title(recs: list[dict], field: str) -> str:
    multi = len(recs) > 1
    bases = [base_title(r[field], multi) for r in recs]
    preferred = [b for b in bases if OMNIBUS_RE.search(b)]
    return Counter(preferred or bases).most_common(1)[0][0]


def same_name(rec: dict, base: str) -> bool:
    """Does our record name the whole act (not one of its parts)? Compares title starts in both directions."""
    for t in (norm_title(rec["title"]), norm_title(rec.get("short_title", ""))):
        if len(t) >= 15 and (t.startswith(base[:40]) or base.startswith(t[:40])):
            return True
    return False


def is_section(r: dict) -> bool:
    return bool(SECTION_RE.search(r["Casual name"]) or SECTION_RE.search(r["Official name"]))


def make_id(iso: str, keys: set[str], short: str, year: str | None) -> str:
    patterns = [
        (r"US:EO:(\d+)$", lambda m: f"us-eo-{m[1]}"),
        (r"US:PL:(\d+)-(\d+)$", lambda m: f"us-pl{m[1]}-{m[2]}"),
        (r"US:FR:([\d-]+)$", lambda m: f"us-fr-{m[1]}"),
        (r"EU:(REG|DIR|DEC):(\d{4}):(\d+)$", lambda m: f"eu-{m[1].lower()}-{m[2]}-{m[3]}"),
        (r"UK:(\w+):(\d{4}):(\d+)$", lambda m: f"uk-{m[1]}-{m[2]}-{m[3]}"),
        (r"US-([A-Z]{2}):BILL:(\d{4}):([A-Z]+\d+)$", lambda m: f"us-{m[1].lower()}-{m[3].lower()}-{m[2]}"),
        (r"US:BILL:(\d+):([A-Z]+):(\d+)$", lambda m: f"us-{m[2].lower()}{m[3]}-{m[1]}"),
        (r"US-([A-Z]{2}):BILL:([A-Z]+\d+)$", lambda m: f"us-{m[1].lower()}-{m[2].lower()}-{year}" if year else None),
    ]
    for pat, fmt in patterns:
        for k in sorted(keys):
            if (m := re.match(pat, k)) and (out := fmt(m)):
                return out
    prefix = {"GB": "uk"}.get(iso, iso.lower())  # our IDs use uk-, as legislation.gov.uk does
    return "-".join(x for x in (prefix, slug(short), year) if x)


def load_agora() -> tuple[list[dict], dict, str]:
    docs = pd.read_csv(AGORA / "documents.csv", dtype=str, keep_default_na=False)
    auth = pd.read_csv(AGORA / "authorities.csv", dtype=str, keep_default_na=False)
    version = (AGORA.parent / "VERSION").read_text().strip()
    return docs.to_dict("records"), dict(zip(auth["Name"], auth["Parent authority"])), version


# ---------------------------------------------------------------- build

def build() -> dict:
    docs, parent, agora_version = load_agora()
    old_links = read_csv(REG / "links.csv")
    old_target = {l["from"]: l["to"].removeprefix("instrument:") for l in old_links if l["from"].startswith("agora:")}
    old_added = {i["instrument_id"]: i.get("provenance", {}).get("added_on") for i in read_jsonl(REG / "instruments.jsonl")}

    instruments: dict[str, dict] = {}
    for o in read_jsonl(REG / "manifest_v0.jsonl"):
        instruments[o["id"]] = from_manifest(o)
    for rec in read_jsonl(REG / "additions.jsonl"):
        instruments[rec["instrument_id"]] = rec
    ours = set(instruments)

    links: list[dict] = []
    review: dict[str, list[str]] = defaultdict(list)

    def link(frm, relation, to, method, note="", confidence=1.0):
        links.append({"from": frm, "relation": relation, "to": f"instrument:{to}", "method": method,
                      "confidence": confidence, "note": note})

    # adjudicated manifest <-> AGORA relations
    verified = defaultdict(list)
    for row in read_csv(REG / "crosswalk" / "agora_overlap_verified.csv"):
        verified[row["agora_id"]].append(row)

    # exact-key index of our records (session-free state keys are too ambiguous to merge on)
    key_index = defaultdict(set)
    for iid, rec in instruments.items():
        for k in rec.get("keys", []):
            if not SESSION_FREE.match(k):
                key_index[k].add(iid)

    manual = {row["agora_id"]: row for row in read_csv(REG / "crosswalk" / "agora_manual.csv")}
    late = []  # records linked after grouping: drafts (version_of) and duplicates of other AGORA records

    excluded, groups = [], defaultdict(list)
    for r in docs:
        place = agora_place(r, parent)
        if place is None:
            excluded.append({"agora_id": r["AGORA ID"], "name": r["Casual name"], "reason": "corporate policy (not law)"})
            continue
        r["_place"] = place
        row = manual.get(r["AGORA ID"])
        if row and (row["relation"] == "version_of" or row["target"].startswith("agora:")):
            late.append((r, row))
            continue
        gk, ks = group_key(r, review)
        r["_keys"] = ks
        groups[gk].append(r)

    agora_of_instrument = defaultdict(set)
    instrument_of_record = {}
    deferred = []  # (manifest_id, relation, agora_id) for implements / amends / codifies

    for gk, recs in groups.items():
        recs.sort(key=lambda r: r["AGORA ID"])
        multi = len(recs) > 1
        matched = [(r, row) for r in recs for row in verified.get(r["AGORA ID"], [])
                   if row["relation"] in ("same", "agora_is_part_of")]
        for r in recs:
            for row in verified.get(r["AGORA ID"], []):
                if row["relation"] not in ("same", "agora_is_part_of"):
                    deferred.append((row["id"], row["relation"], r["AGORA ID"], row["method"]))
        target, part_ids = None, set()
        forced = [manual[r["AGORA ID"]] for r in recs if r["AGORA ID"] in manual]
        if forced:
            target, method, note = forced[0]["target"], "manual", "adjudicated (agora_manual.csv)"
        elif matched:
            r0, row = matched[0]
            m = row["id"]
            parent_base = norm_title(group_title(recs, "Official name"))
            whole = not multi or not is_section(r0) or same_name(instruments[m], parent_base)
            if whole:
                target = m
            else:  # our instrument is one part (e.g., the National AI Initiative Act inside the FY2021 NDAA)
                link(f"agora:{r0['AGORA ID']}", "same", m, row["method"], f"adjudicated; {r0['Casual name'][:100]}")
                instrument_of_record[r0["AGORA ID"]] = m
                agora_of_instrument[m].add(r0["AGORA ID"])
                part_ids.add(r0["AGORA ID"])
            method = row["method"]
            note = "adjudicated (agora_overlap_verified.csv)"
        else:
            hits = set().union(*(key_index.get(k, set()) for r in recs for k in r["_keys"]))
            if len(hits) == 1:
                target = hits.pop()
                method, note = "key", "exact canonical key"
                review["AGORA groups merged into our records by exact key (not in the verified crosswalk)"].append(
                    f"{target} <- agora:{','.join(r['AGORA ID'] for r in recs)} ({recs[0]['Casual name'][:70]})")
            elif len(hits) > 1:
                review["AGORA groups whose key matches several of our records (not merged)"].append(
                    f"{sorted(hits)} <- agora:{recs[0]['AGORA ID']}")

        rest = [r for r in recs if r["AGORA ID"] not in part_ids]
        if target is None:
            target = new_agora_instrument(rest if part_ids else recs, instruments, old_target, old_added,
                                          agora_version, multi, review)
            method, note = "key", ""
            if part_ids:
                link(f"instrument:{next(iter(instrument_of_record[i] for i in part_ids))}", "part_of", target, "key",
                     "shares the parent's canonical link in AGORA")
        synthesized = target not in ours and multi
        for r in rest:
            sub_act = synthesized and norm_title(base_title(r["Official name"], True)) != norm_title(instruments[target]["title"])
            rel = "part_of" if is_section(r) or sub_act else "same"
            extra = f"; AGORA status {r['Most recent activity']}" if r["Most recent activity"] != recs[0]["Most recent activity"] else ""
            link(f"agora:{r['AGORA ID']}", rel, target, method, (note + "; " if note else "") + r["Casual name"][:100] + extra)
            instrument_of_record[r["AGORA ID"]] = target
            agora_of_instrument[target].add(r["AGORA ID"])

    for r, row in late:
        target = row["target"]
        target = instrument_of_record[target.removeprefix("agora:")] if target.startswith("agora:") else target
        link(f"agora:{r['AGORA ID']}", row["relation"], target, "manual", f"adjudicated (agora_manual.csv); {row['note'][:80]}")
        instrument_of_record[r["AGORA ID"]] = target
        agora_of_instrument[target].add(r["AGORA ID"])

    for m, relation, aid, method in deferred:
        rel = {"ours_codifies": "codifies"}.get(relation, relation)
        link(f"instrument:{m}", rel, instrument_of_record[aid], method, "adjudicated (agora_overlap_verified.csv)")
    for row in read_csv(REG / "links_manual.csv"):
        links.append(row)

    # text availability from the access log
    issues = defaultdict(set)
    latest = {(i["instrument_id"], i["url"], i["error_type"]): i for i in read_jsonl(REG / "access_issues.jsonl")}
    for i in latest.values():  # resolutions are appended: the latest row per (id, url, error type) wins
        issues[i["instrument_id"]].add(i["status"])
    for iid, sts in issues.items():
        if iid in instruments:
            instruments[iid]["text_access"] = "blocked" if "open" in sts else "fallback"

    apply_overrides(instruments, review)
    families = build_families(instruments)
    return dict(instruments=instruments, links=links, families=families, excluded=excluded, review=review,
                agora_version=agora_version, n_agora=len(docs), ours=ours, agora_of=agora_of_instrument)


def new_agora_instrument(recs, instruments, old_target, old_added, agora_version, multi, review) -> str:
    head = sorted(recs, key=lambda r: (r["Most recent activity"] == "Enacted", r["Most recent activity date"]))[-1]
    enacted = any(r["Most recent activity"] == "Enacted" for r in recs)
    title = group_title(recs, "Official name") if (multi or is_section(head)) else head["Official name"]
    short = group_title(recs, "Casual name") if (multi or is_section(head)) else head["Casual name"]
    iso, level, jurisdiction = head["_place"]
    itype = agora_type(f"{title} | {short}", head["Authority"], enacted, iso)
    if itype == "other" and any(k.startswith("US:FR:") for r in recs for k in r["_keys"]):
        itype = "administrative_rule"  # Federal Register documents
    activity = "Enacted" if enacted else head["Most recent activity"]
    status, stage, stage_basis = agora_status(activity, itype, f"{title} | {short}")
    proposed = min((r["Proposed date"] for r in recs if r["Proposed date"]), default=None)
    last = max((r["Most recent activity date"] for r in recs if r["Most recent activity date"]), default=None)
    adopted = min((r["Most recent activity date"] for r in recs
                   if r["Most recent activity"] == "Enacted" and r["Most recent activity date"]), default=None)
    dates = {"proposed": proposed, "adopted": adopted} if enacted else {"proposed": proposed, "last_action": last}
    if status == "repealed":
        dates = {"proposed": proposed, "last_action": last}
    keys = set().union(*(r["_keys"] for r in recs)) | keys_from_title(own_title(f"{title} | {short}"), head["Authority"])
    year = next((m[1] for k in sorted(keys) if (m := re.search(r":(20\d\d):", k))), None) or (proposed or last or "")[:4] or None

    reuse = Counter(old_target[f"agora:{r['AGORA ID']}"] for r in recs if f"agora:{r['AGORA ID']}" in old_target)
    iid = next((i for i, _ in reuse.most_common() if i not in instruments), None) or make_id(iso, keys, short, year)
    if iid in instruments:
        review["Possible duplicates: generated ID already taken (adjudicate in agora_manual.csv)"].append(
            f"{iid} <- agora:{head['AGORA ID']} ({short[:60]})")
        iid = f"{iid}-a{head['AGORA ID']}"
    if iso == "INT" and head["Authority"] == "Other authorities":
        review["AGORA records with unknown jurisdiction (set to INT)"].append(f"{iid}: {short[:80]}")
    instruments[iid] = clean({
        "instrument_id": iid, "title": title, "short_title": short if short != title else None,
        "jurisdiction": jurisdiction, "iso": iso, "level": level, "priority_region": priority_region(iso),
        "instrument_type": itype, "status": status, "legislative_stage": stage, "dates": dates,
        "domains": title_domains(f"{title} | {short}"), "keys": sorted(keys), "language": LANG.get(iso.split("-")[0]),
        "urls": {"official": head["Link to document"], "fulltext": head["Official plaintext source"]},
        "text_connector": connector(head["Link to document"]) or "agora",
        "provenance": {
            "added_by": "connector:agora", "added_on": old_added.get(iid) or TODAY,
            "sources": [f"agora:{r['AGORA ID']}" for r in recs], "verified": False,
            "notes": f"Identified from AGORA {agora_version} (CC BY-NC 4.0); type, stage and domains inferred by rules.",
            "stage_basis": stage_basis, "type_basis": "agora_title_rules", "domains_basis": "title_keywords"},
    })
    return iid


def apply_overrides(instruments: dict, review: dict) -> None:
    for o in read_jsonl(REG / "overrides.jsonl"):
        rec = instruments.get(o["instrument_id"])
        if rec is None:
            review["Overrides for unknown instruments"].append(o["instrument_id"])
            continue
        for path, value in o.get("set", {}).items():
            set_path(rec, path, value)
        for path in o.get("unset", []):
            unset_path(rec, path)
        prov = rec.setdefault("provenance", {})
        for field, basis in (("legislative_stage", "stage_basis"), ("instrument_type", "type_basis"), ("domains", "domains_basis")):
            if field in o.get("set", {}):
                prov[basis] = "override"
        prov["notes"] = " ".join(x for x in (prov.get("notes"), f"Override: {o['reason']}") if x)


def build_families(instruments: dict) -> list[dict]:
    out = []
    for f in FAMILIES:
        members = []
        for iid, rec in instruments.items():
            iso = rec["iso"]
            if f["scope"] == "us_state" and not (iso.startswith("US-") and rec["level"] == "subnational"):
                continue
            if f["scope"] == "europe_national" and not (iso.split("-")[0] in EU_EEA | {"GB"} and rec["level"] == "national"):
                continue
            if "types" in f and rec["instrument_type"] not in f["types"]:
                continue
            if "domain" in f and f["domain"] not in rec.get("domains", []):
                continue
            text = f"{rec['title']} | {rec.get('short_title', '')}"
            if iid in f.get("include", []):
                members.append(iid)
                continue
            if not (re.search(f["pattern"], text, re.I) or ("also_pattern" in f and re.search(f["also_pattern"], text, re.I))):
                continue
            if "also" in f and not re.search(f["also"], text, re.I):
                continue
            if "exclude" in f and re.search(f["exclude"], text, re.I):
                continue
            members.append(iid)
        for iid in members:
            instruments[iid]["family_ids"] = sorted(set(instruments[iid].get("family_ids", [])) | {f["family_id"]})
        rule = f"scope={f['scope']}; title matches /{f['pattern']}/" + (
            f" or /{f['also_pattern']}/" if "also_pattern" in f else "") + (f" and /{f['also']}/" if "also" in f else "") + (
            f"; not /{f['exclude']}/" if "exclude" in f else "") + (f"; domain {f['domain']}" if "domain" in f else "") + (
            f"; types {sorted(f['types'])}" if "types" in f else "") + (
            f"; plus manual members {f['include']}" if "include" in f else "")
        out.append({"family_id": f["family_id"], "title": f["title"], "method": "rule", "rule": rule,
                    "verified": False, "members": sorted(members)})
    return out


# ---------------------------------------------------------------- validate and write

def validate(instruments: dict, links: list[dict]) -> list[str]:
    errors = []
    iv = Draft202012Validator(json.loads((ROOT / "schemas" / "instrument.schema.json").read_text()))
    for iid, rec in instruments.items():
        errors += [f"{iid}: {e.message}" for e in iv.iter_errors(rec)]
    lv = Draft202012Validator(json.loads((ROOT / "schemas" / "link.schema.json").read_text()))
    for l in links:
        row = {k: v for k, v in l.items() if v != ""} | {"confidence": float(l.get("confidence") or 1)}
        errors += [f"link {l['from']} -> {l['to']}: {e.message}" for e in lv.iter_errors(row)]
        target = l["to"].removeprefix("instrument:")
        if target not in instruments:
            errors.append(f"link {l['from']} -> unknown instrument {target}")
    return errors


def coverage(b: dict) -> str:
    inst = b["instruments"].values()
    df = pd.DataFrame([{"id": i["instrument_id"], "region": i["priority_region"], "stage": i["legislative_stage"],
                        "domains": i.get("domains", []), "type": i["instrument_type"], "iso": i["iso"],
                        "source": ("both" if i["instrument_id"] in b["agora_of"] and i["instrument_id"] in b["ours"]
                                   else "ours" if i["instrument_id"] in b["ours"] else "AGORA"),
                        "stage_basis": i.get("provenance", {}).get("stage_basis", "")} for i in inst])
    n_rec_linked = sum(len(v) for v in b["agora_of"].values())
    rel = Counter(l["relation"] for l in b["links"] if l["from"].startswith("agora:"))

    def table(rows, cols, index_name):
        t = pd.crosstab(rows, cols, margins=True, margins_name="total")
        head = f"| {index_name} | " + " | ".join(map(str, t.columns)) + " |"
        sep = "|---" * (len(t.columns) + 1) + "|"
        body = [f"| {i} | " + " | ".join(str(v) for v in r) + " |" for i, r in t.iterrows()]
        return "\n".join([head, sep, *body])

    def ordered(s, order):
        return pd.Categorical(s, categories=[o for o in order if o in set(s)], ordered=True)

    long = df.explode("domains")
    out = [
        "# Registry coverage",
        "",
        f"Generated by `tools/build_registry.py` on {TODAY} from `manifest_v0` and AGORA {b['agora_version']}. "
        "Do not edit; rebuild instead.",
        "",
        f"- **{len(df)} instruments**: {sum(df.source == 'ours')} only in our records, {sum(df.source == 'AGORA')} only in AGORA, "
        f"{sum(df.source == 'both')} in both.",
        f"- **AGORA**: {b['n_agora']} records → {n_rec_linked} linked ({rel.get('same', 0)} `same`, {rel.get('part_of', 0)} `part_of`), "
        f"{len(b['excluded'])} excluded (`registry/crosswalk/agora_excluded.csv`).",
        f"- **Links**: {len(b['links'])} in `registry/links.csv`. **Families**: {len(b['families'])} in `registry/families.jsonl` (rule-based, unverified).",
        "",
        "## Region × legislative stage",
        "",
        table(ordered(df.region, REGIONS), ordered(df.stage, STAGES), "region"),
        "",
        "How stages were set: " + ", ".join(f"{k} {v}" for k, v in df.stage_basis.value_counts().items()) + ". "
        "`agora_status` means AGORA's Proposed was read as `introduced`; some of those bills went further, which LegiScan "
        "will settle.",
        "",
        "## Region × source",
        "",
        table(ordered(df.region, REGIONS), df.source, "region"),
        "",
        "## Domain × region",
        "",
        "An instrument can have several domains, so columns sum to more than the instrument count. Domains of "
        "AGORA-only instruments come from title keywords (`ai_general` when nothing matches).",
        "",
        table(ordered(long.domains, VOCAB["domains"]), ordered(long.region, REGIONS), "domain"),
        "",
        "## Instrument type × stage",
        "",
        table(df.type, ordered(df.stage, STAGES), "type"),
        "",
        "## P1 gaps",
        "",
    ]
    for label, isos in (("LATAM", sorted(P1_LATAM)), ("Europe", sorted(P1_EUROPE))):
        counts = df.iso.str.split("-").str[0].value_counts()
        out.append(f"- {label}: " + ", ".join(f"{c} {counts.get(c, 0)}" for c in isos))
    cn = df[df.region == "P1-China"]
    out.append(f"- China: {len(cn)} instruments ({sum(cn.iso != 'CN')} provincial or municipal).")
    out.append(f"- IP and training data: {sum(long.domains == 'ip_training_data')} instruments.")
    us_states = df[df.iso.str.startswith("US-")]
    out.append(f"- US states: {us_states.iso.nunique()} of 51 with at least one instrument; "
               f"{sum(us_states.stage.isin(['introduced', 'advanced', 'dead']))} state bills (pending or dead).")
    out += ["", "## Families", "", "| family | members | adopted | pending | dead |", "|---|---|---|---|---|"]
    for f in b["families"]:
        st = Counter(b["instruments"][m]["legislative_stage"] for m in f["members"])
        out.append(f"| {f['family_id']} | {len(f['members'])} | {st['adopted']} | "
                   f"{st['final_passage'] + st['advanced'] + st['introduced']} | {st['dead']} |")
    out += ["", "## Review queue", ""]
    for head, items in b["review"].items():
        out.append(f"**{head}** ({len(items)})")
        out += [f"- {x}" for x in items[:40]] + ([f"- … {len(items) - 40} more"] if len(items) > 40 else []) + [""]
    if not b["review"]:
        out.append("Nothing to review.")
    return "\n".join(out) + "\n"


def main() -> None:
    b = build()
    errors = validate(b["instruments"], b["links"])
    if errors:
        print(f"{len(errors)} validation errors; nothing written:", *errors[:30], sep="\n  ")
        sys.exit(1)
    with (REG / "instruments.jsonl").open("w") as fh:
        for iid in sorted(b["instruments"]):
            fh.write(json.dumps(b["instruments"][iid], ensure_ascii=False) + "\n")
    with (REG / "links.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["from", "relation", "to", "method", "confidence", "note"])
        w.writeheader()
        w.writerows(sorted(b["links"], key=lambda l: (l["to"], l["from"])))
    with (REG / "families.jsonl").open("w") as fh:
        for f in b["families"]:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    pd.DataFrame(b["excluded"]).to_csv(REG / "crosswalk" / "agora_excluded.csv", index=False)
    (REG / "coverage.md").write_text(coverage(b))
    stages = Counter(i["legislative_stage"] for i in b["instruments"].values())
    print(f"{len(b['instruments'])} instruments {dict(stages)}; {len(b['links'])} links; "
          f"{len(b['families'])} families; {len(b['excluded'])} AGORA records excluded")


if __name__ == "__main__":
    main()
