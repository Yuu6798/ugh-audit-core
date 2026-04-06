"""
tests/test_pipeline_a.py
パイプライン A の追加テスト: quality_score, verdict, API 出力フォーマット
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ugh_calculator import Evidence, calculate  # noqa: E402


# --- quality_score テスト ---

class TestQualityScore:
    """quality_score = 5 - 4 * delta_e のテスト"""

    def test_perfect_quality(self):
        """ΔE=0 → quality_score=5.0"""
        e = Evidence(question_id="t", propositions_hit=3, propositions_total=3)
        s = calculate(e)
        assert s.delta_e == 0.0
        assert s.quality_score == 5.0

    def test_worst_quality(self):
        """ΔE が高い → quality_score が低い"""
        e = Evidence(
            question_id="t", f2_unknown=1.0,
            propositions_hit=0, propositions_total=3, miss_ids=[0, 1, 2],
        )
        s = calculate(e)
        assert s.delta_e > 0.4
        assert s.quality_score == pytest.approx(5.0 - 4.0 * s.delta_e, abs=0.001)

    def test_quality_score_formula(self):
        """quality_score = 5 - 4 * delta_e を直接検証"""
        e = Evidence(
            question_id="t", f1_anchor=0.5,
            propositions_hit=2, propositions_total=3,
            hit_ids=[0, 1], miss_ids=[2],
        )
        s = calculate(e)
        expected = max(1.0, min(5.0, 5.0 - 4.0 * s.delta_e))
        assert s.quality_score == pytest.approx(expected, abs=0.001)

    def test_quality_score_clamp_lower(self):
        """quality_score は 1.0 を下回らない"""
        # ΔE が最大の場合でも quality_score >= 1.0
        e = Evidence(
            question_id="t",
            f1_anchor=1.0, f2_unknown=1.0, f3_operator=1.0, f4_premise=1.0,
            propositions_hit=0, propositions_total=5,
            miss_ids=[0, 1, 2, 3, 4],
        )
        s = calculate(e)
        assert s.quality_score >= 1.0

    def test_quality_score_in_state(self):
        """State dataclass に quality_score フィールドが存在する"""
        e = Evidence(question_id="t", propositions_hit=1, propositions_total=1)
        s = calculate(e)
        assert hasattr(s, "quality_score")
        assert isinstance(s.quality_score, float)


# --- verdict テスト ---

class TestVerdict:
    """verdict ロジックの確定閾値テスト (accept ≤ 0.10, rewrite ≤ 0.25, regenerate > 0.25)"""

    def _verdict(self, delta_e: float) -> str:
        if delta_e <= 0.10:
            return "accept"
        if delta_e <= 0.25:
            return "rewrite"
        return "regenerate"

    def test_accept_boundary(self):
        assert self._verdict(0.0) == "accept"
        assert self._verdict(0.05) == "accept"
        assert self._verdict(0.10) == "accept"

    def test_rewrite_boundary(self):
        assert self._verdict(0.11) == "rewrite"
        assert self._verdict(0.20) == "rewrite"
        assert self._verdict(0.25) == "rewrite"

    def test_regenerate_boundary(self):
        assert self._verdict(0.26) == "regenerate"
        assert self._verdict(0.50) == "regenerate"
        assert self._verdict(1.00) == "regenerate"

    def test_verdict_from_server(self):
        """server.py の _verdict と同じ結果になることを確認"""
        from ugh_audit.server import _verdict as server_verdict

        assert server_verdict(0.0) == "accept"
        assert server_verdict(0.10) == "accept"
        assert server_verdict(0.11) == "rewrite"
        assert server_verdict(0.25) == "rewrite"
        assert server_verdict(0.26) == "regenerate"

    def test_verdict_from_mcp(self):
        """mcp_server.py の _verdict と同じ結果になることを確認"""
        from ugh_audit.mcp_server import _verdict as mcp_verdict

        assert mcp_verdict(0.0) == "accept"
        assert mcp_verdict(0.10) == "accept"
        assert mcp_verdict(0.11) == "rewrite"
        assert mcp_verdict(0.25) == "rewrite"
        assert mcp_verdict(0.26) == "regenerate"

    def test_verdict_from_collector(self):
        """audit_collector.py の _verdict と同じ結果になることを確認"""
        from ugh_audit.collector.audit_collector import _verdict as coll_verdict

        assert coll_verdict(0.0) == "accept"
        assert coll_verdict(0.10) == "accept"
        assert coll_verdict(0.11) == "rewrite"
        assert coll_verdict(0.25) == "rewrite"
        assert coll_verdict(0.26) == "regenerate"


class TestGateVerdict:
    """structural_gate の gate_verdict テスト (pass / warn / fail)"""

    def test_gate_pass(self):
        from ugh_audit.server import _gate_verdict
        assert _gate_verdict(0.0, 0.0, 0.0, 0.0) == "pass"

    def test_gate_warn(self):
        from ugh_audit.server import _gate_verdict
        assert _gate_verdict(0.5, 0.0, 0.0, 0.0) == "warn"
        assert _gate_verdict(0.0, 0.5, 0.5, 0.0) == "warn"

    def test_gate_fail(self):
        from ugh_audit.server import _gate_verdict
        assert _gate_verdict(1.0, 0.0, 0.0, 0.0) == "fail"
        assert _gate_verdict(0.5, 1.0, 0.0, 0.0) == "fail"

    def test_gate_mcp_consistent(self):
        from ugh_audit.server import _gate_verdict as srv
        from ugh_audit.mcp_server import _gate_verdict as mcp
        for args in [(0.0, 0.0, 0.0, 0.0), (0.5, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)]:
            assert srv(*args) == mcp(*args)


# --- API 出力フォーマットテスト ---

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from ugh_audit.server import app, configure  # noqa: E402
from ugh_audit.storage.audit_db import AuditDB  # noqa: E402
from ugh_audit.reference.golden_store import GoldenStore  # noqa: E402


class TestAPIOutput:
    """REST API の出力フォーマットテスト"""

    @pytest.fixture
    def client(self, tmp_path):
        db = AuditDB(db_path=tmp_path / "test.db")
        golden = GoldenStore(path=tmp_path / "golden.json")
        configure(db=db, golden=golden)
        with TestClient(app) as c:
            yield c
        configure(db=None, golden=None)

    def test_audit_output_has_all_fields(self, client):
        resp = client.post("/api/audit", json={
            "question": "テスト質問",
            "response": "テスト回答",
        })
        assert resp.status_code == 200
        data = resp.json()

        # 新仕様の全フィールドが存在する
        assert "S" in data
        assert "C" in data
        assert "delta_e" in data
        assert "quality_score" in data
        assert "verdict" in data
        assert "hit_rate" in data
        assert "structural_gate" in data
        assert "saved_id" in data
        assert "schema_version" in data
        assert "mode" in data
        assert "computed_components" in data
        assert "missing_components" in data
        assert "errors" in data

        # 旧フィールドが存在しない
        assert "por" not in data
        assert "grv" not in data
        assert "meaning_drift" not in data

    def test_audit_output_degraded_without_meta(self, client):
        """question_meta なし → degraded モード"""
        resp = client.post("/api/audit", json={
            "question": "テスト質問",
            "response": "テスト回答",
        })
        data = resp.json()

        assert isinstance(data["S"], float)
        assert data["C"] is None
        assert data["delta_e"] is None
        assert data["quality_score"] is None
        assert data["verdict"] == "degraded"
        assert data["mode"] == "degraded"
        assert data["hit_rate"] is None
        assert isinstance(data["structural_gate"], dict)
        assert data["saved_id"] is None
        assert data["schema_version"] == "2.0.0"

    def test_structural_gate_fields(self, client):
        resp = client.post("/api/audit", json={
            "question": "テスト質問",
            "response": "テスト回答",
        })
        gate = resp.json()["structural_gate"]

        assert "f1" in gate
        assert "f2" in gate
        assert "f3" in gate
        assert "f4" in gate
        assert "gate_verdict" in gate
        assert "primary_fail" in gate

    def test_history_output_has_new_fields(self, client):
        client.post("/api/audit", json={
            "question": "Q", "response": "R",
            "question_meta": {
                "question": "Q",
                "core_propositions": ["命題"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "metric_omnipotence",
            },
        })
        resp = client.get("/api/history")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1

        item = items[0]
        assert "S" in item
        assert "C" in item
        assert "delta_e" in item
        assert "quality_score" in item
        assert "verdict" in item

        # 旧フィールドが存在しない
        assert "por" not in item
        assert "meaning_drift" not in item
