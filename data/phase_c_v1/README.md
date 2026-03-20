# Phase C v1

このディレクトリの成果物は **sentence-transformers backend で再採点した値に統一** する。

## 正式成果物

- CSV: `data/phase_c_v1/phase_c_results_v1.csv`
- 互換リンク: `data/phase_c_v1/phase_c_v1_results.csv`
- HTMLレポート: `data/phase_c_v1/phase_c_report_v1.html`
- スコア済みJSONL: `data/phase_c_v1/phase_c_scored_v1.jsonl`
- calibration log: `data/phase_c_v1/calibration_log.md`

## 方針

- STを使っていない採点値は v1 の正式値として扱わない
- v1 の参照先は原則として `phase_c_results_v1.csv` に統一する
- 旧名称 `phase_c_v1_results.csv` は互換用シンボリックリンクとして残す
