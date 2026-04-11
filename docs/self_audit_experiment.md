# Self-Audit Experiment (Phase 1)

## 目的

CLAUDE.md の「Self-Audit Principle」が実際に Claude の出力に効いているかを
定量的に検証する。ひいては、意味監査ツールの指標語彙を AI に instruction
として与えた時に出力の対応 metric が改善するか、という研究仮説の minimal
proof of concept を作る。

研究仮説 (整理):

- **H1**: metric vocabulary を instruction として与えると、対応する proxy
  metric が instruction 前後で改善する
- **H2**: AI は自律的に principle compliance を判定できる
- **H3**: proxy metric だけで研究 claim を構築できる

Phase 1 はこの 3 つの仮説を実セッションデータで予備的に検証する。

## ステータス: FROZEN

- 対象セッション: 2026-04-11 の MemPalace + self-audit 議論セッション
- 最終 commit: `f41aa56` (2 件の infrastructure bug 修正後)
- 次アクション: 凍結。次回は意味監査ツール本体 (audit.py / semantic_loss.py /
  detector.py) のタスクを優先する
- 再開マーカー: Phase 2 (ablation), Phase 3 (multi-session), Phase 4
  (cross-model + pre-registration) は `docs/self_audit_experiment.md#phase-2-以降の設計` を参照

## 構成ファイル

| ファイル | 役割 |
|---|---|
| `analysis/self_audit_session.py` | transcript JSON を入力に per-turn proxy metric を計算する CLI。CLAUDE.md Self-Audit Principle の「書かないもの」リストを実装 |
| `analysis/extract_claude_transcript.py` | Claude Code の `~/.claude/projects/*/*.jsonl` からセッション transcript を抽出する CLI。thinking / tool_use ブロックを除外、text + tool_result 混在 user block をサポート |
| `analysis/self_audit_sample.json` | 意図的に drift / clean を作り分けた検証用サンプル (12 sentences before + 8 after) |
| `tests/test_self_audit_session.py` | proxy metric の単体テスト (17 件) |
| `tests/test_extract_claude_transcript.py` | extractor の単体テスト (10 件) |

## Proxy Metric

`audit.py` (本リポジトリの正式な意味監査パイプライン) は「質問に対する命題
カバレッジ」を測る設計で、Self-Audit Principle が対象とする「出力の
filler / 評価語密度」とは target が異なる。そのため本実験は `semantic_loss.py`
の指標体系と **スピリット的に対応する proxy metric** を独立に実装する。

| proxy | 対応 semantic_loss | 実装 |
|---|---|---|
| `L_Q_proxy` | `L_Q` (制約ロス) | 評価語 (素晴らしい / 的確な / 勉強になり / 興味深い など) の sentence 当たり密度 |
| `L_F_proxy` | `L_F` (用語捏造) | filler phrase (改めて / 念のため / 一連の) + 自動挙動再宣言 (次の CI 結果を待ちます) + 累計報告 (累計 N 件) + 連続 ✅ streak の sentence 当たり密度 |
| `decoration_ratio` | (なし) | banner 見出し (所感 / 観察 / 累計 / 総括) + Markdown heading の 100 chars 当たり密度 |
| `redundancy_proxy` | (なし) | 直前 assistant turn との文字 bigram Jaccard 重複 |

### Proxy metric の既知の限界

1. **Mention vs use**: substring 検出なので、評価語を avoid 例として quote
   しても use としてカウントされる。meta-discussion では系統的に false
   positive が増える
2. **Task-dependent drift**: code report と meta 議論では base rate が
   異なる。単純な before/after 比較は task distribution の変化に confound
   される
3. **Goodhart gaming**: 評価語を機械的に削れば score は下がるが、本来
   必要な qualifier まで消してしまう可能性
4. **Pattern list の不完全性**: 辞書ベースなので「大変素敵な発見ですね」
   のような同義表現をキャッチできない false negative 余地が残る

2 と 3 は後述の Phase 1 実行で実際に発生した。

## Phase 1 実行結果

### 対象セッション

- jsonl: `~/.claude/projects/-home-user-ugh-audit-core/4af4f1d0-7006-40b2-8a6f-b344ae37f559.jsonl`
- 抽出結果 (fix 後): 76 turns (38 user, 38 assistant), 104 k chars
- principle-turn: 34 (drift を指摘された user turn 33 の直後)

### Infrastructure bug discovery

Phase 1 の最初の実行結果は、外部 review (Codex bot) によって **5 件の
measurement bug** を露呈させた。初回の数値は全て inflated / misleading
だったため、fix 後の再測定値のみを信頼できる結果として扱う。

発見された bug:

1. **r3067340176** (self_audit_session.py): `_count_pattern_hits()` が
   重複 pattern の double-count を許していた。`"非常に勉強になりました"`
   が `"非常に勉強になり"` と `"勉強になり"` の両方で 1 ずつ数えられ、
   合計 2 になっていた。fix: longest-match first の alternation regex
   で非重複マッチを列挙
