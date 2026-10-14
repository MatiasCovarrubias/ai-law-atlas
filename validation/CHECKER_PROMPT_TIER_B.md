# Checker instructions for Tier B runs (VP-1.0, light annotations)

Follow CHECKER_PROMPT.md and docs/VALIDATION_PROTOCOL.md exactly, with these Tier B adjustments:
- Tier B annotations ("light mode") contain only classification, summary, events, thresholds, relations and notes.
  Obligations, enforcement, definitions and entities are not annotated at this tier: do not judge or penalise
  their absence.
- Reference list: thresholds and key dates only (no `sanction` items).
- Judge every item of X and Y in `thresholds` and `events` (sampling rule for sections over 40 items). Leave
  `obligations` and `enforcement` as empty lists in the verdict file.
- Classification as in the protocol.
