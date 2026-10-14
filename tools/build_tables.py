"""Build the annotation database from annotations/*.json (the JSON files are the source of truth).

    python tools/build_tables.py        # writes build/*.csv and build/annotations.sqlite

Tables (one row per ...):
    instruments      law: metadata from the registry + summary + scalar classifications + section counts
    classifications  (law, field, value) for every list-valued classification (domains, mechanisms, roles, AGORA tags, ...)
    events, thresholds, obligations, enforcement, definitions, relations, entities
                     one row per item, with provision + quote

Registry tables (from registry/*.jsonl and links.csv, written by tools/build_registry.py):
    registry         every instrument, annotated or not: metadata, stage, dates, region
    registry_domains, registry_keys, registry_families    (instrument, value) pairs
    links            typed relations between source records and instruments
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
CHILD = ["events", "thresholds", "obligations", "enforcement", "definitions", "relations", "entities"]
LIST_FIELDS = ["domains", "mechanisms", "addressee_roles", "affected_roles", "decision_margins", "agora_applications",
               "agora_harms", "agora_incentives", "agora_risk_factors", "agora_strategies"]


def registry() -> dict:
    path = ROOT / "registry" / "instruments.jsonl"
    if not path.exists():
        path = ROOT / "registry" / "manifest_v0.jsonl"
    rows = [json.loads(l) for l in path.open()]
    return {r.get("instrument_id", r.get("id")): r for r in rows}


def registry_tables(reg: dict) -> dict[str, list[dict]]:
    out = {"registry": [], "registry_domains": [], "registry_keys": [], "registry_families": [], "links": []}
    for iid, r in reg.items():
        prov = r.get("provenance", {})
        out["registry"].append({
            "instrument_id": iid, "title": r.get("title"), "short_title": r.get("short_title"),
            "jurisdiction": r.get("jurisdiction"), "iso": r.get("iso"), "level": r.get("level"),
            "priority_region": r.get("priority_region"), "instrument_type": r.get("instrument_type"),
            "status": r.get("status"), "legislative_stage": r.get("legislative_stage"),
            "furthest_stage_before_death": r.get("furthest_stage_before_death"),
            **{f"date_{k}": v for k, v in r.get("dates", {}).items()},
            "language": r.get("language"), "official_url": r.get("urls", {}).get("official"),
            "text_connector": r.get("text_connector"), "text_access": r.get("text_access"),
            "added_by": prov.get("added_by"), "stage_basis": prov.get("stage_basis"),
            "type_basis": prov.get("type_basis"), "domains_basis": prov.get("domains_basis"),
        })
        for field, table in (("domains", "registry_domains"), ("keys", "registry_keys"), ("family_ids", "registry_families")):
            out[table] += [{"instrument_id": iid, "value": v} for v in r.get(field, [])]
    links = ROOT / "registry" / "links.csv"
    if links.exists():
        out["links"] = list(csv.DictReader(links.open()))
    return out


def flatten(item: dict) -> dict:
    out = {}
    for k, v in item.items():
        if k in {"evidence", "external_evidence"} and isinstance(v, dict):
            for kk, vv in v.items():
                out[f"{'ext_' if k == 'external_evidence' else ''}{kk}"] = vv
        else:
            out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
    return out


def main() -> None:
    reg = registry()
    tables: dict[str, list[dict]] = {t: [] for t in ["instruments", "classifications", *CHILD]}
    if (ROOT / "registry" / "instruments.jsonl").exists():
        tables.update(registry_tables(reg))
    for path in sorted((ROOT / "annotations").glob("*.json")):
        a = json.loads(path.read_text(encoding="utf-8"))
        iid, c, meta = a["instrument_id"], a["classification"], reg.get(a["instrument_id"], {})
        tables["instruments"].append({
            "instrument_id": iid, "title": meta.get("title"), "jurisdiction": meta.get("jurisdiction"),
            "iso": meta.get("iso"), "instrument_type": meta.get("instrument_type"), "status": meta.get("status"),
            "priority_region": meta.get("priority_region"), "legislative_stage": meta.get("legislative_stage"),
            "tier": a["tier"], "summary_short": a["summary"]["short"], "summary_long": a["summary"].get("long"),
            "primary_domain": c["primary_domain"], "binding_force": c["binding_force"],
            "territorial_scope": "|".join(c["territorial_scope"]["types"]),
            "first_in_force": min((e["date"] for e in a["events"] if e["event"] == "entry_into_force" and e["date"]), default=None),
            **{f"n_{s}": len(a.get(s, [])) for s in CHILD},
            "text_version": a["text_version"]["version"], "text_language": a["text_version"]["language"],
            "annotator": a["annotator"]["model"], "annotated_on": a["annotator"]["date"], "validation": a["validation"],
        })
        for field in LIST_FIELDS:
            for value in c.get(field, []):
                tables["classifications"].append({"instrument_id": iid, "field": field, "value": value})
        for section in CHILD:
            for i, item in enumerate(a.get(section, [])):
                tables[section].append({"instrument_id": iid, "item": i, **flatten(item)})

    BUILD.mkdir(exist_ok=True)
    db_path = BUILD / "annotations.sqlite"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    for name, rows in tables.items():
        cols = list(dict.fromkeys(k for r in rows for k in r)) or ["instrument_id"]
        with open(BUILD / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        con.execute(f'CREATE TABLE "{name}" ({", ".join(f"{chr(34)}{c}{chr(34)}" for c in cols)})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" for _ in cols)})',
                        [[r.get(c) for c in cols] for r in rows])
        print(f"{name:16s} {len(rows):5d} rows")
    con.commit()
    con.close()
    print(f"-> {BUILD}/*.csv and {db_path.name}")


if __name__ == "__main__":
    main()
