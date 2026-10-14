# Validation report: 2026-10-paired

Protocol `docs/VALIDATION_PROTOCOL.md`. Sample: 15 laws (Tier A machine annotations with prompt_version 1.3-718f7c3f, not previously spot-checked; frame 280; seed 20261014). Versions: old = `1.3-718f7c3f`, new = `1.3-030c54d5`. Checker judgments were blind to version. Missing verdicts: none.

## Precision (items judged correct; 95% Wilson interval, items pooled across laws)

| Section | old | new |
|---|---|---|
| thresholds | 92% [86–95] (n=122) | 94% [89–97] (n=124) |
| events | 92% [88–95] (n=204) | 97% [93–99] (n=191) |
| obligations | 84% [79–88] (n=311) | 93% [89–95] (n=269) |
| enforcement | 88% [80–93] (n=98) | 87% [79–92] (n=105) |
| all four | 88% [85–90] | 93% [91–95] |

## Recall against the checker's independent reference list

| Category | old | new |
|---|---|---|
| key_date | 85% [77–90] (n=124) | 95% [90–98] |
| sanction | 100% [95–100] (n=70) | 100% [95–100] |
| threshold | 82% [75–88] (n=131) | 94% [88–97] |
| all | 87% [83–90] | 96% [93–97] |

## Error types (share of judged items)

| Verdict | old | new |
|---|---|---|
| wrong_value | 3 (0%) | 1 (0%) |
| wrong_type | 4 (1%) | 6 (1%) |
| wrong_addressee | 2 (0%) | 4 (1%) |
| wrong_date | 8 (1%) | 2 (0%) |
| unsupported | 49 (7%) | 10 (1%) |
| duplicate | 19 (3%) | 21 (3%) |
| not_this_instrument | 3 (0%) | 3 (0%) |

## Paired comparison by law

Precision difference new − old, per law: mean +3.3 points; new better on 9, worse on 3, about equal on 3 of 15 laws.

| id | old correct/judged | new correct/judged |
|---|---|---|
| cl-ley21459-2022 | 38/42 | 32/38 |
| cn-sh-pudong-driverless-icv-provisions-2022 | 58/62 | 61/63 |
| eu-dma-gatekeeper-designations | 4/4 | 4/4 |
| eu-ehds-2025 | 79/87 | 85/88 |
| mx-lmtr-2025 | 116/129 | 120/136 |
| ng-ndpa-2023 | 43/50 | 43/48 |
| tr-law7253-2020 | 39/46 | 38/39 |
| uk-nsi-notifiable-acquisition-regs-2021 | 38/58 | 32/32 |
| us-co-hb24-1130 | 25/27 | 23/24 |
| us-hi-sb3001-2026 | 13/15 | 12/14 |
| us-id-hb575-2024 | 5/5 | 4/5 |
| us-ny-nyc-rules-amendment-automated-employment-decisionmaking-2022 | 15/17 | 19/21 |
| us-obbba-2025 | 51/64 | 61/64 |
| us-wa-mhmda-2023 | 39/40 | 41/45 |
| uy-ley15921-1987 | 84/89 | 67/68 |

## Classification (domains judged right / partly / wrong)

old: {'right': 13, 'partly': 2}; new: {'right': 13, 'partly': 2}
