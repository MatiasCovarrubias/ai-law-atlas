# Validation report: 2026-10-paired-B

Protocol `docs/VALIDATION_PROTOCOL.md`. Sample: 10 laws (Tier B machine annotations with prompt_version 1.3-interim-20261014, not previously spot-checked; frame 362; seed 20261015). Versions: old = `1.3-interim-20261014`, new = `1.3-030c54d5`. Checker judgments were blind to version. Missing verdicts: none.

## Precision (items judged correct; 95% Wilson interval, items pooled across laws)

| Section | old | new |
|---|---|---|
| thresholds | 72% [57–83] (n=43) | 87% [73–94] (n=38) |
| events | 91% [83–96] (n=82) | 94% [86–97] (n=77) |
| obligations | – (n=0) | – (n=0) |
| enforcement | – (n=0) | – (n=0) |
| all four | 85% [77–90] | 91% [85–95] |

## Recall against the checker's independent reference list

| Category | old | new |
|---|---|---|
| key_date | 100% [91–100] (n=38) | 100% [91–100] |
| threshold | 84% [70–93] (n=38) | 82% [67–91] |
| all | 92% [84–96] | 91% [82–95] |

## Error types (share of judged items)

| Verdict | old | new |
|---|---|---|
| wrong_value | 0 (0%) | 0 (0%) |
| wrong_type | 3 (2%) | 2 (2%) |
| wrong_addressee | 0 (0%) | 0 (0%) |
| wrong_date | 1 (1%) | 2 (2%) |
| unsupported | 15 (12%) | 5 (4%) |
| duplicate | 0 (0%) | 1 (1%) |
| not_this_instrument | 0 (0%) | 0 (0%) |

## Paired comparison by law

Precision difference new − old, per law: mean +0.2 points; new better on 3, worse on 2, about equal on 5 of 10 laws.

| id | old correct/judged | new correct/judged |
|---|---|---|
| es-prd-datacenters-efficiency-2025 | 2/2 | 2/2 |
| eu-dsm-directive-2019-790 | 20/23 | 17/21 |
| il-privacy-amendment13-2024 | 42/52 | 45/46 |
| mx-dip-lgamvlv-20quater-ia-2026 | 5/6 | 3/6 |
| sg-eliona-2024 | 2/6 | 2/4 |
| us-az-hb2311-2026 | 4/4 | 4/4 |
| us-ca-ab1064-2025 | 12/13 | 12/12 |
| us-ca-ab2355-2024 | 0/0 | 0/0 |
| us-nj-s3379-a4096-2026 | 11/11 | 11/11 |
| us-s2528-119 | 8/8 | 9/9 |

## Classification (domains judged right / partly / wrong)

old: {'right': 7, 'partly': 3}; new: {'right': 9, 'partly': 1}
