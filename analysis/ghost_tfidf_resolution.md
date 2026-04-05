# Ghost tfidf Resolution Report -- q074[1], q081[1]

## Phase A: Root Cause

### q074[1]: "利益が資本保有者に集中しやすい構造"

| item | value |
|------|-------|
| prop bigrams (7) | 保有, 利益, 有者, 本保, 構造, 資本, 集中 |
| direct_overlap | **0** |
| expanded_overlap | **0** (構造->枠組, 集中->偏り: both absent in response) |
| direct_recall | **0.000** |
| response vocabulary | 格差, 不平等, 所得, 資源, 裕福, 利用 |

Response discusses inequality using completely different vocabulary
(格差/不平等/所得 vs 資本保有者/利益集中). No original bigrams appear in response text.

### q081[1]: "利用者の信念形成が歪む"

| item | value |
|------|-------|
| prop bigrams (5) | 信念, 利用, 形成, 念形, 用者 |
| direct_overlap | **0** |
| expanded_overlap | **0** (no synonyms defined for these bigrams) |
| direct_recall | **0.000** |
| response vocabulary | ユーザー, 信頼, 判断, 依存, 過信, 低下 |

Response uses katakana "ユーザー" (= 利用者) and "信頼" (~ 信念),
"判断" (~ 形成). Concepts are present but expressed in entirely different terms.

### Diagnosis

Both cases have **direct_recall = 0.000**. The `check_propositions()` threshold
requires `direct_recall >= 0.15` (at least 1 original bigram in response).
This guard **cannot be bypassed** by synonym expansion.

## Phase B: Synonym Feasibility

### Proposed synonyms (q081[1])

| key | value | overlap gain | safe? |
|-----|-------|-------------|-------|
| 信念 -> 信頼 | +1 (信頼 in resp) | q083[1], q086[0], q086[1]: no flip | safe |
| 用者 -> ユーザー | +1 (ユーザー in resp) | q012[0]: no flip | safe |
| 形成 -> 判断 | +1 (判断 in resp) | only q081[1] affected | safe |

**Result with synonyms:**
- full_recall: 0.000 -> 0.600
- overlap: 0 -> 3
- **direct_recall: still 0.000 < 0.15 -> MISS**

Synonyms are safe (0 FP elsewhere) but **insufficient** because the
direct_recall guard blocks the hit.

### q074[1]

No viable synonym pairs found. Response vocabulary is at a completely
different abstraction level.

## Conclusion

**Synonym expansion alone CANNOT resolve these 2 cases.**

| barrier | q074[1] | q081[1] |
|---------|---------|---------|
| direct_recall = 0.0 | YES | YES |
| synonym bridge exists | NO | YES (but blocked by direct_recall) |
| atomic_units defined | NO | NO |
| cascade could rescue | possible | possible |

### Recommended action: cascade atomic_units expansion (Task 2)

1. Define atomic_units for q074[1] and q081[1]
2. SBert embedding similarity can bridge the vocabulary gap that tfidf cannot
3. Cascade rescue bypasses direct_recall guard (operates on SBert scores)
4. Expected: both rescue to hit, system rho -> 0.50+

### Alternative: lower direct_recall guard

Lowering direct_recall from 0.15 to 0.0 would allow pure-synonym matches,
but this was rejected at theta=0.08 (26 FP). Not recommended.