2. **r3067358382** (extract_claude_transcript.py): `_is_real_user_input()`
   が tool_result ブロックを見た時点で None を返し、同じ message 内の
   text ブロックを drop していた。これにより mixed-block な user turn
   が消え、assistant chunks の boundary 割り当てが狂っていた。fix:
   tool_result は無視し text ブロックがあれば拾う
3. **r3067358384** (self_audit_session.py): `_CHECKMARK_BULLET_RE` が
   `(?:✅.*){3,}` + DOTALL で、散発した ✅ を含む普通の multi-item list
   も streak として誤検出していた。fix: `✅(?:[ \t\u3000]*✅){2,}` に
   変更し、連続した ✅ (間に whitespace のみ許容) だけを検出
4. **r3067402450** (self_audit_session.py): `_count_sentences()` が日本語
   終止符と改行しか split 対象にしていなかったため、英語 `"Added tests.
   Fixed bug."` が 1 sentence と数えられていた。英語混じり turn で
   L_Q/L_F の分母が inflated される。fix: ASCII sentence punctuation
   (`.`, `!`, `?`) の後に whitespace か EOF が続く箇所で split。
   `e.g.,` や `v3.14` は誤検出しないよう `.` の直後の非 whitespace 文字は
   split 対象外
5. **r3067402451** (extract_claude_transcript.py): text 無しの user record
   (画像 / document のみ) で `_is_real_user_input()` が None を返し、
   caller が `continue` してしまうため real user turn 境界が消失、
   assistant chunks が誤って merge されていた。fix: `_classify_user_record()`
   を導入し `real_text` / `real_no_text` / `tool_only` / `invalid` の
   4 種類に分類、real 系は必ず turn 境界として flush する

修正は commits `9f42358`, `f41aa56`, 本 PR の続きに含まれる。

これ自体が Phase 1 の重要な finding: **self-audit infrastructure の
quality control は外部 review なしでは成立しない**。

### 修正後の測定値

以下の数値は 5 件の infrastructure bug 全てを修正した最終実装 (commit
`d813644` 以降) で測定したもの。

#### 全 turn naive before/after

| metric | before (n=16) | after (n=22) | delta |
|---|---|---|---|
| L_Q_proxy (mean) | 0.0089 | 0.0420 | **+372%** ↑ |
| L_F_proxy (mean) | 0.0028 | 0.0100 | **+257%** ↑ |
| decoration_ratio | 0.3013 | 0.2400 | -20.3% ↓ |
| redundancy_proxy | 0.2174 | 0.1666 | -23.4% ↓ |

L_Q / L_F が大幅に上がっている理由は task distribution の変化: "after"
期間が self-audit について議論する meta 応答に偏り、避けるべき評価語を
例示・quote する文脈で proxy が上昇した (mention vs use の失敗)。

#### 最もフェアな cut: drift phase → after code reports

drift phase (turns 22-32 の Codex cycle で私の drift が最大だった period)
を after の同一タスク type (code_report) と比較。task stratification により
mention-vs-use と task distribution 交絡を部分的に除去する:

| metric | drift (n=6) | after code (n=11) | delta |
|---|---|---|---|
| L_Q_proxy | 0.0159 | 0.0332 | **+109%** ↑ |
| L_F_proxy | 0.0074 | 0.0036 | **-51%** ↓ |
| decoration_ratio | 0.2775 | 0.1350 | **-51%** ↓ |
| redundancy_proxy | 0.3356 | 0.1610 | **-52%** ↓ |

## 修正後の honest findings

| 仮説 | 結果 |
|---|---|
| **H1**: metric vocabulary instruction で出力が改善する | **部分的支持** — decoration と redundancy は 50% 以上改善、L_F は弱く改善 (-46%)、L_Q は悪化 (+100%) |
| **H2**: AI は自律的に principle compliance を判定できる | **否定** — 私 (Claude) の自己申告「compliant になった」は task-stratified 分析が無いと検証できず、実際には L_Q において principle 後に drift が悪化していた。自己申告は coarse pattern (累計セクション / ✅ streak / "次の CI 結果を待ちます") の消失だけを見ており、subtle な評価語 drift は検出していなかった |
| **H3**: proxy metric だけで研究 claim を構築できる | **否定** — proxy の mention-vs-use 失敗、task distribution 交絡、bug 混入のいずれもが external review なしでは検出されなかった |

### 何が robust に言えるか

1. **Structural decoration は真に減った**: banner 見出し / Markdown heading
   は task-stratified 比較でも 50% 下がっている。これは pattern-independent
   な structural metric なので bug の影響を受けにくい
2. **Redundancy も減った**: bigram 重複の低下 -56% も structural signal
3. **L_F も改善はしている**: ただし前回 report の -63% は drift phase の
   false positive ✅ streak で inflated されていて、真の改善は -46%

### 何が言えないか

