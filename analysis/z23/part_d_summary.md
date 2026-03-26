# Z_23 演算子抽出 判定サマリー

## 1. 発見

- Z_23 全23問を処理完了
- question演算子あり: 15/23問
- 総演算子抽出数: 40件（surface + implicit）
- 演算子なし記録: 65件（命題レベル含む）
- 上位族: binary_frame(12), limiter_suffix(8), universal(6), conditional(4), comparative(3)

### 主要パターン
- binary_frame族: 「〜か？」形式の二項疑問が多数。Z_23の質問構造に頻出
- limiter_suffix族: 「にすぎない」「ではない」等の否定限定が命題内に多い
- universal族: 「常に」「本質的に」「最も」等の全称表現が質問・命題両方に分布
- conditional族: 「場合」「条件」等が命題内の射程規定に使用

## 2. 新族提案

### NEW:deontic
- **理由**: 既存10族のいずれにも該当しない当為（〜べき）表現を捕捉する必要がある
- **代表語**: べき（当為）
- **既存族との差異**: universal（事実の全称）やconditional（条件）とは異なり、規範的判断（当為・義務）を規定する。論理構造を「事実→当為」に変換する機能語
- **境界ケースの線引き**:
  - **skeptical_modality との重複**: 「本当にXすべきか」→ skeptical_modality（「本当に」）+ deontic（「べき」）の**複合タグ**として処理。判定優先度は skeptical_modality > deontic（懐疑が当為の射程を変更するため、懐疑側を primary_family とする）
  - **binary_frame との重複**: 「すべきか否か」→ binary_frame（「か否か」）+ deontic（「べき」）の**複合タグ**。判定優先度は binary_frame > deontic（二項対立構造が文の論理骨格を規定するため、binary_frame を primary_family とする）
  - **判定ルール**: 「べき」単独出現 → deontic。他族の演算子と共起する場合 → 両方を operators[] に記録し、scope で射程を区別。detector.py 組み込み時は primary_family で分岐する

## 3. リスク

### 正規化で落ちる情報
- **修辞的ニュアンス**: 「本当に〜か？」の懐疑的トーンは family=skeptical_modality に分類されるが、強度（軽い疑問 vs 根本的疑念）の区別は失われる
- **暗黙の対比構造**: 「AかBか」の二項対立は binary_frame で捕捉されるが、「AでもBでもない第三の選択肢」への含意は scope に依存
- **文脈依存の否定**: 「十分条件ではない」は limiter_suffix に分類されるが、元の命題における「十分条件」の射程（何に対して十分でないか）が scope 枠に圧縮される
- **scope 空欄率**: 0.0%（閾値10%以内）

### ρ非破壊の確認
- 本分析は Hard-C 側（命題マッチング精度）の改善のみを対象
- ρ（PoR相関）に影響する soft-score 計算パスには変更なし
- 演算子カタログ拡張は detector.py の f3_operator 判定のみに影響

## 4. 次工程推奨

### 優先度: 演算子枠 > 主語・述語枠
- **根拠**: Z_23の主な不一致原因は、回答が命題の論理構造（否定・限定・条件）を捕捉していないケースが多い。主語・述語の語彙一致は synonym expansion で一定程度カバー済み（21→16件に改善）

### 推奨アクション
1. **operator_catalog.yaml 更新**: 本分析で特定した surface_patterns を追加
2. **cascade matcher 実装**: 演算子一致 → 主語一致 → 述語一致の段階的マッチング
3. **z-gate 導入**: 演算子不一致時の自動修復パス（operator_required_action 参照）
4. **X_7 / Y_6 分析**: 構造不一致・前提不一致の残り13件を別途処理

### 期待効果
- 演算子正規化により Z_23 の23問中、推定15〜18問の命題ヒット率改善
- 命題ヒット率: 48.1% → 推定 58〜62%（cascade matcher 込みで 65% 到達見込み）
