# Unified Rerun Results -- fr=0.30 + cascade

## Baseline

- File: `audit_102_main_baseline_unified.csv`
- tfidf: 190
- cascade_rescued: 5
- total: 195/310 (62.9%)
- miss: 115

## cascade_rescued detail

- q016[0]: 「誰の意図か」の決定が権力行使
- q046[1]: 学習時に未獲得の知識は生成されない
- q048[2]: ブラックボックスの部分的照明にとどまる
- q064[0]: 嘘は意図的欺瞞を前提とする
- q080[2]: 準証言的機能としての概念拡張が検討可能

## system_hit_rate changes (v2 -> v4)

| qid | subgroup | old | new | delta |
|-----|----------|-----|-----|-------|
| q069 | HA20 | 0.3333 | 0.6667 | +0.3334 |
| q074 | HA28 | 1.0000 | 0.6667 | -0.3333 |
| q081 | HA28 | 1.0000 | 0.6667 | -0.3333 |
| q083 | HA20 | 0.6667 | 1.0000 | +0.3333 |

## delta_e_a rho

| metric | prev (v3) | unified (v4) | delta |
|--------|-----------|--------------|-------|
| system rho | 0.4852 | **0.4968** | +0.0116 |
| system p | 0.0005 | 0.000329 | |
| reference rho | 0.8568 | **0.8568** | -0.0000 |
| gap (ref-sys) | 0.3716 | 0.3600 | -0.0116 |

## Subgroup rho

| group | n | sys rho | p | ref rho | p |
|-------|---|---------|---|---------|---|
| HA20 | 20 | 0.8116 | 0.000014 | 0.9266 | 0.000000 |
| HA28 | 28 | 0.2890 | 0.135770 | 0.7938 | 0.000000 |
| **ALL** | **48** | **0.4968** | **0.000329** | **0.8568** | **0.000000** |

## FP/FN

| metric | prev | unified | delta |
|--------|------|---------|-------|
| FP | 25 | 25 | +0 |
| FN | 9 | 7 | -2 |
| Prec | 0.7368 | 0.7423 | +0.0055 |
| Rec | 0.8861 | 0.9114 | +0.0253 |

## 4-question resolution

| qid | cascade(old) | unified | resolved? |
|-----|-------------|---------|-----------|
| q064 | 1/3 | 1/3 | YES |
| q074 | 3/3 | 2/3 | NO (-1) |
| q080 | 3/3 | 3/3 | YES |
| q081 | 3/3 | 2/3 | NO (-1) |

## Verdict

| criterion | threshold | result | verdict |
|-----------|-----------|--------|---------|
| 4q resolved | all 4 | 2/4 | FAIL |
| tfidf hits | >= 190 | 190 | **PASS** |
| cascade rescue | >= 5 | 5 | **PASS** |
| system rho | >= 0.50 | 0.4968 | FAIL |
| ref rho | ~0.857 | 0.8568 | **PASS** |

**CONDITIONAL: partial resolution + rho improved. Baseline confirmed, continue C improvement.**
