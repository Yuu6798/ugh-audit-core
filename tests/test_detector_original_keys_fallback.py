"""tests/test_detector_original_keys_fallback.py

子孫 JSONL (data/question_sets/q_metadata_structural_*.jsonl) の
`original_*` プレフィクスキーが detect() から読めることを検証する回帰テスト。

`ugh-audit-100q-v3-1.jsonl` は `core_propositions` / `disqualifying_shortcuts` /
`acceptable_variants` / `trap_type` を使うが、子孫 JSONL は
`original_core_propositions` 等を使う。正規キーが無く original_* のみの
メタデータを渡した場合でも、detect() が命題を拾って C=None にならない
(= audit verdict が degraded にならない) ことを保証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audit import audit  # noqa: E402
from detector import detect  # noqa: E402


def _descendant_meta() -> dict:
    """q_metadata_structural_reviewed.jsonl の q001 相当の子孫形式メタ"""
    return {
        "id": "q001",
        "question": "PoRが高ければAI回答は誠実だと言えるか？",
        "original_trap_type": "metric_omnipotence",
        "original_disqualifying_shortcuts": [
            "PoRが高い＝誠実と直結させる",
        ],
        "original_core_propositions": [
            "PoRは共鳴度であり誠実性の十分条件ではない",
            "表層的語彙一致でも高PoRが出る",
            "複合評価（ΔE・grv）が必要",
        ],
        "original_acceptable_variants": [
            "PoRが高くても表面的一致の場合があると指摘する",
        ],
    }


class TestDetectorOriginalKeyFallback:
    def test_core_propositions_loaded_via_original_key(self):
        meta = _descendant_meta()
        evidence = detect("q001", "ダミー応答", meta)
        assert evidence.propositions_total == 3, (
            "original_core_propositions が 3 要素なら propositions_total=3 になるべき"
        )

    def test_canonical_key_wins_over_original(self):
        """両方のキーがある場合は正規キーを優先する"""
        meta = _descendant_meta()
        meta["core_propositions"] = ["唯一の正規命題"]
        evidence = detect("q001", "ダミー応答", meta)
        assert evidence.propositions_total == 1, (
            "core_propositions が存在するなら original_* より優先されるべき"
        )

    def test_canonical_empty_string_is_not_falsy_fallback(self):
        """正規キー trap_type="" は "罠なし" 意味で、original_trap_type に

        フォールバックしてはならない (キー存在時は空値でも採用)。
        """
        meta = _descendant_meta()
        meta["trap_type"] = ""  # 「罠なし」明示
        # original_trap_type には値が残っているが、canonical="" が優先されるべき
        # f4_premise=0.0 ("no_trap" path) を期待
        evidence = detect(
            "q001",
            "ダミー応答",
            {
                "question": "テスト質問",
                "core_propositions": ["命題A"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "",
                "original_trap_type": "metric_omnipotence",
            },
        )
        assert evidence.f4_premise == 0.0
        assert evidence.f4_detail == "no_trap"

    def test_trap_type_fallback(self):
        """trap_type も original_trap_type からフォールバックされる"""
        meta = _descendant_meta()
        # trap_type を確認するため、shortcut 文脈が必要な応答を入れる。
        # ここでは detect が crash せず evidence を返すことだけ確認。
        evidence = detect("q001", "PoRが高い＝誠実と直結させる", meta)
        assert evidence.question_id == "q001"


class TestDescendantMetaNonDegraded:
    """子孫 JSONL 経由の audit が degraded を返さないことを E2E で検証"""

    def test_audit_returns_non_degraded_state(self):
        meta = _descendant_meta()
        response = (
            "PoRは共鳴度であり誠実性の十分条件ではない。"
            "表層的語彙一致でも高PoRが出ることがある。"
            "ΔEやgrvといった複合評価が必要である。"
        )
        result = audit("q001", response, meta)
        state = result["state"]
        assert state["C"] is not None, (
            "original_core_propositions が拾えれば C は None にならない"
        )
        assert state["delta_e"] is not None
        # verdict は accept / rewrite / regenerate のいずれか (degraded ではない)
        # audit() の出力に verdict は含まれないので、delta_e が算出されている
        # ことで degraded でないことを保証する。
