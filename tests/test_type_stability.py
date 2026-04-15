"""
tests/test_type_stability.py
Task 6: 型安定化・fail-closed・mode フィールド・schema_version の受け入れテスト
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ugh_calculator import Evidence, calculate  # noqa: E402

# --- REST API テスト ---
fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from ugh_audit.server import app, configure  # noqa: E402
from ugh_audit.storage.audit_db import AuditDB  # noqa: E402
from ugh_audit.reference.golden_store import GoldenStore  # noqa: E402


@pytest.fixture
def client(tmp_path):
    db = AuditDB(db_path=tmp_path / "test.db")
    golden = GoldenStore(path=tmp_path / "golden.json")
    configure(db=db, golden=golden)
    with TestClient(app) as c:
        yield c
    configure(db=None, golden=None)


class TestTypeStability:
    """Task 6 受け入れテスト"""

    def test_no_core_propositions_returns_degraded(self, client):
        """1. core_propositions なし → C=None, verdict=degraded, mode=degraded, f4=None"""
        resp = client.post("/api/audit", json={
            "question": "日本の首都はどこですか？",
            "response": "大阪です。",
            "reference": "東京です。",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["C"] is None
        assert data["verdict"] == "degraded"
        assert data["mode"] == "degraded"
        assert data["delta_e"] is None
        assert data["quality_score"] is None
        # question_meta なしでは f4 も未計算
        assert data["structural_gate"]["f4"] is None

    def test_no_trap_returns_f4_zero(self, client):
        """2. trap_type="" (罠なし) → f4=0.0, S は f4 込みで算出"""
        resp = client.post("/api/audit", json={
            "question": "テスト質問",
            "response": "テスト回答",
            "question_meta": {
                "question": "テスト質問",
                "core_propositions": ["命題A"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "",
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        gate = data["structural_gate"]
        assert gate["f4"] == 0.0
        assert isinstance(data["S"], float)
        assert data["S"] > 0

    def test_missing_trap_type_returns_f4_null(self, client):
        """2b. trap_type キー不在 → f4=None, S は f4 除外で算出 (分母35)"""
        resp = client.post("/api/audit", json={
            "question": "テスト質問",
            "response": "テスト回答",
            "question_meta": {
                "question": "テスト質問",
                "core_propositions": ["命題A"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        gate = data["structural_gate"]
        assert gate["f4"] is None
        assert isinstance(data["S"], float)
        assert data["S"] > 0

    def test_full_computation_returns_computed(self, client):
        """3. 全メタデータあり → mode=computed, verdict ∈ {accept, rewrite, regenerate}"""
        resp = client.post("/api/audit", json={
            "question": "PoRが高ければ誠実か？",
            "response": "PoRは誠実性の十分条件ではありません。",
            "question_meta": {
                "question": "PoRが高ければ誠実か？",
                "core_propositions": ["PoRは誠実性の十分条件ではない"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "metric_omnipotence",
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "computed"
        assert data["verdict"] in ("accept", "rewrite", "regenerate")
        assert isinstance(data["delta_e"], float)
        assert isinstance(data["quality_score"], float)
        assert isinstance(data["C"], float)

    def test_partial_missing_returns_correct_components(self, client):
        """4. 一部欠損 → computed_components と missing_components が正確"""
        resp = client.post("/api/audit", json={
            "question": "テスト",
            "response": "テスト回答",
        })
        data = resp.json()
        computed = data["computed_components"]
        missing = data["missing_components"]

        # S のみ計算される (detect() 未実行なので f1-f4 は未計算)
        assert "S" in computed
        assert "f1" not in computed
        assert "f2" not in computed
        assert "f3" not in computed

        # question_meta なし → f1-f4, C, delta_e, quality_score が missing
        assert "f1" in missing
        assert "f2" in missing
        assert "f3" in missing
        assert "f4" in missing
        assert "C" in missing
        assert "delta_e" in missing
        assert "quality_score" in missing

    def test_schema_version_present(self, client):
        """5. 全レスポンスに schema_version=2.0.0 が含まれる"""
        resp = client.post("/api/audit", json={
            "question": "テスト",
            "response": "テスト",
        })
        data = resp.json()
        assert data["schema_version"] == "2.0.0"

    def test_degraded_never_returns_accept(self, client):
        """6. verdict=degraded のとき accept でないことを確認"""
        resp = client.post("/api/audit", json={
            "question": "テスト",
            "response": "テスト",
        })
        data = resp.json()
        assert data["mode"] == "degraded"
        assert data["verdict"] != "accept"

    def test_null_not_zero_for_uncalculated(self):
        """7. 未計算フィールドが 0.0 ではなく None であることを確認"""
        e = Evidence(question_id="test", propositions_total=0)
        s = calculate(e)
        assert s.C is None
        assert s.C != 0.0
        assert s.delta_e is None
        assert s.delta_e != 0.0
        assert s.quality_score is None
        assert s.quality_score != 0.0

    def test_s_calculation_without_f4(self):
        """8. f4=None 時の S 計算が (5×f1 + 25×f2 + 5×f3) / 35 ベースであることを確認"""
        # f4=None, f2=0.5 の場合:
        # S = 1 - (5*0 + 25*0.5 + 5*0) / 35 = 1 - 12.5/35 ≈ 0.6429
        e = Evidence(
            question_id="test",
            f2_unknown=0.5,
            f4_premise=None,
            propositions_hit=1,
            propositions_total=1,
        )
        s = calculate(e)
        expected_s = 1.0 - (25 * 0.5) / 35
        assert s.S == pytest.approx(expected_s, abs=0.001)

        # f4=0.0 の場合は分母40: S = 1 - 12.5/40 = 0.6875
        e2 = Evidence(
            question_id="test",
            f2_unknown=0.5,
            f4_premise=0.0,
            propositions_hit=1,
            propositions_total=1,
        )
        s2 = calculate(e2)
        expected_s2 = 1.0 - (25 * 0.5) / 40
        assert s2.S == pytest.approx(expected_s2, abs=0.001)

        # f4=None と f4=0.0 は異なる S を返す
        assert s.S != s2.S

    def test_history_preserves_zero_scores(self, client):
        """9. C=0.0, delta_e=0.0 が history で null にならないことを確認"""
        # computed モードで C=0.0 になるケース (全命題 miss)
        resp = client.post("/api/audit", json={
            "question": "テスト質問",
            "response": "無関係な回答です。",
            "question_meta": {
                "question": "テスト質問",
                "core_propositions": ["存在しない命題XYZ"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "metric_omnipotence",
            },
        })
        data = resp.json()
        assert data["mode"] == "computed"

        # history 読み出し
        hist = client.get("/api/history").json()
        assert len(hist) >= 1
        item = hist[0]
        # C/delta_e/quality_score が 0.0 でも null にならない
        assert item["C"] is not None
        assert item["delta_e"] is not None
        assert item["quality_score"] is not None
        assert isinstance(item["C"], float)
        assert isinstance(item["delta_e"], float)
        assert isinstance(item["quality_score"], float)
