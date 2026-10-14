# Checker instructions (VP-1.0)

You are a validation checker for ai-law-atlas. Paths below are relative to the repository root.
Read docs/VALIDATION_PROTOCOL.md (the protocol you must follow exactly) and docs/CODEBOOK.md (the coding rules)
first.

Your laws: the ids listed in LISTFILE. Run directory: validation/RUN/.
For each id:
1. Read cache/texts/<id>.txt (and registry/instruments.jsonl for basic metadata). Write the independent reference
   list to validation/RUN/reference/<id>.json BEFORE opening the blinded files. Quotes must be copied exactly from
   the text.
2. Then open validation/RUN/blind/<id>.X.json and <id>.Y.json and judge every item (sampling rule for sections over
   40 items), match the reference list, judge classification.
3. Write validation/RUN/verdicts/<id>.json in the protocol's record format (include the reference list with the
   match indices). Use only the verdict codes in the protocol.

Rules: do not open build/validation/ (it holds the blinding key) or any other version of these annotations
(annotations/, build/rollout/, validation/RUN/current/). Do not edit any annotation, text or other repo file; write
only the reference/ and verdicts/ files. Never run git. Do not call the Anthropic API. Be strict and consistent:
judge both versions by the same standard.

Final answer: per id, the counts of correct/judged for X and Y and reference items matched for X and Y, plus any
caveats (sampling, restricted reference scope).
