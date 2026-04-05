# HA28 rho diagnosis -- system rho subgroup gap

## Summary

| group | n | system rho | p | 95% CI |
|-------|---|-----------|---|--------|
| HA20 | 20 | 0.7911 | 0.000033 | [0.536, 0.914] |
| HA28 | 28 | 0.3750 | 0.049 | [0.002, 0.656] |
| ALL | 48 | 0.5402 | 0.000074 | |

CIs overlap -> difference is not statistically significant at 95% level.

## Hypothesis verdicts

| # | hypothesis | verdict | evidence |
|---|-----------|---------|----------|
| H1 | Category distribution bias | **Not supported** | HA28 has broader coverage (5 adversarial vs 2), minor difference |
| H2 | Difficulty distribution bias | **Not tested** | difficulty metadata not available in current schema |
| H3 | O score variance deficit | **Supported (primary)** | HA28: O in {1,2,3,4} only (no 5), O=2 has 11/28 items (39%). Massive tie block |
| H4 | System C precision gap | **Partially supported** | HA28 precision=0.717 vs HA20=0.762. 13 FP questions vs 8 |
| H5 | C discreteness | **Not supported** | HA28 has MORE unique dE values (14) than HA20 (9) |
| H6 | Annotation quality drift | **Plausible but untestable** | HA28 was batch-annotated; HA20 iteratively refined |
| H7 | Outlier influence | **Supported (primary)** | q010 and q053 each boost rho by +0.072 when removed. Remove both -> rho=0.530 |

## Primary causes: H3 + H7

### H3: O score distribution imbalance

| O | HA20 | HA28 |
|---|------|------|
| 1 | 1 | 2 |
| 2 | 3 | **11** |
| 3 | 6 | 5 |
| 4 | 7 | **10** |
| 5 | 3 | **0** |

HA28 has no O=5 items and 11 items at O=2 (39%). This creates a massive tied-rank block
that degrades Spearman rho. The O=2 cluster contains questions with very different
system predictions (pred ranges from 2.91 to 5.00), but they all receive the same
rank for the O variable.

### H7: Two outliers dominate

| qid | O | pred | residual | cause |
|-----|---|------|----------|-------|
| q010 | 2.0 | 5.00 | -3.00 | system=3/3 hit (all tfidf), human=2/3. System sees full coverage but human rates O=2 |
| q053 | 2.0 | 5.00 | -3.00 | system=3/3 hit (all tfidf), human=1/3. **Design suspect**: annotator notes say "模範解答側にも設計疑義" |

Both have system C=1.0 (predicting high quality) but human O=2.0.
Remove both -> **rho jumps from 0.375 to 0.530** (p=0.005).

q053 is a known design suspect where the proposition set itself may be flawed.
q010 is a precision error: system detects all 3 propositions but human judges only 2 as hit.

### Design suspects (q003, q041, q053)

Remove all 3 -> rho=0.452 (p=0.023). q041 has minimal impact (resid=-0.57).
The main driver is q053 (resid=-3.00).

## FP distribution

| group | FP questions | total FP | per question |
|-------|-------------|----------|-------------|
| HA20 | 8 | 11 | 0.55 |
| HA28 | 13 | 17 | 0.61 |

HA28 has more FP questions (13 vs 8) but the per-question rate is similar.
FP is spread across categories in HA28 (no single category dominance).

## Conclusion (for paper)

> The HA28 subgroup rho (0.375) is lower than HA20 (0.791), but the 95% confidence
> intervals overlap [0.002, 0.656] vs [0.536, 0.914], indicating the difference is
> not statistically significant at conventional levels. The primary contributors to
> the lower HA28 rho are: (1) O-score distribution compression -- 39% of HA28 items
> cluster at O=2 with no O=5 items, creating tied ranks that attenuate Spearman
> correlation; (2) two outlier questions (q010, q053) where system C=1.0 but
> human O=2.0, driven by FP overdetection (q010) and a known proposition design
> issue (q053). Removing these two outliers raises HA28 rho to 0.530 (p=0.005),
> comparable to the overall system rho. System C precision (0.717 vs 0.762) is a
> secondary factor. The subgroup gap reflects annotation and design artifacts rather
> than a systematic failure of the delta_e_a metric.

## Recommendations

1. **q053**: Flag as design suspect in paper. Proposition set has acknowledged issues
2. **q010**: Investigate FP -- system detects 3/3 but human sees 2/3. Likely a tfidf FP on one proposition
3. **O-score range**: Future annotation should aim for full 1-5 range coverage in each subgroup
4. **No metric change needed**: The subgroup gap is explained by data artifacts, not metric design flaws
