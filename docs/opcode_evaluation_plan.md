# repair opcode 評価プロトコル (plan)

本リポジトリは `opcodes/runtime_repair_opcodes.yaml` に 13 の修復 opcode
を定義している。`decider.py` は `rewrite` / `regenerate` verdict 時に
検出された問題 (f1-f4, coverage) に応じた opcode 列 (`repair_order`) を
生成する。

**本論文 (UGHer) の scope:**

- opcode は **verdict 分類の副産物** として提示される
- 各 opcode の有効性（実際に apply すると回答が改善するか）の評価は
  **本論文の scope 外、別論文の射程**
- 現状 opcode は repair の "recipe" として公開されているが、apply 後の
  before/after での O 改善量は未測定

## 未評価の論点

1. **apply 効率性**: 各 opcode が指示する修復を LLM / 人手で実施した際、
   O スコアが実際に上昇するか
2. **opcode-fault 対応の妥当性**: 検出された fault (f1-f4) と生成される
   opcode 列の対応が最適か
3. **コスト表の校正**: 各 opcode の `cost` 値は暫定設定、実測校正は未実施
4. **13 個の network 依存性**: 複数 opcode が同時生成された際の相互作用
5. **opcode 適用後の reproducibility**: 同じ fault から決定的に同じ
   opcode 列が生成されるかの完全検証

## 将来 protocol (plan, 未着手)

v2 評価では以下を計画:

1. HA48 / HA100+ の `rewrite` / `regenerate` 事例に対し、human annotator
   が opcode 列を 2 手に分類 (apply-feasible / infeasible)
2. apply-feasible 事例に対し LLM で opcode を 1 つずつ適用し、
   修正後回答の O を再評価
3. opcode 単体の ΔO を算出、cost 表を実測値で校正
4. opcode-fault 対応規則の confusion matrix を出力

評価データ規模見積: 30 fault cases × 平均 2 opcode = 60 apply trial。
人手 + LLM コストで 10 時間程度。

## 現状で公開するもの

- opcode 定義 (`opcodes/runtime_repair_opcodes.yaml`)
- decider が生成する `repair_order` API 出力
- 各 opcode の target (f1/f2/f3/f4/coverage) の categorical 対応

現状で**主張しないこと:**

- opcode を適用すると回答が改善する（未検証）
- 13 個の opcode 集合が包括的 / 最小である（未検証）
- `cost` 値が正しく校正されている（暫定値）

## 関連ファイル

- `opcodes/runtime_repair_opcodes.yaml` — 13 opcode + cost 表
- `decider.py` — `rewrite` / `regenerate` 時に `repair_order` 生成
- `docs/validation.md` — ΔE / L_sem 評価 (opcode は含まない)

## 扱いの明示

「UGHer 論文」で opcode を言及する際は:

- 「verdict 分類に付随する修復 recipe」としてのみ説明
- 「recipe の apply 有効性は scope 外、別論文で扱う」と明記
- 「13 個の opcode は暫定 taxonomy」と限定

この整理により、opcode 未評価が本論文の論点に影響しない設計的に独立な
下流機能であることが査読で明確になる。
