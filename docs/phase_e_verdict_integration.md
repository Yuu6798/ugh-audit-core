# Phase E — Verdict Layer Integration of mode_conditioned_grv

ローカル Claude Code に渡す実装仕様。

---

## 0. 本稿で固定する判断

- advisory の downgrade は 1 段階のみとし、`accept -> rewrite` だけを許可する
- `anchor_alignment` の leak check は Phase E.1 の必須項目に含める
- DB 保存は v1 では行わず、API レスポンスのみを拡張する
- `is_reliable` は advisory に連動させず、primary verdict 基準のまま維持する

---

## 1. 目的とスコープ

**目的:** Phase C で得られた mode_conditioned_grv の信号 (`anchor_alignment` ρ=+0.41, `collapse_risk` ρ=-0.32) を verdict 層に反映させる。

**スコープ境界:**
- In: verdict 出力の拡張、downgrade 系ルール、HA48 での閾値校正
- Out: upgrade 系ルール（弱信号で強信号を覆さない）、ΔE 閾値（0.10/0.25）の変更、`is_reliable` 定義の変更、判定ロジックの計算式（ΔE そのもの）の変更

---

## 2. 設計方針（核となる決定）

### 決定 1: 非破壊 advisory 方式（primary `verdict` は不変）

**採用案:**
- `verdict` フィールドは現状維持（ΔE ベース、HA48 校正済み 0.10/0.25）
- 新フィールド `verdict_advisory` と `advisory_flags` を追加する
- advisory は downgrade only とし、弱信号 (`mode_conditioned_grv`) で強信号 (ΔE) を upgrade しない

**理由:**
- ΔE は HA48 で ρ=-0.52、L_sem Phase 5 で ρ=-0.60 と強信号であり、`anchor_alignment` の ρ=+0.41 は副次信号である
- 強い信号の閾値を弱い信号で直接上書きすると calibration の整合性が崩れる
- consumer 側 (REST / MCP) が段階的に advisory を採用できる
- 将来 HA96+ で advisory の信頼性が確認されたら primary へ昇格する余地を残せる

**却下案:**
- (A) 合成スコア `composite = w1*ΔE - w2*anchor_alignment + w3*collapse_risk` を再 bin する案
  → ΔE calibration を壊すリスクが大きい
- (B) accept ↔ rewrite の双方向書き換え案
  → 弱信号による upgrade は fail-open を招く

### 決定 2: downgrade ルールは `accept` からのみ適用

**採用案:**
- `verdict == "accept"` のときのみ advisory downgrade を検討する
- `rewrite` / `regenerate` はすでに「修正必要」判定であり、それ以上の downgrade は v1 では冗長
- `degraded` は mcg が計算不能であるため pass-through とする

### 決定 3: ルール優先順は固定し、flags は全件記録する

**採用案:**

```text
IF verdict == "accept":
    IF collapse_risk >= τ_collapse_high:
        advisory = "rewrite"
        flags.append("mcg_collapse_downgrade")
    IF anchor_alignment <= τ_anchor_low:
        IF advisory == "accept":
            advisory = "rewrite"
        flags.append("mcg_anchor_missing")
ELSE:
    advisory = verdict
    flags = []
```

- advisory は 1 段階 (`accept -> rewrite`) のみとし、`regenerate` までは飛ばさない
- flags は発火した全ルールを順序つきで記録する
- v1 の flag 順序は `collapse` → `anchor` で固定する

---

## 3. データスキーマ変更

### REST / MCP レスポンス追加フィールド

```jsonc
{
  "verdict": "accept",
  "verdict_advisory": "rewrite",
  "advisory_flags": ["mcg_collapse_downgrade"],
  "mode_conditioned_grv": { ... },
  "is_reliable": true
}
```

### 型定義

```python
Verdict = Literal["accept", "rewrite", "regenerate", "degraded"]
AdvisoryFlag = Literal["mcg_collapse_downgrade", "mcg_anchor_missing"]
```

