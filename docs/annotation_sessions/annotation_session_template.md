# Annotation Session Template (Reusable)

このテンプレは、`analysis/annotation_ui.py` を使った対話式アノテーションを毎回同じ品質で再実行するための標準手順。

## 1. Preflight
```powershell
cd C:\path\to\ugh-audit-core-lazyload
python -m pip install -e ".[analysis,dev]"
python analysis/annotation_sampler.py --batch-size 15
```

## 2. Progress File (Windows 推奨)
```powershell
$env:UGH_ANNOTATION_PROGRESS_PATH="C:\path\to\ugh-audit-core-lazyload\data\human_annotation_accept40\annotation_progress.json"
```

## 3. Per-item Loop
1. 問題表示:
```powershell
python analysis/annotation_ui.py --step-next --resume
```
2. 必ず提示する内容（固定）:
- 質問
- かんたん解説
- core_propositions
- AI回答（原文）
- AI回答要約（判定で使う要点のみ）
- 評価基準テンプレ（Q1/Q2/O/comment）
- 下書き判定（Q1, Q2, O, comment）

3. 人手が確定値を入力（例: `a n 5`）。
4. 保存:
```powershell
python analysis/annotation_ui.py --step-annotate --resume --item-id <ID> --q1 <a|b|c> --q2 <y|n> --comment-key <1-6>
```

## 4. Comment Key
- `1`: 命題カバー不足
- `2`: 方向違い / 主題逸脱
- `3`: 誤情報含む
- `4`: 冗長だが核心あり
- `5`: 完璧
- `6`: カスタム

`comment-key=6` の場合:
```powershell
python analysis/annotation_ui.py --step-annotate --resume --item-id <ID> --q1 <a|b|c> --q2 <y|n> --comment-key 6 --comment-detail "<自由記述>"
```

## 5. End-of-batch Checks
```powershell
python -X utf8 analysis/annotation_blind_check.py
python analysis/run_incremental_calibration.py
```

判定の目安:
- blind check: `|Δ|平均 <= 1.0` かつ `|bias| <= 0.5`
- calibration: `accept subset n >= 28` の後、`fire_rate ∈ [0.10, 0.30]` 候補の有無を確認

## 6. Persistable Record (for Git)
`annotation_accept40.csv` は通常 `.gitignore` のため、毎回 snapshot を残す:

```text
data/human_annotation_accept40/snapshots/YYYY-MM-DD_<session_name>_snapshot.csv
docs/annotation_sessions/YYYY-MM-DD_<session_name>.md
```

記録 md に最低限入れる項目:
- scope（件数、blind件数）
- O分布
- blind check 結果
- calibration 結果
- snapshot と result ファイルのリンク

## 7. PR Ready Checklist
- [ ] snapshot CSV を追加
- [ ] セッション記録 md を追加
- [ ] テンプレ更新（必要時）
- [ ] `python -X utf8 analysis/annotation_blind_check.py` 実行済み
- [ ] `python analysis/run_incremental_calibration.py` 実行済み
