# Meta-corpus design: one registry of instruments, many sources

Goal: a comprehensive, research-grade set of AI, digital and data-center regulation. It aggregates existing
corpora and trackers (AGORA first) and adds our own extensions. Its distinctive layer is neutral, quote-backed
annotation (`docs/CODEBOOK.md`). Research-specific outputs, such as causal-variation screens, live in the projects
that produce them, keyed to `instrument_id`.

## Why overlaps are hard: sources record different things

| Source | One record is… | Consequence |
|---|---|---|
| AGORA | a *document*, which is often part of a law. 357 of 1,255 records (28%) are sections: 36 sections of the FY2026 NDAA, 19 of the OBBBA, and so on. 709 are bills (proposed or defunct). | Many AGORA records map to one instrument, and bills are not laws |
| Our manifest | an *adopted instrument* (law, regulation, decree, order, ordinance) | Closest to the unit researchers need |
| Bill trackers (NCSL, LegiScan) | a *bill*, with versions across sessions | One bill can become a chaptered law and then a codified statute |
| OECD.AI and similar | a *policy initiative*, which may bundle several instruments, strategies and programmes | One-to-many, and often not legal text |
| Digital Policy Alert | a *dated event* in a policy process | Many events per instrument; ideal for event timelines, not for text |
| Official APIs (EUR-Lex, legislation.gov.uk, Federal Register) | a *legal act and its versions* (original, consolidated, amendments) | Canonical identifiers and text |

So deduplication is not a yes/no question. Each source record is linked to a canonical instrument by a typed
relation.

## Data model

```
instrument       canonical legal act (or bill, if it never passed)
  instrument_id  our ID, stable (e.g. eu-ai-act-2024)
  keys           canonical identifiers: EU:REG:2024:1689, US:EO:14110, US:FR:2025-00636,
                 US-CA:BILL:2025:SB53, UK:uksi:2025:226, US:PL:116-283, ... (tools/keys.py)
  jurisdiction, level, instrument_type, status, dates (proposed/adopted/effective/phase-in/repealed)
  topics, language, canonical_text_source

source_record    one record in one source (AGORA ID, OECD.AI ID, DPA event ID, our scout row, ...)
  source, source_id, url, license, retrieved_at, raw metadata

link             source_record -> instrument, with a typed relation:
  same | part_of (section/chapter/provision) | version_of (bill text, consolidated text, translation)
  | amends | implements (decree, guidelines, delegated act) | codifies (session law -> code section)
  | bill_of (bill that became the instrument) | event_of (dated procedural event) | summarizes (tracker entry)
  method: key | title | llm | manual;  confidence

family           groups of instruments: a law + its decrees + amendments (e.g. Korea AI Basic Act + Decree),
                 and cross-jurisdiction panels (e.g. US state privacy laws, election-deepfake laws,
                 data-center sales-tax exemptions, national AI Act implementation laws)

annotation       neutral annotations (docs/CODEBOOK.md), keyed to instrument_id (+ provision citations)
```

## Entity resolution (tested on AGORA × our manifest, Oct 2026)

1. **Canonical keys from URLs and titles** (`tools/keys.py`): CELEX/ELI, legislation.gov.uk paths,
   congress.gov bill URLs, public-law numbers, Federal Register document numbers, EO numbers, state bill
   numbers. 1,022 of 1,255 AGORA records and 166 of 410 manifest entries yield at least one key.
2. **Title matching within blocks** of the same jurisdiction and year, for records without keys.
3. **Adjudication** of ambiguous pairs: a cheap model reads both records and picks a relation, and a human
   reviews the residual.

**What the test showed.**
- **Title matching alone:** 56 true overlaps, plus 14 false positives in the 0.35–0.6 score band. It also
  pointed 3 true overlaps to the wrong AGORA record.
- **Keys alone:** 24 of those 56, plus 6 true overlaps that title matching missed.
- **Keys also surfaced related records:** an AI Act guideline is matched to the Act because its title cites the
  Act, and Colorado SB 25B-004 is matched to SB 24-205 because its title mentions it. These are `implements`
  and `amends` relations, not duplicates. Keys taken from titles must therefore be typed by where they occur
  (own identifier vs a citation).
- **Combined:** 62 same-instrument overlaps (`registry/crosswalk/agora_overlap_verified.csv`), which neither method finds alone.

**Pitfalls to handle.**
- Bill numbers repeat across sessions, so always store the session or year. Session-free keys only serve
  blocking.
- Codification: a Minnesota statute section (609.771) was created by HF 1370. Link them with `codifies`.
- Partial records: AGORA stores only Section 8 of CA AB 2355. Link with `part_of`.
- Translations and consolidated versions are `version_of`, not new instruments.

## Source tiers (from `sources/source_assessment.md`, assessed 11 Oct 2026)

