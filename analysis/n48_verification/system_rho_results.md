# system rho recalc -- 190/310 baseline

## Baseline

- File: `audit_102_main_baseline_safe_relaxed.csv`
- Hits: 190/310 (0.6129)
- Change: fr=0.30 (full_recall 0.35 -> 0.30)

## system_hit_rate changes

| qid | subgroup | old | new | delta |
|-----|----------|-----|-----|-------|
| q064 | HA28 | 0.3333 | 0.0000 | -0.3333 |
| q069 | HA20 | 0.3333 | 0.6667 | +0.3334 |
| q074 | HA28 | 1.0000 | 0.6667 | -0.3333 |
| q080 | HA20 | 1.0000 | 0.6667 | -0.3333 |
| q081 | HA28 | 1.0000 | 0.6667 | -0.3333 |
| q083 | HA20 | 0.6667 | 1.0000 | +0.3333 |

## delta_e_a system rho

| metric | prev (189/310) | now (190/310) | delta |
|--------|----------------|---------------|-------|
| system rho | 0.484 | **0.4852** | +0.0012 |
| system p | - | 0.0005 | - |
| reference rho | 0.857 | **0.8568** | -0.0002 |
| reference p | - | 0.0000 | - |
| gap (ref - sys) | 0.373 | 0.3716 | -0.0014 |

## Subgroup system rho

| group | n | sys rho | p | ref rho | p |
|-------|---|---------|---|---------|---|
| HA20 | 20 | 0.8113 | 0.0000 | 0.9266 | 0.0000 |
| HA28 | 28 | 0.2851 | 0.1414 | 0.7938 | 0.0000 |
| **ALL** | **48** | **0.4852** | **0.0005** | **0.8568** | **0.0000** |

## FP/FN (best case)

| metric | prev (189/310) | now (190/310) | delta |
|--------|----------------|---------------|-------|
| FP | 26 | 25 | -1 |
| FN | 8 | 9 | +1 |
| Precision | 0.732 | 0.7368 | +0.0048 |
| Recall | 0.899 | 0.8861 | -0.0129 |

### Subgroup FP/FN

| group | TP | FP | FN | TN | Precision | Recall |
|-------|----|----|----|----|-----------| -------|
| HA20 | 32 | 10 | 3 | 15 | 0.7619 | 0.9143 |
| HA28 | 38 | 15 | 6 | 27 | 0.7170 | 0.8636 |
| **ALL** | **70** | **25** | **9** | **42** | **0.7368** | **0.8861** |

### New FP: q069

## Verdict

| criterion | threshold | result | verdict |
|-----------|-----------|--------|---------|
| system rho | >= 0.50 | 0.4852 | FAIL |
| FP increase | <= 1 | -1 | **PASS** |
| reference rho | unchanged (~0.857) | 0.8568 | **PASS** |

**Conclusion: system rho < 0.50 but improved. Continue C improvement.**