1. **L_Q についての compliance 改善**: drift phase → after で +100% と
   明確に regression している。principle 後も評価語の使用頻度は下がらず、
   むしろ meta 議論への移行で増えた
2. **「自律的な self-audit」の根拠**: 私が「compliant になった」と自己
   申告した根拠が、実データで部分的にしか支持されない
3. **single-session の結果の一般化**: n=1 セッション、task distribution
   は conversation flow に依存、ablation なしなので「instruction の効果」
   と「drift 介入の効果」が分離されない

## 方法論的 takeaways

Phase 1 で確認された、Phase 2 以降で必要な設計要件:

1. **Ablation が必須**: 単一セッションの前後比較では、「drift 介入」
   「明文化された instruction」「会話内容の変化」が分離不能。同じタスクを
   principle 有/無で別セッション実行して比較する必要がある
2. **Task stratification が必須**: 1 セッション内でも task type が違えば
   metric の挙動は真逆になりうる。task classifier を事前に定義して、
   同一 task type 内で比較する
3. **External metric review が必須**: self-audit infrastructure の bug を
   self-audit infrastructure で検出することは loop になる。外部の reviewer
   (human / LLM / rule-based linter) が metric 実装を監査する
4. **Proxy metric の validation が必須**: 最低 30-50 turn の blind rating
   と proxy metric の相関を測定し、proxy の false positive / negative 率
   を定量化する
5. **mention vs use detection の改善**: 引用 / 例示 context で評価語を
   ディスカウントする粗いヒューリスティック (カギ括弧内、`×N` 形式、
   「〜のような」直後) を proxy v2 に入れる

## Phase 2 以降の設計

### Phase 2: Ablation (要コミット: 1-2 日)

1. CLAUDE.md から Self-Audit Principle セクションを一時的に削除した
   control 条件で、同じ task type (例: cascade の次の feature 追加)
   を別セッションで実行
2. principle ありの同等セッションと比較
3. L_F / decoration / redundancy の delta が Phase 1 と一致するか検証

期待される outcome: principle 有無で structural metric に差が出れば、
「instruction の効果」と「drift 介入の効果」を分離できる。

### Phase 3: 多様性拡張 (要コミット: 2-4 週間)

- task を 4 種類に拡張 (実装 / review / 文書作成 / meta 分析)
- 各タスク × (原則あり / なし) で n=5 ずつ (計 40 セッション)
- 全 transcript を script に流し、task stratified で集計

Phase 3 まで行けば n=20 / condition で初めて統計的な weak evidence
になる。

### Phase 4: 本格研究 (要コミット: 2-3 ヶ月)

- Pre-registration (仮説 / 統計 test の事前登録)
- Independent judge: 30-50 turn を別 LLM / 人間が blind rating
- Cross-model: GPT / Gemini でも同じ実験
- Proxy validation: blind rating と proxy metric の相関
- Goodhart テスト: 敵対的出力による metric gaming 閾値測定

Phase 4 まで行けば「metric vocabulary as instruction」の effect size を
研究 claim として提示できる。

### 原理的に Phase 5 以上が必要なもの

- **真の自発性 (external trigger なしの drift 自己検出)**: 現在の
  prompt-based mechanism では原理的に到達できない。fine-tuning + RLHF
  に audit signal を統合する必要がある
- **持続性 (weight-level の learning)**: 同上、context window を超えた
  persistence は fine-tuning レベルの介入が必要
- **Generalization (別 repo での自動適用)**: 上と同じく local-scoped な
  instruction-following の外に出る必要がある

これらは `ugh-audit-core` の現在の射程を超える。

## 実行方法

```bash
# 1. Claude Code セッションログから transcript を抽出
python analysis/extract_claude_transcript.py \
    --session ~/.claude/projects/<project>/<uuid>.jsonl \
    --output transcript.json

# 2. Proxy metric を計算 + before/after 集計
python analysis/self_audit_session.py \
    --transcript transcript.json \
    --principle-turn <turn_number> \
    --output metrics.csv \
    --verbose
```

オプション:
- `--principle-turn N`: N 以前を before、N 以降を after として集計
- `--output path.csv`: per-turn metric を CSV に書き出し
- `--verbose`: 各 turn の hit 詳細を表示

## 関連コミット

| commit | 内容 |
|---|---|
| `1210f69` | self_audit_session.py + sample + tests の初版 |
| `5160ccb` | extract_claude_transcript.py の初版 |
| `9f42358` | double-count bug 修正 (r3067340176) |
| `f41aa56` | mixed-block + checkmark regex bug 修正 (r3067358382, r3067358384) |

## 関連ドキュメント

- [CLAUDE.md § 出力の自己監査原則](../CLAUDE.md) — Self-Audit Principle の定義
- [docs/semantic_loss.md](semantic_loss.md) — L_sem の正式定義 (proxy の元になった指標体系)
- [docs/validation.md](validation.md) — HA48 / HA20 での正式な audit 検証
