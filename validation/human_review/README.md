# Human review of the Opus checker (next validation level)

Purpose: measure how often a human expert agrees with the Claude Opus checker's item verdicts from the VP-1.0 runs
(`validation/2026-10-paired/`, `validation/2026-10-paired-B/`). This calibrates the model-on-model validation: if
humans agree with Opus on nearly all verdicts, the VP-1.0 figures can be reported as validated estimates.

**Sample:** 80 item verdicts drawn with seed 20261016: 40 the checker judged `correct` and 40 it judged an error
(errors are oversampled, so the sample is not representative of error rates; agreement is reported separately for
the two halves). `sample_key.json` maps each row to its run, law, version and item index.

**For each row** in `sample.csv`: read the machine item and its quote, open the text (`text_file`, search for the
quote), and decide whether the checker's verdict is right under `docs/CODEBOOK.md` and the verdict codes of
`docs/VALIDATION_PROTOCOL.md`. Fill `human_agrees_with_checker` (yes/no); if no, give your verdict and a short note.
About 1–2 minutes per row; 2–3 hours in total. The reviewer should know the codebook but need not be a lawyer for
every jurisdiction (quotes are in the original language).

**Report:** agreement on `correct` verdicts (checker false negatives) and on error verdicts (checker false
positives), with 95% intervals; disagreements by section and verdict code.
