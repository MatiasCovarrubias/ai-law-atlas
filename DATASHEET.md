# Datasheet: AI Law Atlas v1.0

Following "Datasheets for Datasets" (Gebru et al.).

## Motivation

- **Purpose:** structured, verifiable data on law governing AI and its inputs (data, compute, chips, data centers,
  energy), for research, policy analysis and tools. Existing trackers list documents and tags; this dataset records
  the legal facts inside them (dates, thresholds, duties, sanctions) with evidence.
- **Creator and funding:** Matías Covarrubias, as an independent research project. Model costs were paid out of
  pocket (about USD 70 of API use).

## Composition

- **Instances:** legal instruments (statutes, regulations, decrees, executive orders, regulator decisions,
  ordinances, guidance, bills and drafts). 1,574 in the registry; 770 annotated.
- **Unit of annotation:** one instrument, as published at the retrieval date (a law, or a bill version). Items
  inside an annotation (events, thresholds, obligations, sanctions, definitions, relations, entities) each cite a
  provision and a verbatim quote.
- **Tiers:**
  - **Tier A** (408): in-depth annotation.
  - **Tier B** (362): light annotation (classification, summary, events, thresholds, relations).
  - **Tier C** (790): registry only. These are not binding, do not reach private actors, or are low-scoring or
    dead proposals.

  Tiers come from a triage step (`registry/triage.jsonl`).
- **Coverage:** 57 countries and blocs, 190 jurisdictions. United States 1,034 records (many federal bills from
  AGORA), Latin America 181, Europe 159, China 87, other 113.
- **Missing data:** 9 Tier A/B instruments have no annotation because no text could be retrieved, and one
  (`eu-dual-use-regulation-2021`) because its control list exceeded output limits.
- **Confidentiality:** all sources are public legal documents and public metadata. No personal data beyond names of
  public officials or firms named in the texts.

## Collection

- **Registry:** a curated manifest, targeted searches by region and topic, and AGORA (ETO), matched and
  de-duplicated in code (`tools/build_registry.py`).
- **Texts:** AGORA's plaintexts, official websites, official open-data services (EU Cellar, DILA JORF, e-Gov, LeyChile,
  normattiva, govinfo), and archived copies of official pages where sites block automated access. Each text's
  source and retrieval date is recorded in the annotation's `text_version`.
- **Time frame:** instruments dated 2015–2026; texts retrieved in October 2026.

## Preprocessing and annotation

- Texts extracted from HTML or PDF; some trimmed to the instrument's own provisions (recorded in `text_version`).
- Annotation by Claude Haiku 5.5 following `docs/CODEBOOK.md`, one call per group of sections, long texts in
  parts; quotes verified against the text (failures repaired once or dropped); dates of relative provisions computed
  in code; deterministic consistency rules. 26 reference annotations by Claude Opus 5.5.
- Prompt versions are recorded per file and archived in `docs/prompts/`.

## Validation

Blinded, paired validation protocol (`docs/VALIDATION_PROTOCOL.md`; results in `docs/validation_log.md`, records in
`validation/`). Tier A precision 93% [91–95], recall 96% [93–97]; Tier B precision 85% [77–90], recall 92% [84–96].
The checker is a stronger model, not a human; a human review of its verdicts is planned.

## Uses

- **Intended:**
  - empirical research on AI and digital regulation (event studies, difference-in-differences, discontinuity and
    bunching designs at statutory thresholds, diffusion of laws across jurisdictions);
  - comparative policy monitoring;
  - tools and agents that need structured legal facts with evidence.
- **Not intended:** legal advice or compliance decisions without checking the official text.

## Distribution and maintenance

- Licenses: CC BY 4.0, with AGORA-derived content under CC BY-NC 4.0 (`NOTICE.md`). Code under MIT. Law texts are
  not redistributed.
- Versioned releases. Errors can be reported as issues on the repository.
