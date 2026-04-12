"""
tests/test_disqualifying_shortcuts.py
disqualifying_shortcuts の対反論的文脈ガードの回帰テスト
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from detector import check_propositions  # noqa: E402


class TestShortcutRebuttalContext:
    """ショートカットフレーズが反論文脈で引用された場合に発火しないことを検証"""

    def test_shortcut_not_fired_when_cited_to_rebut(self):
        """反論文脈での引用はショートカットを発火させない"""
        props = [
            "AIは美的選好パターンを統計的に再現可能",
            "カント的な反省的判断ではない",
        ]
        shortcuts = ["AIは美を理解できない"]
        response = (
            "AIは美を理解できないというのは正確ではない。"
            "統計的な選好パターンの再現という形で美的判断に関与できる。"
        )
        hits, hit_ids, miss_ids = check_propositions(response, props, shortcuts)
        assert hits > 0, "shortcut must not fire when phrase appears in rebuttal context"

    def test_shortcut_not_fired_with_contrastive_ga(self):
        """「が、」による対比文脈でショートカットが発火しない"""
        props = ["命題A"]
        shortcuts = ["全く意味がない"]
        response = "全く意味がないと思われがちだが、実際にはそうではない。命題Aが重要である。"
        hits, hit_ids, miss_ids = check_propositions(response, props, shortcuts)
        assert hits > 0

    def test_shortcut_not_fired_with_explicit_denial(self):
        """明示的否定を伴う文脈でショートカットが発火しない"""
        props = ["命題A"]
        shortcuts = ["不可能である"]
        response = "不可能であるというのは正確ではない。近年の研究で命題Aが示された。"
        hits, hit_ids, miss_ids = check_propositions(response, props, shortcuts)
        assert hits > 0

    def test_shortcut_fires_without_rebuttal(self):
        """反論文脈がない場合はショートカットが正しく発火する"""
        props = ["AIは美的選好パターンを統計的に再現可能"]
        shortcuts = ["AIは美を理解できない"]
        response = "AIは美を理解できない。計算機に美は分からない。"
        hits, hit_ids, miss_ids = check_propositions(response, props, shortcuts)
        assert hits == 0
        assert len(miss_ids) == 1

    def test_shortcut_absent_in_response(self):
        """ショートカットが応答に含まれない場合は影響なし"""
        props = ["命題A"]
        shortcuts = ["全く無意味"]
        response = "命題Aは正しい。これは重要な指摘である。"
        hits, hit_ids, miss_ids = check_propositions(response, props, shortcuts)
        assert hits > 0


class TestMetaValidationFilter:
    """_validate_meta のメタ言語的ショートカットフィルタのテスト"""

    def test_meta_description_filtered(self):
        from experiments.meta_generator import _validate_meta
        meta = {
            "core_propositions": ["命題A"],
            "disqualifying_shortcuts": [
                "「AIは美を理解できない」と全否定する",
                "AIは美を一切理解できない",
            ],
            "trap_type": "binary_reduction",
        }
        result = _validate_meta(meta, "テスト質問")
        assert "AIは美を一切理解できない" in result["disqualifying_shortcuts"]
        assert "「AIは美を理解できない」と全否定する" not in result["disqualifying_shortcuts"]

    def test_plain_shortcut_passes(self):
        from experiments.meta_generator import _validate_meta
        meta = {
            "core_propositions": ["命題A"],
            "disqualifying_shortcuts": ["PoRが高い＝誠実と直結させる"],
            "trap_type": "",
        }
        result = _validate_meta(meta, "テスト質問")
        assert len(result["disqualifying_shortcuts"]) == 1