- `verdict_advisory: Verdict`
- `advisory_flags: list[AdvisoryFlag]`
- `verdict_advisory` は常に値を持つ。`Optional` にはしない
- `degraded` のときは `verdict_advisory = "degraded"`, `advisory_flags = []`
- `accept` でルール未発火のときは `verdict_advisory = "accept"`, `advisory_flags = []`
- `rewrite` / `regenerate` は pass-through とし、`advisory_flags = []`
- consumer には「未知の flag は無視する」ポリシーを明示する

### DB (AuditDB) への保存

v1 では新フィールドは **保存しない**。スキーマ migration は行わず、Phase E.2 以降で primary 昇格が決まった段階で再検討する。

---

## 4. 閾値 τ の校正プロトコル（Phase E.1）

実装本体の前に、まず校正スクリプトを書いて閾値を決める。

**ファイル:** `analysis/calibrate_phase_e_thresholds.py`

### 入力

- HA48 (n=48) の O スコア
- 各問の v5 ベースライン `(S, C, ΔE, verdict, anchor_alignment, collapse_risk)`
- `verdict == "accept"` のサブセット
- `verdict != "degraded"` のフルセット

### 探索グリッド

```text
τ_collapse_high ∈ {0.20, 0.22, 0.24, ..., 0.40}   (step=0.02)
τ_anchor_low    ∈ {0.60, 0.62, 0.64, ..., 0.80}   (step=0.02)
```

上記 2 軸の直積グリッドを全探索する。

**レンジ根拠 (HA48 accept subset 実分布, n=13):**

| 成分 | min | max | 参照点 |
|---|---|---|---|
| `collapse_risk` | 0.134 | 0.266 | P75≈0.25 付近が downgrade 候補の下端 |
| `anchor_alignment` | 0.796 | 0.881 | P25≈0.80 付近が downgrade 候補の上端 |

初版設計 (`{0.50..0.90} × {0.10..0.50}`) は accept 実分布から乖離しており、
全ペアで fire_rate=0 となった（2026-04-17 校正時点）。本版のレンジは HA48
accept の実分布 quantile に合わせて再定義している。

### 評価用の順序付け

accept サブセットでは primary verdict が定数になり Spearman ρ を直接比較できない。そのため評価を 2 系統に分ける。

**A. accept サブセット評価**

```python
advisory_accept_rank = 1 if verdict_advisory == "accept" else 0
```

**B. フルセット評価（`degraded` 除外）**

```python
VERDICT_QUALITY_RANK = {
    "accept": 2,
    "rewrite": 1,
    "regenerate": 0,
}
```

### メトリクス

**accept サブセット内で:**
- `rho_accept_subset`: Spearman ρ(`advisory_accept_rank`, O)
- `fire_rate`: downgrade 発火率
- `low_quality_recall`: `O_norm <= 0.4` の問で downgrade が発火した率
  （**O_norm は [0, 1] 正規化 O**。HA48 は raw O が 1–5 スケールのため、
  `O_norm = (O - 1) / 4` で変換してから比較する。HA48 では `O_norm ≤ 0.4`
  は raw `O ≤ 2.6` に相当、整数で `O ∈ {1, 2}`）
- `single_rule_fire_ratio`: 両閾値同時発火ではなく、片側発火がどれだけ多いか
- LOO-CV での `rho_accept_subset` の平均と shrinkage

**フルセット（`degraded` 除外）で:**
- `rho_primary_full`: Spearman ρ(`VERDICT_QUALITY_RANK[verdict]`, O)
- `rho_advisory_full`: Spearman ρ(`VERDICT_QUALITY_RANK[verdict_advisory]`, O)

**leak check:**
- `pearson_r(C, anchor_alignment)`
- 参考値として `spearman_r(C, anchor_alignment)` も併記する

### 選択基準

- `abs(pearson_r(C, anchor_alignment)) < 0.50` を満たすこと
- `rho_advisory_full >= rho_primary_full - 0.02`
- `fire_rate` は 10%〜25% を第一目標、上限 30% を超えないこと
- 上記条件を満たす候補の中で、`low_quality_recall` が最大の組を優先する
- 同率の場合は `rho_accept_subset` が高い方を優先する
- さらに同率の場合は `fire_rate` が低い方、次に `single_rule_fire_ratio` が高い方を優先する
- LOO-CV shrinkage が大きい場合は、より保守的な τ を採用する
- 条件を満たす候補が 1 つもない場合は、閾値をハードコードせず **no-ship** として結果を記録する

