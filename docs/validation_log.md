# Validation log

## VP-1.0 paired validation of prompt versions, 14 Oct 2026 (formal)

Protocol: `docs/VALIDATION_PROTOCOL.md`. Full record: `validation/2026-10-paired/` (sample with seed and strata,
blinded pairs, the checkers' independent reference lists, item-level verdicts, the key, `report.md`).

- **Design:** 15 Tier A laws drawn at random (seed 20261014; strata region × length; frame of 280 laws annotated
  with prompt `1.3-718f7c3f`). Each law re-annotated with the current prompt `1.3-030c54d5`. Five Claude Opus 5.5
  checkers judged both versions blind (X/Y in random order, metadata stripped), after writing an independent
  reference list of thresholds, key dates and sanction regimes from the text.
- **Results** (items pooled; 95% Wilson intervals):

| | old `718f7c3f` | new `030c54d5` |
|---|---|---|
| Precision, all items | 88% [85–90] | 93% [91–95] |
| obligations | 84% | 93% |
| events | 92% | 97% |
| thresholds | 92% | 94% |
| enforcement | 88% | 87% |
| Recall vs independent reference | 87% [83–90] | 96% [93–97] |
| thresholds / key dates / sanctions | 82% / 85% / 100% | 94% / 95% / 100% |
| "unsupported" items (not in text or wrong kind) | 7% | 1% |
| Paired by law (precision) | | better on 9, worse on 3, equal on 3 |

- **Decision:** prompt frozen at `1.3-030c54d5`; all Tier A machine annotations made with older prompts re-run with
  it (Gold and the 25 Opus-corrected files kept).
- **Caveats:** model-on-model validation (not human expert review). The protocol has no verdict for over-splitting
  an article into several rows (checkers scored such rows `correct` unless the content repeated), so obligation
  precision is somewhat generous; a VP-1.1 should add `over_split`. Two checkers restricted the reference list to
  operative articles on very long texts, and long sections were judged on systematic samples (recorded per law).
  The higher figures than the earlier spot-check below partly reflect that the code fixes (dates, duty types)
  were already applied to both versions.
- **Text problems found:** `us-obbba-2025`'s text joins AGORA excerpts of the House-passed bill and the enacted law
  (4 sections in two versions); AGORA `part_of` concatenation can mix versions.

## VP-1.0 paired validation, Tier B, 14 Oct 2026

Record: `validation/2026-10-paired-B/`. 10 Tier B laws drawn at random (seed 20261015; frame 362 annotated with
`1.3-interim-20261014`), re-annotated in light mode with the frozen `1.3-030c54d5`, judged blind by three Opus
checkers on thresholds and events (checker addendum `validation/CHECKER_PROMPT_TIER_B.md`).

| | interim (current Tier B) | frozen |
|---|---|---|
| Precision, all items | 85% [77–90] | 91% [85–95] |
| thresholds / events | 72% / 91% | 87% / 94% |
| Recall vs independent reference | 92% [84–96] | 91% [82–95] |
| Paired by law (precision) | | better on 3, worse on 2, equal on 5; mean +0.2 points |

- **Decision: Tier B is not re-run.** The pooled gain comes almost entirely from one law (il-privacy-amendment13-2024,
  fine mechanics coded as thresholds by the interim prompt); per law the versions are equivalent and the intervals
  overlap.
- **Code fix from this run:** for instruments not yet adopted, no date is computed from adoption, signature,
  publication or entry into force (a bill's "entry into force on publication" has not happened; a gazette printing a
  bill is not its publication). Re-applied to all machine annotations (25 files changed).
- **Text problems found:** es-prd-datacenters-efficiency-2025 is the consultation web page only (draft decree PDF not
  cached); sg-eliona-2024 is the bill as introduced, not the enacted act.

## Earlier: informal spot-check, 14 Oct 2026

**What was checked.** 26 machine annotations (Claude Haiku 5.5, batch mode, focused, high effort, obligations at
medium effort) from `build/rollout/tierA/`, stratified by region (P1-China 3, P1-Europe 6, P1-LATAM 5, P1-US 10, P2 1, P3 1).
Checked by Claude Opus 5.5 subagents in Claude Code against the cached text, following `playbooks/verify.md`.
Long texts were checked on a stratified sample of items (marked `sampled` in the record). Errors were corrected in
place and the files set to `validation: "spot-checked"`. Per-law records: `build/rollout/spotcheck/<id>.json`.

**Agreement** (items right as given, before correction):

| Section | Agree / checked |
|---|---|
| Thresholds | 232/264 (88%) |
| Events | 446/517 (86%) |
| Dates computed in code | 11/32 (34%) |
| Obligations | 688/915 (75%) (type wrong 102, addressee wrong 68) |
| Enforcement | 126/139 (91%) |
| Classification right as given | 13/26 laws |

Items added by checkers (missed by the model): thresholds 9, events 4, obligations 24, enforcement 4.

**Error patterns and what was done**

| Pattern | Fix |
|---|---|
| Relative dates computed from the wrong anchor: periods counted from another act's event (implementing rules, secondary laws, staggered commencement) filled from the instrument's single entry-into-force date; ambiguous anchors in consolidated texts; "N months before" added instead of subtracted | `resolve_dates` rewritten: registry anchors only when the text shows no staggered or conditional version of the event, ambiguous event types never used, "before" periods with positive offsets skipped, chained anchors, provenance in the new field `date_computed_from`; `recompute_dates` re-applied to all machine files; guidance: anchor_event only for this instrument's events, negative offsets for "before" |
| Duties of public bodies typed by content (reporting, disclosure, cooperation) instead of `government_duty` | code fix for generic types on `government_agency`/`regulator` (AI-use types kept, codebook 3.5); guidance |
| `enterprise` as a catch-all addressee | guidance: most specific role |
| Penalties and rights to sue repeated as obligations or events; infringement lists coded as duties; non-duties (no-monitoring clauses, burden of proof) | guidance |
| Restated unchanged text of amending acts coded as new | guidance |
| Obligation parameters, damages and procedural periods coded as thresholds | guidance |
| Dates of other acts (EU transposition deadlines in preambles, amending acts in consolidated texts) coded as events | guidance |
| Federal Register PDFs carrying neighbouring rules (annotations took content from unrelated rules) | `tools/trim_fr.py` trims texts to the target FR Doc; 7 affected machine annotations re-run |
| `external` date type for dates printed in the document; empty `anchor`; empty territorial scope | deterministic fixes in `fix_bookkeeping` |
| `ai_sectoral` for semiconductor or compute industrial policy; overview of long texts based on the first part only | guidance |
| Texts that were not the full instrument (Irish DPA contents page only) | text replaced, law re-annotated |

The fixes apply to annotations made after 14 Oct 2026 (Tier A extras, Tier B) and, for the code fixes, to all
machine files via `tools/refix.py`. Thresholds and enforcement amounts were the most reliable sections throughout.

**Per law** (agree / checked):

| id | thresholds | events | computed dates | obligations | enforcement | classification right |
|---|---|---|---|---|---|---|
| br-brasil-semicon-lei14968-2024 | 19/19 | 17/18 | 2/2 | 14/22 | 1/1 | fixed |
| br-mme-portaria129-2026 | – | 14/17 | 1/1 | 28/31 | 3/3 | yes |
| cl-cmf-ncg514-2024 | 13/13 | 26/27 | 1/1 | 64/65 | 3/3 | fixed |
| cl-ley21521-2023 | 40/40 | 32/41 | 0/4 | 53/54 | 12/12 | fixed |
| cn-digital-virtual-human-measures-draft-2026 | 1/1 | 4/4 | – | 25/26 | 5/5 | yes |
| cn-platform-price-conduct-rules-2025 | – | 3/3 | 1/1 | 27/28 | 2/2 | yes |
| cn-security-management-measures-application-facial-recognition-2025 | 2/2 | 4/4 | – | 16/20 | 2/2 | yes |
| es-rdl-24-2021-dsm | 48/59 | 83/106 | 0/10 | 90/143 | 28/33 | fixed |
| eu-chips-act-2023 | 1/2 | 22/23 | 1/2 | 27/52 | 3/3 | fixed |
| eu-dora-2022 | 10/13 | 50/53 | 1/2 | 117/149 | 7/7 | yes |
| ie-dpa-2018 | – | 1/1 | – | – | – | fixed |
| in-dpdp-act-2023 | 4/4 | 7/9 | 0/2 | 30/31 | 10/10 | fixed |
| it-garante-2022-clearview | – | 8/9 | – | 7/8 | 6/7 | yes |
| mx-cpeum-simplificacion-organica-2024 | – | 10/13 | 3/6 | 20/21 | 5/5 | yes |
| my-online-safety-act-2025 | 4/4 | 10/10 | – | 33/42 | 17/17 | fixed |
| se-sfs-2022-1781-datacenter-tax | 9/9 | 6/6 | 1/1 | 8/8 | – | yes |
| us-az-ars-41-1519 | 12/13 | 15/15 | – | 8/12 | 2/4 | fixed |
| us-bis-adv-computing-2022 | 21/21 | 8/9 | – | 6/15 | – | yes |
| us-bis-fdpr-hbm-2024 | 18/19 | 14/19 | – | 17/32 | – | fixed |
| us-bis-veu-revocation-china-2025 | – | 3/4 | – | 0/4 | – | fixed |
| us-co-hb24-1147 | 2/2 | 2/2 | – | 4/6 | 4/4 | yes |
| us-co-sb288-2025 | 5/16 | 3/3 | – | 8/8 | 7/9 | yes |
| us-ferc-pjm-colocation-2025 | 11/13 | 12/29 | – | 43/53 | 4/4 | yes |
| us-fr-2024-07915 | – | 1/1 | – | – | – | fixed |
| us-ndaa-fy2026-coins-2025 | 6/6 | 86/86 | – | 41/82 | 2/3 | fixed |
| us-ut-hb464-2024 | 6/8 | 5/5 | – | 2/3 | 3/5 | yes |
