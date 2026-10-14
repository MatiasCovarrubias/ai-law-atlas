# AI Law Atlas

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23374030.svg)](https://doi.org/10.5281/zenodo.23374030)

The AI Law Atlas collects the laws, regulations and bills that govern artificial intelligence and what it runs on:
data, compute, chips, data centers and energy. It turns them into data that can be counted and compared. Each
instrument is listed in a registry with its jurisdiction, stage and dates. For the more consequential ones, the
legal facts inside the text are recorded as structured items: when the rules apply, which numbers trigger them, who
must do what, and what happens to those who don't. Every item cites its provision and quotes the text verbatim, so
it can be checked against the source. The atlas covers proposals as well as enacted laws, including bills that
failed.

Release v1.0.1 (October 2026). Research data, not legal advice.

## How it is built

**Sources.** The registry combines three inputs:

- a hand-curated list of instruments;
- targeted searches by region and topic, aimed at areas that existing trackers cover thinly, such as data-center,
  compute and energy rules and Latin America;
- the AGORA archive of AI legislation (Emerging Technology Observatory).

Records are matched and de-duplicated in code. Texts come from AGORA's plaintext collection, official websites,
official open-data services (EU Cellar, France's JORF, Japan's e-Gov, LeyChile, Normattiva, govinfo) and archived
copies of official pages where a site blocks automated access. Each annotation records which text it used and when
that text was retrieved.

**Triage.** Not every instrument needs the same depth. A model reads each instrument's metadata, summary and opening
text and answers four questions, each scored 0 to 2:

- Does it change a decision a firm or household makes?
- Does it have bite (sanctions, conditions, money)?
- How far does it reach?
- How specific is it?

It also applies two gates: whether the instrument is binding, and whether it reaches private actors. Tiers are then
set in code from these answers and the registry's legislative stage:

- **Tier A (in-depth annotation):** binding, reaches private actors, scores high, is adopted or advanced, and comes
  from a priority region (United States, Europe, China, Latin America) or is globally consequential.
- **Tier B (light annotation):** binding instruments with a moderate score, high-scoring bills, and members of the
  diffusion panels.
- **Tier C (registry only):** everything else, such as non-binding documents, rules that reach only the state,
  low-scoring instruments and dead proposals.

Manual overrides are recorded with a reason.

**Annotation.** Claude Haiku 5.5 reads the full text of each Tier A and B instrument and fills the fields defined in
the codebook (`docs/CODEBOOK.md`). Long texts are read in parts. Every item must carry a verbatim quote. Code then
does four things:

- it checks each quote against the text and asks the model to repair a quote that fails, or drops the item;
- it computes calendar dates from relative provisions ("24 months after entry into force") and records how;
- it applies fixed consistency rules;
- it validates each file against the schema.

The prompt is versioned and archived (`docs/prompts/`), and each file records the version that produced it.

**Checks.** Accuracy was measured with a blinded protocol (`docs/VALIDATION_PROTOCOL.md`):

1. A seeded random sample of instruments is drawn, stratified by region and text length.
2. A stronger model (Claude Opus 5.5) writes its own list of each instrument's key facts before seeing any
   annotation. Recall is measured against this list.
3. The same model judges every annotated item against the text, without knowing which annotation version it is
   looking at. Precision comes from these verdicts.

The protocol also compared prompt versions on the same laws, and Tier A was re-annotated after the comparison
favoured the revised prompt. In addition, 26 Tier A instruments have hand-curated reference annotations, and 25 were
corrected by the checker (`validation` = "spot-checked").

| | Precision (items correct) | Recall (vs independent reference) |
|---|---|---|
| Tier A, current prompt | 93% [91–95] | 96% [93–97] |
| Tier B | 85% [77–90] | 92% [84–96] |

Brackets are 95% intervals. Details are in `docs/validation_log.md` and the records in `validation/`. This is
model-on-model validation; a human review of the checker's verdicts is planned.

## Contents

| | |
|---|---|
| Registry | 1,574 instruments from 57 countries and blocs (190 jurisdictions, including US states and subnational units). Stage: 829 adopted, 41 advanced, 314 introduced, 390 dead. |
| Annotations | 770 instruments annotated: 408 in depth (Tier A), 362 in a light format (Tier B). |
| Annotated items | 9,935 dated events, 5,076 thresholds, 13,886 obligations, 2,341 sanction regimes, 5,960 definitions, 8,151 relations, 3,004 named entities. |
| Diffusion panels | 9 families of related instruments (e.g. US state privacy laws, data-center tax exemptions, European AI Act implementation laws), including bills that failed. |

**Registry** (`registry/`): one record per instrument, with jurisdiction, level, type, legislative stage and
dates, policy domains, official URLs, provenance, a triage tier (A, B or C), and links to related instruments and to
the AGORA dataset.

**Annotations** (`annotations/<instrument_id>.json`, schema in `schemas/annotation.schema.json`, rules in
`docs/CODEBOOK.md`):

- **Events:** adoption, publication, entry into force, application dates, phase-in steps, deadlines, sunsets. Dates
  computed from relative provisions record how they were computed.
- **Thresholds:** the numbers at which legal treatment changes (variable, operator, value, unit, and what crossing
  it triggers).
- **Obligations:** duty type, addressee role, modality and start date (Tier A).
- **Enforcement:** sanction type, maximum amounts or formulas, authority, private right of action (Tier A).
- **Definitions, relations** (amends, implements, transposes, repeals) **and named entities** (Tier A; relations in
  both tiers).
- **Classification:** domains, mechanisms, decision margins, addressee roles, territorial scope.

Each item carries `evidence.provision` and `evidence.quote`. Each file records the model, the prompt version
(`annotator.prompt_version`), the text version used and its validation status.

**Tables** (`data/`): the same content as CSV files and one SQLite database (`annotations.sqlite`).

**Code** (`tools/`, `connectors/`): the full pipeline, from registry building and text retrieval to triage,
annotation, validation and table building.

## Using the data

Each CSV table has one row per item, keyed by `instrument_id`; `instruments` has one row per annotated instrument and
`registry` one row per registry record. In Python, `pd.read_csv("data/thresholds.csv")`, or with SQLite:

```sql
-- Power-capacity thresholds in MW and what crossing them triggers
SELECT i.jurisdiction, t.value, t.unit, t.consequence, t.provision
FROM thresholds t JOIN instruments i USING (instrument_id)
WHERE t.variable = 'power_capacity' AND t.unit = 'MW'
ORDER BY t.value DESC;

-- Application dates in the next year
SELECT e.date, i.jurisdiction, i.title, e.applies_to
FROM events e JOIN instruments i USING (instrument_id)
WHERE e.event = 'application' AND e.date BETWEEN '2026-10-15' AND '2027-10-15'
ORDER BY e.date;

-- Duties placed on AI providers, by type
SELECT type, COUNT(*) AS n FROM obligations
WHERE addressee = 'ai_provider' GROUP BY type ORDER BY n DESC;
```

Every row carries `provision` and `quote`, so any item can be checked against the official text.

## Limitations

- Coverage is curated, not a census. The United States is heavily represented (many federal bills come from AGORA).
  Tier C instruments (790) have registry data only.
- Annotations are machine-made and point-in-time: texts were retrieved in October 2026 and later amendments are not
  reflected. Some texts are partial or are bill versions; `text_version` says which.
- Labels follow the codebook's conventions; some fields use `other` frequently (threshold variables, obligation
  types).
- Not legal advice. Check the official text before relying on any item.

## Licenses

- Registry, annotations, tables and documentation: **CC BY 4.0** (`LICENSE`).
- AGORA-derived records and files: **CC BY-NC 4.0**, see `NOTICE.md`.
- Code (`tools/`, `connectors/`): **MIT** (`LICENSE-code`).
- Law texts are not distributed.

## Citation

DOI: [10.5281/zenodo.23374030](https://doi.org/10.5281/zenodo.23374030) (archived on Zenodo). See `CITATION.cff`. Please also cite AGORA when using AGORA-derived records.