**プロトコル追補:** grid は実データの quantile（例: τ_collapse_high は
accept subset の collapse_risk の P75–P95、τ_anchor_low は P5–P25）から
決めること。HA96+ 再校正時も同様。

### 出力

- 探索した全ペアの評価表（CSV または Markdown）
- 採用した 1 組の閾値
- 上位候補 3〜5 組
- leak check の結果
- 採用理由の短いメモ

**ドキュメント成果物:** `analysis/phase_e_calibration_result.md`

このレポートには最低限、以下を記録すること:

1. 採用した `τ_collapse_high` と `τ_anchor_low`
2. `rho_primary_full` と `rho_advisory_full`
3. `rho_accept_subset`, `fire_rate`, `low_quality_recall`
4. LOO-CV shrinkage
5. `pearson_r(C, anchor_alignment)` と簡単な解釈
6. no-ship の場合は、その理由

---

## 5. 実装対象ファイル（Surgical Changes）

### 触る

| ファイル | 変更内容 |
|---|---|
| `analysis/calibrate_phase_e_thresholds.py` | HA48 での閾値探索と評価レポート出力 |
| `ugh_audit/server.py` | `_run_pipeline` で advisory 計算、`AuditResponse` に 2 フィールド追加 |
| `ugh_audit/mcp_server.py` | 同上。constructor と MCP proxy path の両方で伝播確認 |
| `mode_grv.py` | `derive_verdict_advisory(verdict, mcg, thresholds) -> (advisory, flags)` を追加 |
| `tests/test_server.py` | advisory フィールド確認 |
| `tests/test_mcp_server.py` | 同上 |
| `tests/test_mode_grv.py` | `derive_verdict_advisory` の単体テスト追加 |
| `docs/phase_e_verdict_integration.md` | この設計書を配置 |
| `CLAUDE.md` | `verdict_advisory` / `advisory_flags` を API response または key outputs の該当節に追記し、設計ドキュメント索引にも 1 行追加 |

### 触らない

- `ugh_calculator.py` — ΔE threshold と primary verdict ロジックは不変
- `decider.py` — repair order は primary verdict 基準のまま
- `storage/audit_db.py` — DB スキーマ不変
- `grv_calculator.py` — Phase C の計算そのものは不変
- `semantic_loss.py` — 無関係

### 閾値の配置

`mode_grv.py` 先頭に module-level 定数で定義する:

```python
# Phase E thresholds — HA48 calibrated (n=?, YYYY-MM-DD)
_TAU_COLLAPSE_HIGH: float = ...
_TAU_ANCHOR_LOW: float = ...
```

`engine/calculator.py` には持ち込まない。Phase C と同様に numpy 依存を増やさない。

---

## 6. 擬似コード（ローカル実装の核）

```python
# mode_grv.py に追加
from typing import List, Literal, Optional, Tuple

Verdict = Literal["accept", "rewrite", "regenerate", "degraded"]
AdvisoryFlag = Literal["mcg_collapse_downgrade", "mcg_anchor_missing"]


def derive_verdict_advisory(
    verdict: Verdict,
    mcg: Optional[ModeConditionedGrv],
    *,
    tau_collapse_high: float = _TAU_COLLAPSE_HIGH,
    tau_anchor_low: float = _TAU_ANCHOR_LOW,
) -> Tuple[Verdict, List[AdvisoryFlag]]:
    """
    primary verdict と mode_conditioned_grv から advisory verdict と flags を導出する。
    v1 は accept のみ downgrade 対象。他の verdict は pass-through。
    """
    flags: List[AdvisoryFlag] = []

    if verdict != "accept" or mcg is None:
        return verdict, flags

    advisory: Verdict = verdict

    if mcg.collapse_risk is not None and mcg.collapse_risk >= tau_collapse_high:
        advisory = "rewrite"
        flags.append("mcg_collapse_downgrade")

    if mcg.anchor_alignment is not None and mcg.anchor_alignment <= tau_anchor_low:
        if advisory == "accept":
            advisory = "rewrite"
        flags.append("mcg_anchor_missing")

    return advisory, flags
```

