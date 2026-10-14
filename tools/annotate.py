"""Annotate instruments with Claude following docs/CODEBOOK.md (Tier A sections).

    python tools/annotate.py <instrument_id> ... | --split tune|test | --ids-file F  --out build/pilot/<run>
        [--batch [--wave 150]] [--tier A|B]
        [--model claude-haiku-5-5] [--effort low|medium|high] [--group-effort obligations=medium]
        [--mode focused|packed] [--workers 4]

Defaults come from the Oct 2026 pilot (docs/pilot_2026-10.md): Haiku 5.5, high effort, focused mode.

- **Calls.** The full schema is too large for one structured-output call (grammar limit), so `focused` makes one
  call per group of sections (6) and `packed` 4 (see MODES). Each call carries section-specific instructions
  (GUIDANCE) that spell out the codebook conventions the pilot found the model got wrong.
- **Prompt layout.** System = instructions + codebook (cached; shared across laws for the same group), then the
  document. The structured-output schema sits at the start of the prompt, so calls with different schemas do not
  share a cache.
- **Long texts** (over CHUNK_TOKENS) are split at article or chapter headings into parts. Fact sections are
  extracted part by part and merged; the overview sees the first part plus an outline of all headings. This keeps
  prompts under 100K tokens (where Haiku's price is 5x higher) and avoids output truncation.
- **Relative dates.** The model gives offset_value / offset_unit / anchor_event; `resolve_dates` computes the date
  when the anchor event's date is known (business days are left empty).
- **Checks.** The validator runs (`tools/validate.py`). Bookkeeping errors are fixed in code; quotes that fail the
  verbatim check get one repair call (correct the quote or drop the item).

Output: <out>/<id>.json (never written to annotations/) and <out>/<id>.log.json (calls, tokens, cost, repairs,
remaining errors). Reads ANTHROPIC_API_KEY / ANTHROPIC_WORKSPACE_ID from .env.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from itertools import accumulate
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from api_check import load_key  # noqa: E402
from budget import BudgetExceeded, guard, record, release  # noqa: E402
from quote_check import TEXTS, Doc, iter_quotes, quote_in  # noqa: E402
from validate import TIER_SECTIONS, validate  # noqa: E402

SCHEMA = json.loads((ROOT / "schemas" / "annotation.schema.json").read_text())
CODEBOOK = (ROOT / "docs" / "CODEBOOK.md").read_text()
REGISTRY = {json.loads(l)["instrument_id"]: json.loads(l) for l in (ROOT / "registry" / "instruments.jsonl").open()}
META = {"schema_version", "instrument_id", "tier", "annotator", "validation", "text_version"}
SECTIONS = ["events", "thresholds", "obligations", "enforcement", "definitions", "relations", "entities"]
# Section groups per call. The full schema is too large for one structured-output call (grammar size limit), so
# `packed` uses the fewest groups that compile (4 calls) and `focused` gives each area its own call (6 calls).
MODES = {
    "packed": {
        "overview": ["classification", "summary"],
        "rules": ["thresholds", "definitions", "obligations"],
        "timeline_enforcement": ["events", "enforcement", "notes"],
        "links": ["relations", "entities"],
    },
    "focused": {
        "overview": ["classification", "summary"],
        "links": ["relations", "entities", "notes"],
        "timeline": ["events"],
        "thresholds": ["thresholds", "definitions"],
        "obligations": ["obligations"],
        "enforcement": ["enforcement"],
    },
    "light": {  # Tier B (PLAN.md section 5): topics, timeline, applicability (thresholds), relations
        "overview": ["classification", "summary"],
        "links": ["relations", "notes"],
        "timeline": ["events"],
        "thresholds": ["thresholds"],
    },
}
# USD per million tokens: (input, output) for prompts up to 100K tokens, and above. Cache write 1.25x, read 0.1x.
PRICES = {"claude-haiku-5-5": ((0.10, 0.50), (0.50, 2.50)), "claude-sonnet-5-5": ((2.0, 10.0), (2.0, 10.0))}

INSTRUCTIONS = """You annotate legal and regulatory texts for ai-law-atlas, a public database of AI-relevant law.
Follow the codebook below exactly. Work only from the document provided: never use outside knowledge, never
invent dates, numbers, names or penalties. Every fact item needs `evidence` with the provision ID and a quote
copied character for character from the document (original language; an English gloss in [brackets] may follow).
Keep quotes short (one sentence or clause, at most about 40 words). Summaries, notes and classifications are in
English. Annotations are neutral: describe what the text says, not its effects or research value.
Sections you are asked for but find nothing for get an entry in `section_status` (not_applicable or not_found)."""

CHUNK_TOKENS = 80_000  # document part size (conservative estimate); with the codebook, under 100K real tokens
GUIDANCE = {
    "summary": "summary: `short` at most 50 words, `long` at most 200; what the instrument does, to whom, from when.",
    "classification": ("classification: use only labels supported by operative provisions; `primary_domain` is the "
                       "domain most operative provisions serve; decision_margins only when tied to a provision. "
                       "Mechanism enforcement_order only for an individual measure against a named party, never for "
                       "a statute's general sanctions. ai_sectoral only for rules on AI use in a sector; semiconductor "
                       "or compute industrial policy is chips_export_controls or compute_data_centers. The overview "
                       "must describe the whole instrument (use the outline), not only the first part."),
    "events": ("events: work through the whole text and list every dated or datable event: adoption, signature, "
               "publication, entry into force, each application date, each phase-in step (one row per cohort, size "
               "class or sector), deadlines with legal consequences, sunset, expiry, repeal. For a relative date "
               "('24 months after publication') give date_type relative, offset_value, offset_unit and anchor_event "
               "(the event the period counts from) and leave `date` empty unless the text states it: the date is "
               "computed in code. Use date_type recurring for repeating dates. Dates printed in the document "
               "(gazette dateline, signature date) are absolute, never external. anchor_event must be an event of "
               "THIS instrument; if the period runs from another act's event (implementing rules, a decree, "
               "commencement by notification, section-by-section commencement) use date_type conditional with "
               "depends_on and leave anchor_event empty. Conditions and measurement windows ('12 consecutive months "
               "above X') are thresholds, not events. A period BEFORE the anchor ('3 months before entry into "
               "force') has a negative offset_value. Only events of this instrument: dates of other acts it mentions "
               "(an EU directive's transposition deadline in the preamble, an amending act's publication noted in "
               "a consolidated text) are not its events. One row per distinct deadline, even if several duties "
               "share it; penalties (suspension, termination) are enforcement, not events."),
    "thresholds": ("thresholds: every number at which legal treatment changes (scope, eligibility, exemption, "
                   "obligation tier, prohibition, benefit, penalty tier, price tier). Record value AND variable, one "
                   "row per value; ranges use operator range with value_max. Price surcharges or tariffs above a limit "
                   "are pricing_tier. NOT thresholds: government policy targets, fine maxima (enforcement), procedural "
                   "time limits (events), technical performance specifications unless crossing them decides scope, "
                   "eligibility or a legal consequence. A threshold the government will set later (by "
                   "notification or regulation) is still a row: value empty, the delegation in value_text. Penalty "
                   "or damages amounts are enforcement, not thresholds. Amending acts: only new or changed text."),
    "definitions": ("definitions: every defined term that determines scope or a legal consequence (who or what is "
                    "covered); skip purely procedural terms."),
    "obligations": ("obligations: work through the operative provisions in order, article by article, so none is "
                    "skipped. One row per article, addressee role and obligation type: merge all duties of the same "
                    "type that one article places on the same role into one row (quote the main duty, list the "
                    "others in `summary`); split only when start dates differ. An identical duty on several roles is "
                    "one row with also_addressees. Include duties of public bodies: obligation type government_duty "
                    "(delegated_power for powers to make rules), role government_agency or regulator, never a "
                    "private role. Not obligations: lists of infringements (enforcement), statements that no duty "
                    "exists, burden-of-proof and liability rules, penalties and rights to sue (enforcement only). "
                    "Amending acts: only new or changed text (insertions, capitals, underlining), never restated "
                    "unchanged text. A duty stated inside a definition still counts. Addressee: the most specific "
                    "role the text names (provider, deployer, platform_operator, financial_institution, "
                    "cloud_service_provider, ...); enterprise only for duties on firms in general or above a size "
                    "test. Scope exemptions are not prohibitions. Staged start dates: one row per start date. "
                    "Expect roughly one to three rows "
                    "per operative article, not one per sentence."),
    "enforcement": ("enforcement: one row per sanction regime (fines with their maxima and formulas, criminal "
                    "penalties, licence revocation, benefit clawback, suspension, blocking). Amounts only if this text "
                    "states them; otherwise penalty_reference."),
    "relations": ("relations: one row per target instrument (never list several instruments in one row): enabling "
                  "act (legal_basis), acts amended, repealed, implemented or transposed, and substantive cross-references."),
    "entities": ("entities: named firms, facilities or public bodies with a role; public authorities the text "
                 "designates or empowers are `designated`, with their role in `function`."),
    "notes": "notes: internal inconsistencies of the text and anything a reader must know; keep it short.",
}
def prompt_version() -> str:
    """Fingerprint of everything the model is told (instructions, per-section guidance, codebook, call groups).
    The prompt itself is archived in docs/prompts/<version>.json on first use, so any annotation can be traced to
    the exact prompt that produced it."""
    import hashlib
    blob = json.dumps({"instructions": INSTRUCTIONS, "guidance": GUIDANCE, "codebook": CODEBOOK, "modes": MODES},
                      sort_keys=True, ensure_ascii=False)
    v = "1.3-" + hashlib.sha256(blob.encode()).hexdigest()[:8]
    arch = ROOT / "docs" / "prompts" / f"{v}.json"
    if not arch.exists():
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(json.dumps({"prompt_version": v, "created": date.today().isoformat(),
                                    "instructions": INSTRUCTIONS, "guidance": GUIDANCE, "modes": MODES,
                                    "codebook_sha256": hashlib.sha256(CODEBOOK.encode()).hexdigest()},
                                   ensure_ascii=False, indent=1))
    return v


HEADING = re.compile(
    r"^\s*(?:Art(?:ículo|icle|igo|icolo)?\.?\s*\d|ART[ÍI]CULO|ARTICLE|Section\s+\d|SEC(?:TION)?\.?\s*\d|§\s*\d"
    r"|第[一二三四五六七八九十百零〇\d]+[条章]|Chapter|CHAPTER|CAP[ÍI]TULO|Cap[íi]tulo|TITLE|Title\s+[IVX]|T[ÍI]TULO"
    r"|T[íi]tulo|Part\s+\d|PART\b|Kapitel|Abschnitt|Anlage|Annex|ANNEX|Anexo|ANEXO|Subanexo)", re.M)


CODE_ONLY = {"date_computed_from"}  # fields filled in code, never by the model


def model_schema(fields: list[str]) -> dict:
    """The part of the annotation schema the model fills: the requested sections plus section_status."""
    def strip(node):
        if isinstance(node, dict):
            out = {k: strip(v) for k, v in node.items() if k not in ("minItems", "$schema", "title")}
            if isinstance(out.get("properties"), dict):
                out["properties"] = {k: v for k, v in out["properties"].items() if k not in CODE_ONLY}
            return out
        if isinstance(node, list):
            return [strip(x) for x in node]
        return node
    props = {k: v for k, v in SCHEMA["properties"].items() if k in fields}
    own = [f for f in fields if f in SCHEMA["properties"]["section_status"]["properties"]]
    if own:  # statuses only for this call's own sections
        st = SCHEMA["properties"]["section_status"]
        props["section_status"] = {**st, "properties": {k: st["properties"][k] for k in own}}
    required = [k for k in SCHEMA["required"] if k in props and k not in META]
    required += [k for k in fields if k in SECTIONS and k not in required]
    return close(strip({"type": "object", "additionalProperties": False, "properties": props, "required": required}))


def types(node: dict) -> list:
    t = node.get("type", [])
    return t if isinstance(t, list) else [t]


def close(node, optional: bool = False):
    """Structured outputs allow at most 24 optional fields and 16 union-typed ones, so every field is required and
    "absent" is an empty value: "" for strings and enums, [] for arrays, an object of empty values for objects.
    Numbers and the private-right-of-action flag keep a null (4 unions). `prune` maps the empties back."""
    if isinstance(node, list):
        return [close(x) for x in node]
    if not isinstance(node, dict):
        return node
    if "enum" in node:
        vals = [v for v in node["enum"] if v is not None]
        return {**node, "enum": vals + ([""] if (optional or None in node["enum"]) and "" not in vals else [])}
    t = types(node)
    if "object" in t and "properties" in node:
        req = set(node.get("required", []))
        props = {k: close(v, optional=k not in req) for k, v in node["properties"].items()}
        return {**node, "type": "object", "properties": props, "required": list(props)}
    if "array" in t:
        return {**node, "type": "array", "items": close(node["items"])}
    if "string" in t:
        return {**node, "type": "string"}
    if "number" in t:
        return {**node, "type": ["number", "null"]}
    if "boolean" in t:
        return {**node, "type": ["boolean", "null"]} if "null" in t else {**node, "type": "boolean"}
    return node


def prune(value, schema: dict):
    """Inverse of `close`: drop empty values of optional fields; "" becomes null where the real schema allows null."""
    if isinstance(value, list) and "items" in schema:
        return [prune(v, schema["items"]) for v in value]
    if not (isinstance(value, dict) and "properties" in schema):
        return value
    req, out = set(schema.get("required", [])), {}
    for k, v in value.items():
        sub = schema["properties"].get(k, {})
        nullable = "null" in types(sub) or None in sub.get("enum", [])
        v = prune(v, sub)
        empty = v in ("", None, []) or (isinstance(v, dict) and not v) or (
            sub.get("type") == "boolean" and v is False and k not in req)
        if isinstance(v, dict) and "required" in sub and any(v.get(r) in ("", None) for r in sub["required"]):
            empty = True  # an optional object whose required fields are empty was not meant to be there
        if empty and k not in req:
            continue
        out[k] = None if (v == "" and nullable) else v
    return out


def cost(model: str, usage) -> float:
    prompt = usage.input_tokens + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
    (pin, pout) = PRICES[model][1 if prompt > 100_000 else 0]
    return (usage.input_tokens * pin + (usage.cache_creation_input_tokens or 0) * pin * 1.25
            + (usage.cache_read_input_tokens or 0) * pin * 0.1 + usage.output_tokens * pout) / 1e6


class Truncated(RuntimeError):
    """The model's answer hit max_tokens."""


