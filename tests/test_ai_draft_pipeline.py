"""
tests/test_ai_draft_pipeline.py
computed_ai_draft mode + soft_rescue の統合テスト
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ugh_calculator import VALID_MODES, derive_mode, State  # noqa: E402

# --- derive_mode テスト ---


class TestDeriveMode:
    def test_default_inline(self):
        state = State(S=1.0, C=1.0, delta_e=0.0, quality_score=5.0,
                      delta_e_bin=1, C_bin=3, por_state="inactive", grv_tag="none")
        assert derive_mode(state) == "computed"

    def test_llm_generated(self):
        state = State(S=1.0, C=1.0, delta_e=0.0, quality_score=5.0,
                      delta_e_bin=1, C_bin=3, por_state="inactive", grv_tag="none")
        assert derive_mode(state, metadata_source="llm_generated") == "computed_ai_draft"

    def test_degraded_overrides_metadata_source(self):
        state = State(S=1.0, C=None, delta_e=None, quality_score=None,
                      delta_e_bin=0, C_bin=0, por_state="inactive", grv_tag="none")
        assert derive_mode(state, metadata_source="llm_generated") == "degraded"

    def test_computed_ai_draft_in_valid_modes(self):
        assert "computed_ai_draft" in VALID_MODES


# --- REST API 統合テスト ---

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


class TestComputedAiDraftPipeline:
    """REST API 経由の computed_ai_draft モード検証"""

    def test_inline_meta_stays_computed(self, client):
        """inline メタデータでは mode=computed のまま"""
        resp = client.post("/api/audit", json={
            "question": "PoRとは何ですか？",
            "response": "PoRは問いの核心に対する回答の位置座標です。",
            "question_meta": {
                "question": "PoRとは何ですか？",
                "core_propositions": ["PoRは回答の位置座標である"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "none",
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "computed"
        assert data["soft_rescue"] is None

    def test_degraded_without_meta(self, client):
        """メタデータなしでは mode=degraded"""
        resp = client.post("/api/audit", json={
            "question": "テスト",
            "response": "テスト回答",
        })
        data = resp.json()
        assert data["mode"] == "degraded"
