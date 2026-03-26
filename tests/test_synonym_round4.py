"""Round 4 synonym pairs のユニットテスト。

追加4ペア:
  裾野 → エッジケース (q040[2])
  境界 → 分割 (q037[1])
  ルーティング → ゲーティングネットワーク (q039[2])
  負荷 → オーバーヘッド (q039[2])
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from detector import _SYNONYM_MAP, _expand_with_synonyms


class TestRound4SynonymEntries:
    """_SYNONYM_MAP に Round 4 の4エントリが存在することを確認。"""

    @pytest.mark.parametrize(
        "key, expected_value",
        [
            ("裾野", "エッジケース"),
            ("境界", "分割"),
            ("ルーティング", "ゲーティングネットワーク"),
            ("負荷", "オーバーヘッド"),
        ],
    )
    def test_synonym_entry_exists(self, key: str, expected_value: str) -> None:
        assert key in _SYNONYM_MAP, f"キー '{key}' が _SYNONYM_MAP に存在しない"
        assert expected_value in _SYNONYM_MAP[key], (
            f"'{expected_value}' が _SYNONYM_MAP['{key}'] に含まれない"
        )


class TestRound4SynonymExpansion:
    """_expand_with_synonyms で Round 4 ペアが展開されることを確認。"""

    def test_expand_susono(self) -> None:
        result = _expand_with_synonyms({"裾野"})
        assert "エッジケース" in result

    def test_expand_kyoukai(self) -> None:
        result = _expand_with_synonyms({"境界"})
        assert "分割" in result

    def test_expand_routing(self) -> None:
        result = _expand_with_synonyms({"ルーティング"})
        assert "ゲーティングネットワーク" in result

    def test_expand_fuka(self) -> None:
        result = _expand_with_synonyms({"負荷"})
        assert "オーバーヘッド" in result


class TestRound4NoRegression:
    """既存エントリの値が Round 4 追加で破壊されていないことを確認。"""

    @pytest.mark.parametrize(
        "key, expected_value",
        [
            ("llm", "ai"),
            ("grv", "語彙"),
            ("報酬", "フィードバック"),
            ("技術", "設計"),
        ],
    )
    def test_existing_entry_intact(self, key: str, expected_value: str) -> None:
        assert expected_value in _SYNONYM_MAP[key]