def est_tokens(text: str) -> int:
    """Conservative: Haiku 5.5 counts about 2.5 characters per token on legal text with numbers and tables (the
    codebook, 22K characters, is about 10K tokens; Federal Register text is denser still). Overestimating keeps
    prompts under the 100K-token price step and keeps the budget guard's worst case on the safe side."""
    cjk = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]", text))
    return cjk + int((len(text) - cjk) / 2.5)


def split_text(text: str, limit: int = CHUNK_TOKENS) -> list[str]:
    """Split at headings so that each part is at most about `limit` tokens (hard cut at a line if no heading fits)."""
    if est_tokens(text) <= limit:
        return [text]
    cjk = list(accumulate(1 if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" else 0 for ch in text))
    tok = lambda i, j: (cjk[j - 1] - (cjk[i - 1] if i else 0)) + int(((j - i) - (cjk[j - 1] - (cjk[i - 1] if i else 0))) / 2.5)
    cuts = [m.start() for m in HEADING.finditer(text)]
    parts, start = [], 0
    while tok(start, len(text)) > limit:
        fit = [c for c in cuts if c > start and tok(start, c) <= limit]
        cut = fit[-1] if fit and tok(start, fit[-1]) > limit // 3 else None
        if cut is None:  # no usable heading: cut at the last newline within the limit
            hi = start + 1
            while hi < len(text) and tok(start, hi) < limit:
                hi = min(len(text), hi + 2000)
            cut = text.rfind("\n", start + 1, hi) if text.rfind("\n", start + 1, hi) > start else hi
        parts.append(text[start:cut])
        start = cut
    parts.append(text[start:])
    return parts


def outline(text: str, max_tokens: int = 6000) -> str:
    lines, total = [], 0
    for m in HEADING.finditer(text):
        line = text[m.start():text.find("\n", m.start())][:100].strip()
        total += est_tokens(line) + 1
        if total > max_tokens:
            break
        lines.append(line)
    return "\n".join(lines)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    day = min(d.day, [31, 29 if (d.year + y) % 4 == 0 and ((d.year + y) % 100 or (d.year + y) % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m])
    return date(d.year + y, m + 1, day)


BEFORE = re.compile(r"\bbefore\b|\bprior to\b|\bantes\b|\bavant\b|\bprima\b|\bvor\b|\bantes de\b|前", re.I)


def resolve_dates(a: dict, registry: dict) -> int:
    """Fill `date` of relative events from offset_value/offset_unit/anchor_event when the anchor date is known, and
    record how in `date_computed_from`. Anchors: the text's own absolute dates first; the registry's adopted /
    published / entry-into-force dates only when the text shows no staggered or conditional version of that event
    (spot-check, Oct 2026: periods counted from implementing rules or from section-by-section commencement were
    filled from the registry's single entry-into-force date). An event type with two different stated dates is
    ambiguous and is not used as an anchor."""
    events = a.get("events", [])
    stated, unsure = defaultdict(set), set()
    for e in events:
        if e.get("date_computed_from"):
            continue
        if e.get("date") and e.get("date_type") == "absolute" and len(e["date"]) == 10:
            stated[e["event"]].add(e["date"])
        elif e.get("date_type") in ("relative", "conditional") or not e.get("date"):
            unsure.add(e["event"])
    anchors = {ev: (min(ds), "text") for ev, ds in stated.items() if len(ds) == 1}
    if registry.get("legislative_stage") not in (None, "adopted"):  # a bill: its adoption, publication and entry into
        for ev in ("adopted", "signed", "published", "entry_into_force"):  # force have not happened (a gazette
            anchors.pop(ev, None)                                            # printing a bill is not "published")
    for k, src in (() if registry.get("legislative_stage") not in (None, "adopted") else
                   (("adopted", "adopted"), ("signed", "adopted"), ("published", "published"),
                    ("entry_into_force", "entry_into_force"))):  # US acts: "date of enactment" = signature = adopted
        v = registry.get("dates", {}).get(src)
        if v and len(v) == 10 and k not in anchors and k not in stated and k not in unsure:
            anchors[k] = (v, "registry")
    count = defaultdict(int)
    for e in events:
        count[e["event"]] += 1
    filled = 0
    for _ in range(3):  # chains: a computed date can anchor another (repeal counted from a computed entry into force)
        new = 0
        for e in events:
            unit, n = e.get("offset_unit"), e.get("offset_value")
            anchor = anchors.get(e.get("anchor_event") or "")
            if e.get("date") or not (unit and n is not None and anchor) or unit == "business_days":
                continue
            if n > 0 and BEFORE.search(e.get("date_text") or ""):  # "X before" with a positive offset: unsure
                continue
            d0, n = date.fromisoformat(anchor[0]), int(n)
            d = {"days": lambda: d0 + timedelta(days=n), "weeks": lambda: d0 + timedelta(weeks=n),
                 "months": lambda: add_months(d0, n), "years": lambda: add_months(d0, 12 * n)}[unit]()
            e["date"] = d.isoformat()
            e["date_computed_from"] = f"{e['anchor_event']} {anchor[0]} ({anchor[1]}) + {n} {unit}"
            if count[e["event"]] == 1 and e["event"] not in anchors:  # the only event of its type: usable anchor
                anchors[e["event"]] = (e["date"], "computed")
            new += 1
        filled += new
        if not new:
            break
    return filled


def recompute_dates(a: dict, registry: dict) -> int:
    """For annotations made before `date_computed_from` existed: clear the dates the old rule computed (it used any
    stated or registry anchor), then resolve again with the current rules. A date is cleared only if the old rule
    reproduces it exactly, so dates the model read from the text stay."""
    for e in a.get("events", []):  # dates computed by the current code are recomputed from scratch
        if e.pop("date_computed_from", None):
            e["date"] = None
    old = {k: registry["dates"][k] for k in ("adopted", "published", "entry_into_force")
           if len(registry.get("dates", {}).get(k) or "") == 10}
    for e in a.get("events", []):
        if e.get("date") and e.get("date_type") in ("absolute", "external") and len(e["date"]) == 10:
            old[e["event"]] = e["date"]
    for e in a.get("events", []):
        unit, n, d0 = e.get("offset_unit"), e.get("offset_value"), old.get(e.get("anchor_event") or "")
        if not (e.get("date") and unit and n is not None and d0) or unit == "business_days" or e.get("date_computed_from"):
            continue
        d0, n = date.fromisoformat(d0), int(n)
        guess = {"days": lambda: d0 + timedelta(days=n), "weeks": lambda: d0 + timedelta(weeks=n),
                 "months": lambda: add_months(d0, n), "years": lambda: add_months(d0, 12 * n)}[unit]()
        if guess.isoformat() == e["date"]:
            e["date"] = None
    return resolve_dates(a, registry)


class Annotator:
    def __init__(self, model: str, effort: str | None, mode: str, out: Path, group_effort: dict | None = None,
                 tier: str = "A"):
        load_key()
        ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        self.client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None,
                                          max_retries=4)
        self.model, self.effort, self.mode, self.out = model, effort, mode, out
        self.group_effort = group_effort or {}  # per-group override of `effort`, e.g. {"obligations": "medium"}
        self.tier = tier

    def call(self, system: list, prompt: str, schema: dict, log: dict, max_tokens: int = 64000,
             effort: str | None = None) -> dict:
        config = {"format": {"type": "json_schema", "schema": schema}}
        if effort or self.effort:
            config["effort"] = effort or self.effort
        n_in = est_tokens("".join(b["text"] for b in system) + prompt + json.dumps(schema))
        (pin, pout) = PRICES[self.model][1 if n_in > 100_000 else 0]
        worst = (n_in * pin * 1.25 + max_tokens * pout) / 1e6  # all input written to cache, full output
        guard(worst)
        try:
            with self.client.messages.stream(model=self.model, max_tokens=max_tokens, system=system,
                                             output_config=config,
                                             messages=[{"role": "user", "content": prompt}]) as stream:
                msg = stream.get_final_message()
        except Exception:
            record(n_in * pin / 1e6, f"annotate {self.out.name}: failed call (input estimate)")
            raise
        finally:
            release(worst)
        u = msg.usage
        record(cost(self.model, u), f"annotate {self.out.name}")
        log["calls"].append({"stop": msg.stop_reason, "in": u.input_tokens, "out": u.output_tokens,
                             "cache_write": u.cache_creation_input_tokens or 0,
                             "cache_read": u.cache_read_input_tokens or 0, "usd": round(cost(self.model, u), 6)})
        if msg.stop_reason == "max_tokens":
            raise Truncated(f"output limit reached ({u.output_tokens} tokens)")
        if msg.stop_reason != "end_turn":
            raise RuntimeError(f"stop_reason {msg.stop_reason}")
        return json.loads(next(b.text for b in msg.content if b.type == "text"))

    def by_parts(self, iid: str, chunks: list[str], fields: list[str], log: dict, depth: int = 0,
                 effort: str | None = None) -> dict:
        """One call per part; a part whose answer is truncated is split again (at most twice)."""
        outs = []
        for k, c in enumerate(chunks):
            part = f"{k + 1} of {len(chunks)}" if len(chunks) > 1 else ""
            try:
                outs.append(self.call(self.system(iid, c, part), self.prompt(iid, fields, part), model_schema(fields), log,
                                      effort=effort))
            except Truncated:
                if depth >= 2:
                    raise
                log.setdefault("resplit", []).append(f"{fields[0]} part {part or 1}")
                outs.append(self.by_parts(iid, split_text(c, max(4000, est_tokens(c) // 2)), fields, log, depth + 1,
                                          effort))
        return combine(outs, fields)

    def system(self, iid: str, text: str, part: str = "", extra: str = "") -> list:
        tag = f' part="{part}"' if part else ""
        return [{"type": "text", "text": f"{INSTRUCTIONS}\n\n<codebook>\n{CODEBOOK}\n</codebook>",
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"<document instrument_id=\"{iid}\"{tag}>\n{text}\n</document>{extra}",
                 "cache_control": {"type": "ephemeral"}}]

    def prompt(self, iid: str, fields: list[str], part: str = "") -> str:
        r = REGISTRY.get(iid, {})
        meta = {k: r.get(k) for k in ("title", "jurisdiction", "instrument_type", "status", "legislative_stage",
                                      "language")}
        scope = (f"\nThe document is long and is given in parts; this is part {part}. Annotate only the provisions "
                 f"in this part (other parts are annotated separately)." if part else "")
        guide = "\n".join(f"- {GUIDANCE[f]}" for f in fields if f in GUIDANCE)
        return (f"Instrument: {iid}\nRegistry metadata (may be incomplete; the document is authoritative): "
                f"{json.dumps(meta, ensure_ascii=False)}{scope}\n\nAnnotate this instrument at Tier A. Fill these "
                f"fields: {', '.join(fields)}. Follow the codebook sections that govern them, and in particular:\n"
                f"{guide}")

    def annotate(self, iid: str) -> dict:
        log = {"instrument_id": iid, "model": self.model, "effort": self.effort, "group_effort": self.group_effort,
               "mode": self.mode, "calls": []}
        text = (TEXTS / f"{iid}.txt").read_text(encoding="utf-8")
        chunks = split_text(text)
        log["parts"] = len(chunks)
        groups = MODES[self.mode]
        parts = {}
        for name, fields in groups.items():
            eff = self.group_effort.get(name)
            if len(chunks) == 1:
                try:
                    parts[name] = self.call(self.system(iid, text), self.prompt(iid, fields), model_schema(fields), log,
                                            effort=eff)
                except Truncated:  # the answer outgrew the output limit: redo this group on smaller parts
                    log.setdefault("resplit", []).append(name)
                    parts[name] = self.by_parts(iid, split_text(text, max(4000, est_tokens(text) // 2)), fields, log,
                                                effort=eff)
            elif name == "overview":  # first part plus an outline of the whole text
                extra = f"\n<outline of the whole document>\n{outline(text)}\n</outline>"
                parts[name] = self.call(self.system(iid, chunks[0], f"1 of {len(chunks)}", extra),
                                        self.prompt(iid, fields), model_schema(fields), log, effort=eff)
            else:
                parts[name] = self.by_parts(iid, chunks, fields, log, effort=eff)
        a = self.assemble(iid, parts, log)
        log["quote_repairs"] = self.repair_quotes(iid, a, text, log)
        return self.write(iid, a, log)

    def assemble(self, iid: str, parts: dict, log: dict) -> dict:
        """Group outputs -> annotation: merge, prune the sentinels, compute dates, add metadata, fix bookkeeping."""
        a = prune(merge(parts, MODES[self.mode]), SCHEMA)
        r = REGISTRY.get(iid, {})
        log["dates_computed"] = resolve_dates(a, r)
        a = {"schema_version": "1.3", "instrument_id": iid, "tier": self.tier,
             "annotator": {"model": self.model, "method": f"{self.mode}, effort {self.effort or 'default'}"
                           + "".join(f", {g} {e}" for g, e in self.group_effort.items())
                           + (", batch" if log.get("batch") else ""),
                           "date": date.today().isoformat(), "prompt_version": prompt_version()},
             "validation": "machine",
             "text_version": text_version(iid, r),
             **a}
        log["code_fixes"] = fix_bookkeeping(a)
        return a

    def write(self, iid: str, a: dict, log: dict) -> dict:
        path = self.out / f"{iid}.json"
        path.write_text(json.dumps(a, ensure_ascii=False, indent=1))
        log["errors"] = validate(path)
        log["usd"] = round(sum(c["usd"] for c in log["calls"]), 5)
        (self.out / f"{iid}.log.json").write_text(json.dumps(log, indent=1))
        return log

    def repair_quotes(self, iid: str, a: dict, text: str, log: dict) -> dict:
        doc = Doc(text)
        bad = bad_quotes(a, doc)
        if not bad:
            return {"failed": 0}
        prompt, schema = repair_request(iid, bad)
        fixes = {}
        for k, c in enumerate(split_text(text)):  # long texts: ask part by part, keep the first verbatim answer
            part = f"{k + 1} of {len(split_text(text))}" if est_tokens(text) > CHUNK_TOKENS else ""
            for f in self.call(self.system(iid, c, part), prompt, schema, log, max_tokens=16000)["fixes"]:
                if f["quote"] and quote_in(f["quote"], doc) and f["path"] not in fixes:
                    fixes[f["path"]] = f["quote"]
        return apply_fixes(a, bad, fixes, doc)


REPAIR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["fixes"],
                 "properties": {"fixes": {"type": "array", "items": {
                     "type": "object", "additionalProperties": False, "required": ["path", "quote"],
                     "properties": {"path": {"type": "string"}, "quote": {"type": ["string", "null"]}}}}}}


def bad_quotes(a: dict, doc: Doc) -> list[tuple[str, str]]:
    return [(p, q) for p, q in iter_quotes(a) if not quote_in(q, doc)]


def repair_request(iid: str, bad: list[tuple[str, str]]) -> tuple[str, dict]:
    listing = "\n".join(f"- {p}: {q}" for p, q in bad)
    return (f"These quotes in your annotation of {iid} were not found verbatim in the document:\n{listing}\n\n"
            "For each path, return a corrected quote copied character for character from the document that "
            "supports the same item, or null if the document has no such text."), REPAIR_SCHEMA


def apply_fixes(a: dict, bad: list[tuple[str, str]], fixes: dict, doc: Doc) -> dict:
    """Set verified corrected quotes; drop the items whose quote could not be corrected."""
    dropped, gone = 0, set()
    for p, _ in sorted(bad, key=lambda x: item_key(x[0]), reverse=True):  # reverse: list indices stay valid
        q = fixes.get(p)
        if q and quote_in(q, doc):
            set_quote(a, p, q)
        elif item_key(p) not in gone:
            gone.add(item_key(p))
            dropped += drop_item(a, p)
    fix_bookkeeping(a)
    return {"failed": len(bad), "fixed": len(bad) - dropped, "dropped": dropped}


def text_version(iid: str, r: dict) -> dict:
    """From cache/texts/<id>.meta.json (written by the text connectors) when present, else from the registry."""
    tv = {"source": "ai-law-atlas cache/texts", "url": r.get("urls", {}).get("fulltext")
          or r.get("urls", {}).get("official", ""), "retrieved": "see cache", "version": "as cached",
          "language": r.get("language", ""), "extent": "full"}
    meta = TEXTS / f"{iid}.meta.json"
    if meta.exists():
        m = json.loads(meta.read_text())
        tv.update({k: str(m[k]) for k in ("source", "url", "retrieved", "version", "extent", "note") if m.get(k)})
    return tv


def combine(outs: list[dict], fields: list[str]) -> dict:
    """Merge the outputs of one group across document parts: concatenate lists, drop repeated quotes."""
    out, seen, status = {}, set(), {}
    for o in outs:
        for f in fields:
            v = o.get(f)
            if isinstance(v, list):
                for item in v:
                    q = json.dumps(item.get("evidence", item), ensure_ascii=False, sort_keys=True)
                    if (f, q) not in seen:
                        seen.add((f, q))
                        out.setdefault(f, []).append(item)
                out.setdefault(f, [])
            elif isinstance(v, str) and v:
                out[f] = (out.get(f, "") + " " + v).strip()
        status.update(o.get("section_status") or {})
    out["section_status"] = status
    return out


def merge(parts: dict, groups: dict) -> dict:
    out, status = {}, {}
    for name, fields in groups.items():
        part = parts[name]
        for f in fields:
            if f in part:
                out[f] = part[f]
        for s, v in (part.get("section_status") or {}).items():
            if s in fields:
                status[s] = v
    out["section_status"] = status
    return out


PUBLIC_ROLES = {"government_agency", "regulator"}
GENERIC_TYPES = {"reporting_to_authority", "disclosure_transparency", "cooperation_with_authority", "record_keeping",
                 "other"}


def fix_bookkeeping(a: dict) -> list[str]:
    """Deterministic repairs: section_status consistent with section contents; primary_domain in domains."""
    fixes, st = [], a.setdefault("section_status", {})
    for s in list(st):
        if a.get(s):
            del st[s]
            fixes.append(f"removed status for non-empty {s}")
    for s in SECTIONS:
        if s not in TIER_SECTIONS[a.get("tier", "A")]:  # not annotated at this tier: absent, not "not found"
            if not a.get(s):
                a.pop(s, None)
                st.pop(s, None)
            continue
        if not a.get(s):
            a[s] = []
            if s not in st:
                st[s] = {"status": "not_found", "note": "set by runner: model returned no entries"}
                fixes.append(f"added not_found for {s}")
    kept = []
    for i, e in enumerate(a.get("events", [])):  # an event without any evidence is unsupported: drop it
        if not e.get("evidence") and not e.get("external_evidence"):
            fixes.append(f"events[{i}]: dropped (no evidence)")
            continue
        if e.get("date") and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", str(e["date"])):
            fixes.append(f"events[{i}]: malformed date {e['date']!r} cleared")
            e["date"] = None
            if e.get("date_type") == "absolute":
                e["date_type"] = "relative" if e.get("offset_unit") else "conditional"
        kept.append(e)
    if "events" in a:
        a["events"] = kept
    for i, e in enumerate(a.get("events", [])):
        if e.get("date_type") == "external" and "external_evidence" not in e and e.get("evidence"):
            e["date_type"] = "absolute" if e.get("date") else "relative"  # the date is printed in the document
            fixes.append(f"events[{i}]: external -> {e['date_type']} (date comes from the document)")
        if e.get("date_type") in ("relative", "conditional") and not e.get("anchor") and not e.get("depends_on"):
            e["anchor"] = e.get("anchor_event") or "not stated in the text"
            fixes.append(f"events[{i}]: anchor set to {e['anchor']!r}")
    for i, o in enumerate(a.get("obligations", [])):  # codebook 3.5: duties of public bodies are government_duty,
        if o.get("addressee") in PUBLIC_ROLES and o.get("type") in GENERIC_TYPES:  # except AI-use duties
            fixes.append(f"obligations[{i}]: {o['type']} -> government_duty (public addressee)")
            o["type"] = "government_duty"
    for i, t in enumerate(a.get("thresholds", [])):  # a "range" with no bounds is membership of a category defined
        if t.get("operator") == "range" and t.get("value") is None and t.get("value_max") is None:  # elsewhere
            t["operator"] = "="
            fixes.append(f"thresholds[{i}]: range without bounds -> '=' (category in value_text)")
    c = a.get("classification", {})
    ts = c.get("territorial_scope")
    if isinstance(ts, dict) and not ts.get("types"):  # the model left it empty: use the registry's level
        lvl = REGISTRY.get(a.get("instrument_id"), {}).get("level")
        ts["types"] = [lvl] if lvl else ["national"]
        fixes.append(f"territorial_scope.types set from registry level {lvl!r}")
    if c.get("primary_domain") and c["primary_domain"] not in c.get("domains", []):
        c.setdefault("domains", []).append(c["primary_domain"])
        fixes.append("added primary_domain to domains")
    return fixes


def item_key(path: str) -> tuple:
    """('events', 3) for '$.events[3].evidence'; other paths sort first and are never list items."""
    keys = path.removeprefix("$").replace("[", ".").replace("]", "").strip(".").split(".")
    return (keys[0], int(keys[1])) if keys[0] in SECTIONS and len(keys) > 1 and keys[1].isdigit() else (path, -1)


def resolve(a: dict, path: str):
    """'$.events[3].evidence' -> (container, key) of the node holding the quote."""
    node, keys = a, path.removeprefix("$").replace("[", ".").replace("]", "").strip(".").split(".")
    for k in keys:
        node = node[int(k)] if isinstance(node, list) else node[k]
    return node


def set_quote(a: dict, path: str, quote: str) -> None:
    resolve(a, path)["quote"] = quote


def drop_item(a: dict, path: str) -> int:
    """Drop the section item that carries the failing quote (or the evidence object of a classification field)."""
    keys = path.removeprefix("$").replace("[", ".").replace("]", "").strip(".").split(".")
    if keys[0] in SECTIONS and len(keys) > 1 and keys[1].isdigit():
        del a[keys[0]][int(keys[1])]
        return 1
    parent = resolve(a, "$." + ".".join(keys[:-1])) if len(keys) > 1 else a
    parent.pop(keys[-1], None)
    return 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--split", help="tune or test (all laws in that half of tools/pilot_split.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--effort", choices=["low", "medium", "high"], default="high")
    ap.add_argument("--group-effort", nargs="*", default=["obligations=medium"], metavar="GROUP=EFFORT",
                    help="per-group effort overrides (default obligations=medium: same recall, better precision "
                         "than high in the Oct 2026 test; pass none to disable)")
    ap.add_argument("--mode", choices=list(MODES), default="focused")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ids-file", help="file with one instrument id per line")
    ap.add_argument("--batch", action="store_true", help="Message Batches API (half price; see tools/annotate_batch.py)")
    ap.add_argument("--wave", type=int, default=150, help="batch mode: laws per wave")
    ap.add_argument("--tier", default="A", choices=["A", "B"])
    ap.add_argument("--include-gold", action="store_true", help="also annotate ids that have a file in annotations/")
    args = ap.parse_args()
    if args.tier == "B" and args.mode == "focused":
        args.mode = "light"
    if args.ids_file:
        args.ids += [l.strip() for l in Path(args.ids_file).read_text().splitlines() if l.strip()]
    if args.split:
        half = json.loads((ROOT / "tools" / "pilot_split.json").read_text())[args.split]
        args.ids += half["high"] + half["control"]
    if not args.ids:
        ap.error("give instrument ids or --split")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ann = Annotator(args.model, args.effort, args.mode, out, dict(g.split("=", 1) for g in args.group_effort),
                    tier=args.tier)
    if args.batch:
        from annotate_batch import run_batch
        ids = [i for i in args.ids if args.include_gold or not (ROOT / "annotations" / f"{i}.json").exists()]
        missing = [i for i in ids if not (TEXTS / f"{i}.txt").exists()]
        if missing:
            print(f"{len(missing)} ids have no text and are skipped: {' '.join(missing[:20])}")
        try:
            run_batch(ann, [i for i in ids if i not in missing], args.wave)
        except BudgetExceeded as e:
            print(f"STOPPED: {e}", flush=True)
            sys.exit(2)
        return

    def run(iid):
        try:
            log = ann.annotate(iid)
            return f"{iid}: ${log['usd']:.4f}, quotes {log['quote_repairs']}, errors {len(log['errors'])}"
        except BudgetExceeded:
            raise
        except Exception as e:  # keep going; the failure is recorded
            (out / f"{iid}.log.json").write_text(json.dumps({"instrument_id": iid, "error": repr(e)}))
            return f"{iid}: FAILED {e!r}"

    pool = ThreadPoolExecutor(args.workers)
    try:
        for line in pool.map(run, args.ids):
            print(line, flush=True)
    except BudgetExceeded as e:
        print(f"STOPPED: {e}", flush=True)
        sys.exit(2)
    finally:
        pool.shutdown(cancel_futures=True)


if __name__ == "__main__":
    main()
