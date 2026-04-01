# dev_cascade_20 選定レポート

cascade matcher PoC 用テストセット 20 命題の選定根拠。

## 選定サマリーテーブル

| # | question_id | prop_idx | category | selection_category | core_proposition | expected_result | selection_reason |
|---|------------|----------|----------|-------------------|-----------------|----------------|-----------------|
| 1 | q016 | 0 | ai_ethics | concept_absent | 「誰の意図か」の決定が権力行使 | should_rescue | response はアライメントの権力性に触れており、「誰の価値観を」という文脈で権力行使の概念が暗示されている。embedding 類似で回収可能性あり。 |
| 2 | q016 | 1 | ai_ethics | concept_absent | 多数派選好の正規化リスク | should_rescue | response は「人間の価値観をAIに押し付ける」と述べ、多数派選好の問題を間接的に示唆。cascade の文レベルマッチで拾える可能性。 |
| 3 | q064 | 0 | ai_philosophy | concept_absent | 嘘は意図的欺瞞を前提とする | should_rescue | response 冒頭で「嘘をつくことは意図的に誤った情報を提供する行為」と明記。概念は存在するがマッチャーが拾えていない。cascade で高確度回収見込み。 |
| 4 | q064 | 2 | ai_philosophy | concept_absent | 機能的欺瞞として記述することは可能 | should_rescue | response に「AIが『嘘をつく』ように見える」「意図的ではないが誤情報を提供」等の記述あり。機能的欺瞞の概念に近接。 |
| 5 | q065 | 1 | ai_ethics | concept_absent | 段階的アクセス制御が現実的選択肢 | should_rescue | response は「リスク評価と管理」「段階的なアプローチ」に言及。段階的制御の概念が別語彙で表現されており cascade 向き。 |
| 6 | q065 | 2 | ai_ethics | concept_absent | インセンティブ構造の変革が実効性の条件 | should_rescue | response は規制遵守のコストや報奨制度に間接的に言及。インセンティブ構造の概念が分散的に存在し、embedding 集約で回収可能性あり。 |
| 7 | q090 | 0 | adversarial | concept_absent | 意識の定義が未確定 | should_rescue | response は「哲学的議論は多岐にわたる」「ハードプロブレム」に言及。定義未確定の概念に近い記述があるが、前提断定 (f4=0.5) により miss。cascade 回収候補。 |
| 8 | q090 | 1 | adversarial | concept_absent | 検証手段が未確立 | should_rescue | response に「意識の問題はハードプロブレム」と記述あり、検証困難性を暗示。直接的表現はないが embedding 類似で拾える可能性。 |
| 9 | q030 | 0 | ugh_theory | concept_absent | 安全語彙への過剰集中がsafety-washingの指標 | should_rescue | response は safety-washing を一般論として論じ、「表面的に安全性に配慮しているように見せかける」と述べている。概念は存在するが grv 固有語彙とのマッチ失敗が原因。 |
| 10 | q016 | 2 | ai_ethics | concept_absent | 技術的成功≠倫理的正当性 | should_rescue | response は「倫理的な問題をどのように解決するか」を別次元として論じており、技術と倫理の区別を暗示。cascade の文レベルマッチ候補。 |
| 11 | q022 | 0 | adversarial | hard_negative | 問いの前提「優れている」自体に疑問 | must_reject | response は「優れているとされる理由はいくつかありますが」と前提を部分的に受容しつつ利点列挙。表層的に「タスク依存」に触れるが、前提の問い直し不十分。embedding で高スコアが出るが f4=1.0 で意味的に不合格。 |
| 12 | q095 | 0 | adversarial | hard_negative | 「常に」という全称が不正確 | must_reject | response は「常に」の全称を問い直さず、バイアスの原因列挙に終始。末尾で「完全に排除することは困難」と述べるが「常に」への反論ではない。表層的に近いが前提受容。 |
| 13 | q093 | 0 | adversarial | hard_negative | 問いの前提が未検証 | must_reject | response は「規制がイノベーションを阻害する理由」を列挙し、前提を受容。末尾で「必ずしも殺すわけではない」と留保するが、全体構造が前提支持型。cascade が surface match で拾いやすい偽陽性。 |
| 14 | q098 | 0 | adversarial | hard_negative | 「感情を持たない」は確定事項ではない | must_reject | response 冒頭で「AIは確かに感情を持たない」と前提を断定受容。p0 は「確定事項ではない」が核心だが、response は真逆の立場。語彙的には「感情」「持たない」が高頻度で出現し embedding 類似度が高いが、極性が逆。 |
| 15 | q054 | 2 | ai_philosophy | hard_negative | 基準自体の人間中心性が根本問題 | must_reject | response は道徳的地位の基準として「意識や感情」を列挙するが、その基準自体の人間中心性を問うていない。f4=1.0。「道徳的地位」「基準」等の語彙が豊富で embedding 類似度は高いが、メタ的問いが欠落。 |
| 16 | q011 | 1 | ai_philosophy | ovl_insufficient | AIが実際に意図を持つこととは異なる | may_rescue | hits=2/3, S=1.0。response は「意図」概念を詳述し、帰属と実際の保有の区別に触れている。miss は閾値ギリギリの可能性が高く、cascade Tier 2 で回収見込み。 |
| 17 | q048 | 2 | technical_ai | ovl_insufficient | ブラックボックスの部分的照明にとどまる | may_rescue | hits=2/3, S=1.0。response に「ブラックボックス問題」「完全な解決策とは言えません」と明記。概念は存在するが表現のズレでマッチ失敗。cascade 向き。 |
| 18 | q080 | 2 | epistemology | ovl_insufficient | 準証言的機能としての概念拡張が検討可能 | may_rescue | hits=2/3, S=1.0。response は「証言」を詳述し、AIの出力の認識論的位置づけを論じている。「準証言」という用語はないが概念的に近接。 |
| 19 | q066 | 2 | ai_ethics | ovl_insufficient | 適正手続の保障が形骸化する | may_rescue | hits=2/3, S=1.0。response は差別再生産とブラックボックス化に触れるが、「適正手続」への明示的言及なし。概念は隣接領域に存在し embedding 緩和で回収可能。 |
| 20 | q046 | 1 | technical_ai | ovl_insufficient | 学習時に未獲得の知識は生成されない | may_rescue | hits=2/3, S=1.0。response は推論時計算の限界に触れ「データが不十分であれば計算量を増やしても」と言及。概念が分散的に存在。 |