**server.py 側の接続点（`_run_pipeline` 内）:**

```python
mcg = result.get("mode_conditioned_grv")
advisory, flags = derive_verdict_advisory(verdict, mcg)
result["verdict_advisory"] = advisory
result["advisory_flags"] = flags
```

**AuditResponse:**
- `verdict_advisory` は `Optional` ではなく常時必須
- `advisory_flags` は空 list が default だが、mutable default は使わない
  - Pydantic なら `Field(default_factory=list)`
  - dataclass なら `field(default_factory=list)`
- constructor / response builder / proxy relay の全経路で伝播させる

---

## 7. テスト（受入基準）

### 単体テスト (`tests/test_mode_grv.py`)

| ケース | 入力 | 期待 |
|---|---|---|
| 1 | `verdict="accept", mcg=None` | `("accept", [])` |
| 2 | `verdict="rewrite", mcg=任意` | `("rewrite", [])` |
| 3 | `verdict="regenerate", mcg=任意` | `("regenerate", [])` |
| 4 | `verdict="degraded", mcg=None` | `("degraded", [])` |
| 5 | `accept + collapse_risk >= τ_collapse_high` | `("rewrite", ["mcg_collapse_downgrade"])` |
| 6 | `accept + anchor_alignment <= τ_anchor_low` | `("rewrite", ["mcg_anchor_missing"])` |
| 7 | `accept + 両閾値 violation` | `("rewrite", ["mcg_collapse_downgrade", "mcg_anchor_missing"])` |
| 8 | `accept + collapse_risk=None + anchor violation` | anchor のみ発火 |
| 9 | `accept + anchor_alignment=None + collapse violation` | collapse のみ発火 |
| 10 | `accept + 両信号 None` | `("accept", [])` |
| 11 | 境界値ちょうど | `>= / <=` の等号側で発火 |

### 統合テスト (`tests/test_server.py`)

- `/api/audit` レスポンスに `verdict_advisory` と `advisory_flags` が含まれる
- `collapse_risk` が高い fixture で `verdict_advisory == "rewrite"` になる
- `anchor_alignment` が低い fixture で `verdict_advisory == "rewrite"` になる
- primary `verdict == "rewrite"` の fixture で advisory も `"rewrite"` のまま
- `degraded` fixture で advisory == `"degraded"`, flags == `[]`

### MCP テスト (`tests/test_mcp_server.py`)

- proxy path (`UGH_REMOTE_API`) でも advisory が転送される
- stateless_http モードでセッション間に advisory 計算の汚染がない
- constructor path / proxy path の両方で `advisory_flags` が欠落しない

### 受入基準（ship 条件）

この PR が ship 可能なのは以下を満たしたとき:

1. `ruff check` が clean
2. 既存フルテストスイート + 新規テストが pass
3. `analysis/phase_e_calibration_result.md` に校正結果が記録されている
4. `degraded` 除外の HA48 で `rho_advisory_full >= rho_primary_full - 0.02`
5. accept サブセットで `fire_rate` が 10%〜30% に収まる
6. `abs(pearson_r(C, anchor_alignment)) < 0.50` が確認されている
7. no-ship 条件に該当しない

---

## 8. リスクと観測項目

1. **n=48 での閾値校正の統計的弱さ**
   accept サブセットは小さい可能性が高く、overfit リスクがある。
   → LOO-CV shrinkage を必ず併記し、必要なら τ を保守側に寄せる。

2. **`anchor_alignment` の C 経由漏洩**
   question keyword 系の処理が C と相関している可能性がある。
   → `pearson_r(C, anchor_alignment)` を必ず確認し、解釈をレポートに明記する。

3. **`advisory_flags` の将来拡張**
   v1 は `{mcg_collapse_downgrade, mcg_anchor_missing}` のみ。
   → consumer には「未知の flag は無視する」ことを明示する。

4. **実装順序の崩れによる再作業**
   閾値未確定のまま API 実装へ進むと手戻りが増える。
   → 先に Phase E.1 を終えて τ を確定し、その後 API 統合に進む。
