# Validation protocol VP-1.0: machine annotations checked by a stronger model

Purpose: measure, in a documented and repeatable way, how accurate the machine annotations (Claude Haiku 5.5) are
and whether a prompt version improves on another. The checker is Claude Opus 5.5 run as subagents in Claude Code.
This is model-on-model validation: it is documented as such and is not a substitute for human expert review (see
Limitations).

## 1. Design

- **Unit:** an annotation item (one threshold, event, obligation or sanction regime) and, for recall, an item of
  the checker's own reference list.
- **Paired, blinded comparison.** For a sample of laws, the same text is annotated with two prompt versions. The
  checker receives both as `X` and `Y`, in random order, with the annotator, validation status and version fields
  removed (`tools/validation.py blind`). The key stays outside the checker's files until the report is computed.
- **Sampling frame and draw** (`tools/validation.py sample`): all Tier A machine annotations of the older version
  not previously spot-checked; strata = region × text length (≤ 80K or > 80K characters); proportional allocation
  with at least one law per stratum of 3 or more; seeded random draw. Frame, strata, seed and ids are recorded in
  `validation/<run>/sample.json`.

## 2. Checker procedure (in this order)

1. **Reference list, before opening X or Y.** Read the text (`cache/texts/<id>.txt`) and list, independently:
   - every threshold: a number at which legal treatment changes (codebook 3.3);
   - key dates: adoption, publication, entry into force, each application or phase-in date, sunset;
   - every sanction regime with its maximum or formula.
   Each item gets category (`threshold`, `key_date`, `sanction`), provision, a verbatim quote and the value. Write
   it to `validation/<run>/reference/<id>.json` before opening the blinded files. For very long texts, the checker
   may restrict the reference list to the operative articles and record that restriction.
2. **Judge every item of X and Y** in thresholds, events, obligations and enforcement against the text and
   `docs/CODEBOOK.md`. If a section has more than 40 items, judge a systematic sample (every k-th item from index
   0, k = ceil(n/40)) and record k. Each judged item gets exactly one verdict:

   | Verdict | Meaning |
   |---|---|
   | `correct` | supported by the text and coded per the codebook (minor wording differences are fine) |
   | `wrong_value` | wrong number, unit, operator, amount or variable |
   | `wrong_type` | wrong obligation type, event type, sanction type or threshold trigger |
   | `wrong_addressee` | wrong addressee role |
   | `wrong_date` | wrong date, or a computed date with the wrong anchor, direction or arithmetic |
   | `unsupported` | not in the text, or not an item of this kind (e.g. a penalty coded as an obligation) |
   | `duplicate` | repeats another item of the same version |
   | `not_this_instrument` | belongs to another act (an EU directive's deadline, a neighbouring rule) |

   When several apply, use the first in this order: `not_this_instrument`, `unsupported`, `duplicate`,
   `wrong_value`, `wrong_date`, `wrong_type`, `wrong_addressee`.
3. **Match the reference list** to each version: for every reference item, the index of the X item and of the Y
   item that records it (or null).
4. **Classification:** for each version, domains `right` / `partly` / `wrong`, primary domain `right` / `wrong`.
5. The checker does not edit annotations during a validation run. Corrections, if any, are a separate step.

## 3. Record format (`validation/<run>/verdicts/<id>.json`)

```json
{"instrument_id": "...", "checker": "claude-opus-5-5 (Claude Code)", "protocol": "VP-1.0", "date": "YYYY-MM-DD",
 "reference": [{"category": "threshold|key_date|sanction", "provision": "...", "quote": "...", "value": "...",
                "match": {"X": 3, "Y": null}}],
 "judgments": {"X": {"thresholds": [{"index": 0, "verdict": "correct", "note": ""}], "events": [], "obligations": [],
                     "enforcement": []},
               "Y": {...}},
 "sampling": {"X": {"obligations": 2}, "Y": {}},
 "classification": {"X": {"domains": "right", "primary_domain": "right", "note": ""}, "Y": {...}},
 "reference_scope": "full text | operative articles only",
 "notes": "..."}
```

## 4. Metrics (`tools/validation.py report`)

- **Precision** per section and version: correct / judged, items pooled across laws, 95% Wilson interval.
- **Recall** per category and version: reference items matched / reference items.
- **Error profile:** share of each verdict.
- **Paired difference** by law (new − old precision), with counts of laws better, worse, about equal (±2 points).
- The report is written to `validation/<run>/report.md` and the key is copied next to it.

## 5. Limitations

- The checker is a model. Opus is stronger than Haiku, but its judgments are not ground truth; agreement between
  two models can share blind spots. A human review of a subsample of the checker's verdicts is the next level.
- Items are pooled across laws, so intervals understate uncertainty when errors cluster by law; the paired
  per-law comparison is the more robust signal.
- Systematic sampling within long sections and restricted reference lists on very long texts are recorded per law.
- Blinding removes metadata, but stylistic differences between versions could still be recognisable.