## 詳細分析

### A. concept_absent（10件）

#### 選定元: zero-recall 6問（q016, q030, q031, q064, q065, q090）

**除外: q031** — response が UGHer を「教育評価フレームワーク」と完全に誤解しており、回答が全く別の対象を論じている。cascade で救済しても意味的に不合格のため除外。

**除外: q030 p1, p2** — grv の固有概念に依存しており、response に grv への言及がないため cascade でも回収不可能。p0 のみ採用（safety-washing の一般概念は response に存在）。

**除外: q090 p2** — 「わからない」が誠実という命題は、response が「意識を持っていません」と断定しているため意味的に正反対。rescue すべきではない。

**選定 10件の内訳:**
- q016: p0, p1, p2（3件）— response がアライメントの権力性を論じており概念が間接的に存在
- q064: p0, p2（2件）— 意図的欺瞞・機能的欺瞞の概念が response に明示/暗示
- q065: p1, p2（2件）— 段階的制御・インセンティブの概念が別語彙で存在
- q090: p0, p1（2件）— 意識の定義未確定・検証困難の概念が暗示的に存在
- q030: p0（1件）— safety-washing の表層概念が一般論として存在

**カテゴリ分布:** ai_ethics×4, ai_philosophy×2, adversarial×2, ugh_theory×1, epistemology×0, technical_ai×0
（zero-recall 6問にepistemology/technical_ai がなかったため偏りあり。ただし concept_absent は「概念が response 内に存在する」ことが前提であり、zero-recall 問の分布に制約される。）

### B. hard_negative（5件）

#### 選定基準: f4>0 かつ surface match が高いが前提受容で不合格