| Tier | Sources | What we store |
|---|---|---|
| **Backbone** | AGORA v1.33.0 (CC BY-NC 4.0) | IDs, names, links, dates and status, collapsed to instruments; texts and segments stay in the local cache |
| **Canonical text and IDs** (INGEST) | EUR-Lex/Cellar, legislation.gov.uk (OGL), Federal Register, govinfo, Congress.gov (public domain), Korea law.go.kr, Brazil LexML (access unconfirmed) | Identifiers, versions, official text |
| **Bills** (INGEST) | LegiScan (CC BY 4.0 per its site; needs a free key) | Every US state and federal bill, including failed ones: panels and announcement events |
| **Policy initiatives** (INGEST, subset) | EC-OECD STIP Compass, the 368 AI-tagged initiatives (licence presumed CC BY 4.0) | Strategies and programmes, linked with `summarizes` |
| **Link only** | OECD.AI Policy Navigator, Digital Policy Alert (API needs a licence), China flk.npc.gov.cn, Good Jobs First, MIT AI Governance Map (CC BY 4.0; INGEST if an export appears) | IDs, URLs, our own metadata |
| **Reference only** | IAPP, NCSL, MultiState, law-firm trackers, FLI AI Act Explorer, Stanford AI Index, CAIDP, UNCTAD, DLA Piper, data-center moratorium trackers | Coverage checklists and citations |
| **Our extensions** | non-US AI laws in original languages; privacy and platform law; data-center, compute and energy rules (no reusable bulk source exists); neutral annotations | Full records |

## Build order

1. Instruments = our manifest ∪ all AGORA documents (enacted, proposed, defunct), collapsed to instrument level
   (parts → parent). **Done: registry v1, see below.**
2. Add official-API identifiers and canonical text: EUR-Lex, legislation.gov.uk, Federal Register / govinfo,
   state legislatures, law.go.kr.
3. Link trackers that cannot be redistributed by ID and URL only (see `sources/source_assessment.md`).
4. Build families and panels.
5. Attach annotations.

Licensing determines what can be republished. AGORA is CC BY-NC 4.0: we link and cite it and keep only the
facts needed to identify an instrument, never its texts, summaries, tags or segments. For sources with
restrictive terms, store only IDs, URLs and our own metadata.

## Registry v1 build (`tools/build_registry.py`, 11 Oct 2026)

**Inputs → outputs.** Our records (`manifest_v0.jsonl`, frozen; `additions.jsonl`), manual inputs
(`overrides.jsonl`, `crosswalk/agora_overlap_verified.csv`, `crosswalk/agora_manual.csv`, `links_manual.csv`)
and the AGORA cache go in. `instruments.jsonl`, `links.csv`, `families.jsonl`, `coverage.md` and
`crosswalk/agora_excluded.csv` come out, and every record and link is validated against its schema. Rebuilding is
deterministic; generated IDs stay stable because the builder reuses the instrument each AGORA record was
linked to in the previous `links.csv`.

**Steps.**
1. Our records become instruments. Scout topics map to `vocab.json` domains; statutes that never passed
   become `bill`; proposal and veto dates move out of `dates.adopted`.
2. AGORA records are grouped. Records sharing a canonical key from their link (or, failing that, the same
   link) are parts of one act. For federal bills, the link, the plaintext source and the record's name vote on
   the bill number, because AGORA sometimes files two companion bills under one link.
3. Each group is matched to our records: adjudicated rows first, then an exact canonical key (session-free
   state bill numbers are never used to merge). If our record covers the whole act, the group joins it. If our
   record is one part (the National AI Initiative Act inside the FY2021 NDAA), the rest of the group becomes the
   parent and our record is linked to it with `part_of`.
4. Unmatched groups become new instruments. The parent title comes from AGORA's official names with the
   section stripped, preferring the omnibus name (Appropriations, NDAA) over named sub-acts. Type, stage and
   domains come from rules on the title and AGORA's Enacted / Proposed / Defunct, labelled in `provenance`.
   AGORA's Proposed becomes `introduced` unless the title marks a published draft regulation (then
   `advanced`). Defunct becomes `dead` for bills and `repealed` for executive acts.
5. Records are linked: `same` for the whole act, `part_of` for sections and for named sub-acts inside an
   omnibus, `version_of` for drafts that were later adopted.
6. Overrides are applied, families are built by title rules (plus explicit members), text access is read
   from the access log, and the coverage report and review queue are written.

**What adjudication found** (beyond the Oct 2026 crosswalk): laws that AGORA stores only as sections (OBBBA,
CHIPS and Science), duplicates within AGORA (6), AGORA records of our instruments that the crosswalk missed
(Texas HB 149, Colorado SB 21-169, the AI Liability Directive proposal, and the bills behind Michigan PA 263 of
2023, Illinois PA 103-0804 and the NY RAISE Act), and consultation drafts later adopted (4 Chinese, 1 CPPA). All are in
`crosswalk/agora_manual.csv`. An ID collision during the build signals a likely duplicate and goes to the review
queue.
