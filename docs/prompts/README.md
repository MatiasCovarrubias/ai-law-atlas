# Prompt versions

Every machine annotation records `annotator.prompt_version`. A version `1.3-<hash>` is the first 8 hex digits of
SHA-256 over the exact instructions, per-section guidance, codebook and call groups (`annotate.prompt_version`);
the prompt is archived here as `<version>.json` (codebook by hash; it is unchanged since commit 1f29928).

| Version | Used for | Notes |
|---|---|---|
| `1.3-718f7c3f` | Tier A rollout waves 0–2; after the 14 Oct re-run only the 25 Opus-corrected files remain | Reconstructed exactly from git 7f426bd. Before any spot-check finding. |
| `1.3-interim-20261014` | Tier B (362); the 68 Tier A extras were re-run with 030c54d5 | Not committed at launch, so no exact archive. It is 718f7c3f plus the guidance written after the first spot-check round: dates printed in the text are absolute; `anchor_event` only for this instrument's events, conditional with `depends_on` otherwise; negative offsets for "before"; other acts' dates are not events; `enforcement_order` only for individual measures; thresholds set later are rows with the delegation in `value_text`; public bodies' duties are `government_duty`; infringement lists, no-duty statements and liability rules are not obligations. |
| `1.3-030c54d5` | **Frozen.** All other Tier A machine annotations (357), after the VP-1.0 validation | Interim plus: penalties and rights to sue only in enforcement; amending acts only new text; one row per deadline; most specific addressee role, `enterprise` only with a size test; staged start dates; `ai_sectoral` only for AI use in a sector; overview of the whole instrument. |
| `gold (...)` | 26 gold annotations | Hand annotation by Claude Opus in Claude Code, or the codebook's worked examples; no prompt. |

Post-processing (date rules, `fix_bookkeeping`) is code, not prompt, and was re-applied to every machine file with
`tools/refix.py`, so all vintages share the current deterministic rules.