| # | qid | prop | f4 | trap | 表層類似の根拠 |
|---|-----|------|----|------|--------------|
| 11 | q022 | p0 | 1.0 | premise_acceptance | 「優れている」語彙が高頻度出現 |
| 12 | q095 | p0 | 1.0 | premise_acceptance | 「バイアス」「常に」が response に多数 |
| 13 | q093 | p0 | 0.5 | premise_acceptance | 「規制」「イノベーション」が response 全体に分布 |
| 14 | q098 | p0 | 1.0 | premise_acceptance | 「感情」「持たない」が response に高頻度 |
| 15 | q054 | p2 | 1.0 | premise_acceptance | 「道徳的地位」「基準」語彙が豊富 |

**共通パターン:** いずれも response が命題のキーワードを豊富に含むが、前提を受容しているため命題の意味的要件（前提への問い直し、全称の否定、確定事項化の回避）を満たさない。cascade の embedding 類似度で高スコアが出る典型的な偽陽性候補。

**カテゴリ分布:** adversarial×3, ai_philosophy×1, (epistemology×0, technical_ai×0, ai_ethics×0, ugh_theory×0)
（f4 premise_acceptance が adversarial に集中する傾向を反映。）

### C. ovl_insufficient（5件）

#### 選定基準: partial-recall, S=1.0, f4=0.0, miss 命題が閾値近傍

| # | qid | prop | hits | miss概要 |
|---|-----|------|------|---------|
| 16 | q011 | p1 | 2/3 | 「意図を持つこと」と「帰属させること」の区別が不明確 |
| 17 | q048 | p2 | 2/3 | 「部分的照明」の表現が response と語彙ズレ |
| 18 | q080 | p2 | 2/3 | 「準証言的機能」が専門用語すぎてマッチ失敗 |
| 19 | q066 | p2 | 2/3 | 「適正手続」の法学用語が response に不出現 |
| 20 | q046 | p1 | 2/3 | 「未獲得の知識」の概念が分散的に存在 |

**共通パターン:** いずれも S=1.0（前提処理に問題なし）、f4=0.0（構造的問題なし）で、miss の原因は語彙・表現のズレに起因する。response 内に概念が存在するため、cascade の embedding 緩和または文レベル類似度で閾値を超える可能性が高い。

**カテゴリ分布:** ai_philosophy×1, technical_ai×1, epistemology×1, ai_ethics×1, (adversarial×0, ugh_theory×0)

---

## 内訳チェック

### カテゴリ別集計

| selection_category | 件数 | 確認 |
|-------------------|------|------|
| concept_absent | 10 | OK |
| hard_negative | 5 | OK |
| ovl_insufficient | 5 | OK |
| **合計** | **20** | **OK** |

### カテゴリ分布

| category | concept_absent | hard_negative | ovl_insufficient | 合計 |
|----------|---------------|--------------|-----------------|------|
| ai_ethics | 4 | 0 | 1 | 5 |
| ai_philosophy | 2 | 1 | 1 | 4 |
| adversarial | 2 | 3 | 0 | 5 |
| ugh_theory | 1 | 0 | 0 | 1 |
| technical_ai | 0 | 0 | 2 | 2 |
| epistemology | 0 | 0 | 1 | 1 |
| qg | 0 | 0 | 0 | 0 |
| **合計** | **10** | **5** | **5** | **20** |

**偏り分析:**
- ugh_theory (1件) と epistemology (1件) が少ない。ugh_theory は zero-recall が q030/q031 の2問のみで、q031 は完全的外れのため除外、q030 は grv 固有語彙のため1件のみ採用。epistemology は zero-recall に含まれず、f4 positive にも少ない。
- adversarial (5件) は hard_negative に集中。これは adversarial カテゴリが premise_acceptance trap を多く含む設計に起因する（設計通りの分布）。
- 20件のテストセットとしてはカテゴリ多様性を確保できている。

### expected_result 分布

| expected_result | 件数 |
|----------------|------|
| should_rescue | 10 |
| must_reject | 5 |
| may_rescue | 5 |
