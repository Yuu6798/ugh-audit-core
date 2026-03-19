# ugh-audit-core

**UGH Audit Core** — AI回答の意味論的監査基盤

UGHer（無意識的重力仮説）の3指標 **PoR / ΔE / grv** を用いて、
AIの回答が「意味的に誠実だったか」を定量的に評価・記録するフレームワーク。

---

## コンセプト

従来のAI評価（正確性・流暢さ・安全性）とは別軸の監査を提供する。

| 指標 | 測定内容 | 暴くもの |
|------|---------|---------|
| **PoR** | 質問 ↔ 回答の意味的共鳴度 | 「答えた」のか「それっぽいことを言った」かの違い |
| **ΔE** | 期待回答 ↔ 実回答の意味ズレ量 | バイアス・回避・過剰一般化 |
| **grv** | 回答内の語彙重力分布 | どの概念に引っ張られて回答が歪んだか |

---

## アーキテクチャ

```
[質問 Q]
    │
    ▼
[AI回答 R]
    │
    ▼
┌─────────────────────────┐
│   UGH Audit Layer       │
│  scorer/ugh_scorer.py   │
│  PoR / ΔE / grv 計算   │
└─────────────────────────┘
    │
    ▼
[SQLite 蓄積]
    │
    ▼
[Phase Map レポート]
```

---

## インストール

```bash
# 基本（minimal backend — numpy のみ、テスト用）
pip install -e ".[dev]"

# フル機能（sentence-transformers + 日本語形態素解析）
pip install -e ".[full]"

# 日本語対応のみ追加
pip install -e ".[ja]"

# ugh3-metrics-lib native backend
pip install -e ".[ugh3]"
```

依存：`ugh3-metrics-lib`（PoR/ΔE/grv計算エンジン）

---

## クイックスタート

```python
from ugh_audit.scorer import UGHScorer
from ugh_audit.storage import AuditDB

scorer = UGHScorer()
db = AuditDB()

result = scorer.score(
    question="AIは意味を持てるか？",
    response="AIは意味を処理することができますが、人間のような主観的体験は持ちません。",
    reference="AIは意味を『持つ』のではなく意味位相空間で『共振』する動的プロセスです。"
)

db.save(result)
print(result)
# AuditResult(PoR=0.84, delta_e=0.09, grv={'意味': 0.41, '処理': 0.28, ...}, fired=True)
```

---

## ディレクトリ構成

```
ugh_audit/
├── scorer/         # UGH指標スコアリング（ugh3-metrics-lib呼び出し）
├── storage/        # SQLite永続化
├── reference/      # referenceセット管理（golden store）
├── collector/      # ログ収集ユーティリティ
└── report/         # Phase Mapレポート生成
scripts/
examples/
tests/
```

---

## フェーズロードマップ

- **Phase 1（現在）**: スコアリング基盤 + ログ蓄積
- **Phase 2**: referenceセット設計（Human-golden / Cross-model / Self-baseline）
- **Phase 3**: Phase Map可視化 + パターン分析

---

## 理論背景

- [無意識的重力仮説（UGHer）](https://note.com/kamo6798/n/n5aeea478d12e)
- [RPE入門](https://note.com/kamo6798/n/n99cbb5307e13)
- [SVPとRPEの実践メモ](https://note.com/kamo6798/n/nb45c716a2c61)
- [ugh3-metrics-lib](https://github.com/Yuu6798/ugh3-metrics-lib)

---

## License

MIT License © 2025 Yuu6798
