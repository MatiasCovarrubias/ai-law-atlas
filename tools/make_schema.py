"""Generate schemas/annotation.schema.json from schemas/vocab.json (run after editing the vocabulary)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = json.loads((ROOT / "schemas" / "vocab.json").read_text())


def enum(key):
    return {"enum": V[key]}


def arr(item, **kw):
    return {"type": "array", "items": item, **kw}


def obj(props, required=()):
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(required)}


STR, NSTR, BOOL = {"type": "string"}, {"type": ["string", "null"]}, {"type": "boolean"}
NUM = {"type": ["number", "null"]}
EVIDENCE = obj({"provision": STR, "quote": STR}, ["provision", "quote"])
EXTERNAL = obj({"url": STR, "description": STR, "accessed": STR}, ["url", "description"])
SECTION_STATUS = {"type": "object", "additionalProperties": False,
                  "properties": {s: obj({"status": {"enum": ["not_applicable", "not_found"]}, "note": STR}, ["status"])
                                 for s in V["sections"]}}

schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": f"Annotation v{V['_version']} (docs/CODEBOOK.md)",
    **obj({
        "schema_version": {"enum": ["1.1", "1.2", V["_version"]]},  # 1.2 and 1.3 only added values and optional fields
        "instrument_id": STR,
        "tier": {"enum": ["A", "B", "C"]},
        "annotator": obj({"model": STR, "method": STR, "date": STR, "prompt_version": STR}, ["model", "method", "date"]),
        "validation": {"enum": ["machine", "spot-checked", "validated"]},
        "text_version": obj({"source": STR, "url": STR, "retrieved": STR, "version": STR,
                             "language": STR, "extent": {"enum": ["full", "partial", "excerpt"]}, "note": STR},
                            ["source", "url", "retrieved", "version", "language", "extent"]),
        "summary": obj({"short": STR, "long": STR}, ["short"]),
        "classification": obj({
            "domains": arr(enum("domains"), minItems=1),
            "primary_domain": enum("domains"),
            "mechanisms": arr(enum("mechanisms")),
            "binding_force": enum("binding_force"),
            "addressee_roles": arr(enum("addressee_roles")),
            "affected_roles": arr(enum("addressee_roles")),
            "decision_margins": arr(enum("decision_margins")),
            "territorial_scope": obj({"types": arr(enum("territorial_scope"), minItems=1), "description": STR,
                                      "evidence": EVIDENCE}, ["types", "description"]),
            "agora_applications": arr({"enum": V["agora"]["applications"]}),
            "agora_harms": arr({"enum": V["agora"]["harms"]}),
            "agora_incentives": arr({"enum": V["agora"]["incentives"]}),
            "agora_risk_factors": arr({"enum": V["agora"]["risk_factors"]}),
            "agora_strategies": arr({"enum": V["agora"]["strategies"]}),
        }, ["domains", "primary_domain", "mechanisms", "binding_force", "addressee_roles", "decision_margins",
            "territorial_scope"]),
        "events": arr(obj({"event": enum("event_types"), "date": NSTR, "date_type": enum("date_types"),
                           "date_text": STR, "anchor": STR, "depends_on": STR, "applies_to": STR,
                           "offset_value": NUM, "offset_unit": enum("offset_units"), "anchor_event": enum("event_types"),
                           "evidence": EVIDENCE, "external_evidence": EXTERNAL,
                           "date_computed_from": STR},
                          ["event", "date", "date_type", "applies_to"])),
        "thresholds": arr(obj({"variable": enum("threshold_variables"), "variable_text": STR,
                               "operator": enum("operators"), "value": NUM, "value_max": NUM, "value_text": STR,
                               "unit": STR, "currency": NSTR, "indexed": BOOL, "measurement": STR,
                               "level": {"enum": ["entity", "group", "facility", "service", "model", "product", "person",
                                                  "jurisdiction", "other"]},
                               "trigger": enum("threshold_triggers"), "consequence": STR, "conditions": STR,
                               "depends_on": STR, "evidence": EVIDENCE},
                              ["variable", "operator", "value", "value_text", "unit", "trigger", "consequence",
                               "evidence"])),
        "obligations": arr(obj({"type": enum("obligation_types"), "addressee": enum("addressee_roles"),
                                "also_addressees": arr(enum("addressee_roles")),
                                "modality": {"enum": ["shall", "shall_not", "may", "encouraged"]},
                                "summary": STR, "applies_from": NSTR, "conditional_on": STR, "evidence": EVIDENCE},
                               ["type", "addressee", "summary", "evidence"])),
        "enforcement": arr(obj({"sanction_type": enum("sanction_types"), "authority": STR, "triggered_by": STR,
                                "max_penalty_text": NSTR, "max_penalty_amount": NUM, "currency": NSTR,
                                "penalty_formula": NSTR, "penalty_reference": NSTR, "private_right_of_action": {"type": ["boolean", "null"]},
                                "cure_period": NSTR, "evidence": EVIDENCE},
                               ["sanction_type", "authority", "triggered_by", "evidence"])),
        "definitions": arr(obj({"term": STR, "defined_in": STR, "evidence": EVIDENCE}, ["term", "evidence"])),
        "relations": arr(obj({"relation": enum("relation_types"), "target_instrument_id": NSTR, "target_text": STR,
                              "note": STR, "evidence": EVIDENCE}, ["relation", "target_text", "evidence"])),
        "entities": arr(obj({"name": STR, "role": enum("entity_roles"), "function": STR, "lei": NSTR, "ticker": NSTR,
                             "evidence": EVIDENCE}, ["name", "role", "evidence"])),
        "section_status": SECTION_STATUS,
        "notes": STR,
    }, ["schema_version", "instrument_id", "tier", "annotator", "validation", "text_version", "summary",
        "classification", "events", "section_status"]),
}
(ROOT / "schemas" / "annotation.schema.json").write_text(json.dumps(schema, indent=1, ensure_ascii=False))
print("wrote schemas/annotation.schema.json")
