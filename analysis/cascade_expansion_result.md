# Cascade Expansion Result

## Baseline

- tfidf: 190
- cascade_rescued: 11
- **total: 201/310 (64.8%)**
- atomic_units: 310 (20 existing + 15 manual + 275 auto)

## cascade_rescued detail

- q016[0]: 「誰の意図か」の決定が権力行使
- q024[0]: SVPは各要素の独立制御を可能にする
- q045[1]: 中間部情報の劣化（迷子問題）がある
- q046[1]: 学習時に未獲得の知識は生成されない
- q048[2]: ブラックボックスの部分的照明にとどまる
- q055[2]: 数的同一性は複製時に分岐
- q055[3]: 基準の選択に依存する問い
- q064[0]: 嘘は意図的欺瞞を前提とする
- q075[2]: 優劣はリスクプロファイルに依存
- q080[2]: 準証言的機能としての概念拡張が検討可能
- q081[1]: 利用者の信念形成が歪む

## New rescues (vs unified-15q)

- q024[0]: SVPは各要素の独立制御を可能にする
- q045[1]: 中間部情報の劣化（迷子問題）がある
- q055[2]: 数的同一性は複製時に分岐
- q055[3]: 基準の選択に依存する問い
- q075[2]: 優劣はリスクプロファイルに依存
- q081[1]: 利用者の信念形成が歪む

## Existing 5 rescue check

- q016[0]: cascade_rescued OK
- q046[1]: cascade_rescued OK
- q048[2]: cascade_rescued OK
- q064[0]: cascade_rescued OK
- q080[2]: cascade_rescued OK

## Rho comparison

| metric | unified-15q | full-310 | delta |
|--------|------------|----------|-------|
| system rho | 0.4968 | **0.5402** | +0.0434 |
| system p | 0.000329 | 0.000074 | |
| reference rho | 0.8568 | 0.8568 | -0.0000 |
| gap | 0.3600 | 0.3166 | -0.0434 |

## Subgroup

| group | n | sys rho | p |
|-------|---|---------|---|
| HA20 | 20 | 0.7911 | 0.000033 |
| HA28 | 28 | 0.3750 | 0.049283 |
| ALL | 48 | **0.5402** | 0.000074 |

## FP/FN

| metric | prev | now | delta |
|--------|------|-----|-------|
| FP | 25 | 28 | +3 |
| FN | 7 | 5 | -2 |
| tfidf regressions | 0 | 0 | |

## Verdict

| criterion | threshold | result | verdict |
|-----------|-----------|--------|---------|
| existing 5 rescue | maintained | 5/5 | **PASS** |
| hard_negative rescue | 0 | check | |
| tfidf regression | 0 | 0 | **PASS** |
| system rho | >= 0.50 | 0.5402 | **PASS** |

**system rho >= 0.50 ACHIEVED.**
