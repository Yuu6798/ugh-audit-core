"""
tests/test_golden_store.py
GoldenStore の日本語マッチングテスト
"""
from __future__ import annotations

import numpy as np
import pytest

import cascade_matcher
from ugh_audit.reference import golden_store as gs_module
from ugh_audit.reference.golden_store import GoldenEntry, GoldenStore


def _empty_store(tmp_path) -> GoldenStore:
    """初期データなしの空ストアを返す"""
    store = GoldenStore(path=tmp_path / "golden.json")
    # 初期データをクリアしてテスト用エントリのみにする
    store._store.clear()
    return store


def test_find_reference_exact_substring(tmp_path):
    store = _empty_store(tmp_path)
    store.add("test", GoldenEntry(
        question="AIは意味を持てるか",
        reference="AIは意味と共振する",
        source="test",
    ))
    # 部分一致
    ref = store.find_reference("AIは意味を持てるか？という問い")
    assert ref == "AIは意味と共振する"


def test_find_reference_bigram(tmp_path):
    store = _empty_store(tmp_path)
    store.add("por", GoldenEntry(
        question="PoRとは何か",
        reference="意味の発火点",
        source="test",
    ))
    # bigram マッチング
    ref = store.find_reference("PoRとは何ですか")
    assert ref == "意味の発火点"


def test_find_reference_no_match(tmp_path):
    store = _empty_store(tmp_path)
    # 空ストアでは完全に無関係な質問は None
    ref = store.find_reference("全く関係のない質問xyz")
    assert ref is None


def test_find_reference_empty(tmp_path):
    store = _empty_store(tmp_path)
    assert store.find_reference("") is None


# ============================================================
# Stage 3: SBert rerank テスト
# ============================================================
#
# 実 SBert は heavyweight なので、cascade_matcher の get_shared_model /
# encode_texts_cached を monkeypatch し、質問文字列から決定的に生成した
# 擬似ベクトルで再スコアロジックを検証する。


class _ScriptedModel:
    """cascade_matcher の encode 互換のダミーモデル。"""

    def encode(self, texts, batch_size=64, convert_to_numpy=True):
        # このモデルは encode_texts_cached のモンキーパッチで直接バイパス
        # されるため、実際には呼ばれない。
        raise RuntimeError("should not be called")


@pytest.fixture
def scripted_rerank(monkeypatch):
    """SBert をスクリプト化ベクトルで差し替えるフィクスチャ。

    caller は `set_vectors({text: np.ndarray, ...})` を呼んで
    「この質問テキスト → このベクトル」という固定マッピングを登録する。

    `encode_texts`（query 用、cache bypass）と `encode_texts_cached`
    （entry.question 用、cache 経由）の両方をスタブする。
    """
    vector_map: dict[str, np.ndarray] = {}

    def _set_vectors(mapping: dict[str, np.ndarray]) -> None:
        vector_map.clear()
        vector_map.update(mapping)

    fake_model = _ScriptedModel()

    def _fake_get_shared_model():
        return fake_model

    def _fake_lookup(texts):
        out = []
        for t in texts:
            if t not in vector_map:
                raise KeyError(f"unregistered text in scripted rerank: {t!r}")
            out.append(vector_map[t])
        return np.stack(out).astype(np.float32)

    def _fake_encode_texts(model, texts, batch_size=64):
        return _fake_lookup(texts)

    def _fake_encode_texts_cached(model, texts, model_name=None, batch_size=64):
        return _fake_lookup(texts)

    monkeypatch.setattr(cascade_matcher, "get_shared_model", _fake_get_shared_model)
    monkeypatch.setattr(cascade_matcher, "encode_texts", _fake_encode_texts)
    monkeypatch.setattr(
        cascade_matcher, "encode_texts_cached", _fake_encode_texts_cached
    )

    yield _set_vectors


def _unit(vec):
    v = np.asarray(vec, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-10)


def test_sbert_rerank_picks_semantic_top1_over_bigram(tmp_path, scripted_rerank):
    """bigram top1 と SBert top1 が異なる場合、gap 十分なら SBert が勝つ。"""
    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="PoRとは何か",
        reference="ref_A",
        source="test",
    ))
    store.add("b", GoldenEntry(
        question="PoRの定義",
        reference="ref_B",
        source="test",
    ))

    # query は両 entry と bigram 重複があるが "PoRとは何か" の方が高い
    # (bigram: Po, oR, Rな, なる, る何, 何か → entry a と overlap 3、b と overlap 2)
    query = "PoRなる何か"

    # SBert 上は b が明確な top1（cos ≈ 0.994）、a は直交（cos ≈ 0）
    scripted_rerank({
        query:        _unit([1.0, 0.0, 0.0]),
        "PoRとは何か": _unit([0.0, 1.0, 0.0]),
        "PoRの定義":   _unit([0.95, 0.1, 0.0]),
    })

    result = store.find_reference(query)
    # bigram top1 は a だが、SBert で b が明確に top1 → gap 十分で ref_B
    assert result == "ref_B"


def test_sbert_rerank_gap_insufficient_falls_back_to_bigram(tmp_path, scripted_rerank):
    """SBert top1 と top2 の差が小さい場合、bigram top1 にフォールバック。"""
    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="PoRとは何か",
        reference="ref_A_bigram_top",
        source="test",
    ))
    store.add("b", GoldenEntry(
        question="PoRの定義",
        reference="ref_B",
        source="test",
    ))

    query = "PoRなる何か"  # bigram で a が top（overlap 3 vs 2）

    # SBert 上は a と b が僅差（gap < 0.04 かつ top1 < 0.70）
    scripted_rerank({
        query:        _unit([1.0, 0.0, 0.0]),
        "PoRとは何か": _unit([0.55, 0.83, 0.0]),   # cos≈0.55
        "PoRの定義":   _unit([0.54, 0.84, 0.0]),   # cos≈0.54 (gap≈0.01)
    })

    result = store.find_reference(query)
    # gap 不足 → bigram top1 (a) へフォールバック
    assert result == "ref_A_bigram_top"


def test_sbert_rerank_high_score_relaxed_gap(tmp_path, scripted_rerank):
    """top1_score > 0.70 の時は gap ≥ 0.02 で通過。"""
    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="PoRとは何か",
        reference="ref_A",
        source="test",
    ))
    store.add("b", GoldenEntry(
        question="PoRの定義",
        reference="ref_B",
        source="test",
    ))

    query = "PoRなる何か"

    # top1 ≈ 0.80, top2 ≈ 0.77 → gap 0.03（通常 δ 0.04 では fail、緩和 δ 0.02 で pass）
    scripted_rerank({
        query:        _unit([1.0, 0.0, 0.0]),
        "PoRとは何か": _unit([0.80, 0.60, 0.0]),
        "PoRの定義":   _unit([0.77, 0.638, 0.0]),
    })

    result = store.find_reference(query)
    # 緩和閾値 (0.02) で top1=a が採用される
    assert result == "ref_A"


def test_rerank_disabled_preserves_bigram_behavior(tmp_path, scripted_rerank):
    """use_sbert_rerank=False で Stage 2 の挙動に固定される。"""
    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="PoRとは何か",
        reference="ref_A",
        source="test",
    ))
    store.add("b", GoldenEntry(
        question="PoRの定義",
        reference="ref_B",
        source="test",
    ))

    # scripted_rerank は登録しないので、呼ばれたら KeyError になる
    # （= rerank=False で SBert 経路が走らないことを同時に検証）
    result = store.find_reference("PoRなる何か", use_sbert_rerank=False)
    # bigram top1 （"PoRとは何か"）が返る
    assert result == "ref_A"


def test_find_reference_detailed_reports_stage(tmp_path, scripted_rerank):
    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="PoRとは何か",
        reference="ref_A",
        source="test",
    ))
    store.add("b", GoldenEntry(
        question="PoRの定義",
        reference="ref_B",
        source="test",
    ))

    query = "PoRなる何か"
    scripted_rerank({
        query:        _unit([1.0, 0.0, 0.0]),
        "PoRとは何か": _unit([0.1, 0.99, 0.0]),
        "PoRの定義":   _unit([0.95, 0.2, 0.0]),
    })

    detail = store.find_reference_detailed(query)
    assert detail is not None
    assert detail["stage"] == "sbert_rerank"
    assert detail["confidence"] == "high"
    assert detail["reference"] == "ref_B"
    assert detail["sbert_top1_score"] > detail["sbert_gap"]


def test_find_reference_detailed_direct_match(tmp_path):
    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="AIは意味を持てるか",
        reference="ref_A",
        source="test",
    ))
    detail = store.find_reference_detailed("AIは意味を持てるか？という問い")
    assert detail is not None
    assert detail["stage"] == "direct"
    assert detail["confidence"] == "high"
    assert detail["reference"] == "ref_A"


def test_find_reference_single_candidate_no_rerank_needed(tmp_path, scripted_rerank):
    """候補 1 件のみの場合、SBert 再スコアを呼ばず bigram top1 を返す。"""
    store = _empty_store(tmp_path)
    store.add("only", GoldenEntry(
        question="一意な質問テキスト",
        reference="ref_only",
        source="test",
    ))
    # scripted_rerank は登録しないので、SBert が呼ばれると KeyError
    result = store.find_reference("一意な質問")
    assert result == "ref_only"


def test_bigram_candidates_respects_top_k(tmp_path):
    """bigram 候補プールは top-K で打ち切られる。"""
    store = _empty_store(tmp_path)
    for i in range(10):
        store.add(f"e{i}", GoldenEntry(
            question=f"共通プレフィックス_エントリ_{i}",
            reference=f"ref_{i}",
            source="test",
        ))

    candidates = store._bigram_candidates("共通プレフィックス")
    assert len(candidates) <= gs_module._BIGRAM_CANDIDATE_TOP_K
    # スコア降順で並んでいる
    scores = [c[0] for c in candidates]
    assert scores == sorted(scores, reverse=True)


# ============================================================
# Codex review r3067133071 対応:
# _sbert_rerank は entry.question のみキャッシュし、query は bypass
# ============================================================


class _RerankFakeModel:
    """cascade_matcher の _infer_model_id から identity を拾える最小 fake。"""

    def __init__(self, identity: str) -> None:
        self.calls: list[list[str]] = []

        class _Config:
            _name_or_path = identity

        class _AutoModel:
            config = _Config()

        class _FirstModule:
            auto_model = _AutoModel()

        self._first = _FirstModule()

    def _first_module(self):
        return self._first

    def encode(self, texts, batch_size=64, convert_to_numpy=True):
        self.calls.append(list(texts))
        # 各テキストを決定的な 8 次元ベクトルに写像
        out = np.zeros((len(texts), 8), dtype=np.float32)
        for i, t in enumerate(texts):
            rng = np.random.default_rng(abs(hash(t)) % (2**31))
            out[i] = rng.standard_normal(8).astype(np.float32)
        return out


def test_sbert_rerank_recovers_from_stale_cache_dim(tmp_path, monkeypatch):
    """GoldenStore._sbert_rerank も stale cache dim から gracefully recover する。
    Codex review #60 r3067145596 の対応を golden_store 側で検証。
    """
    import cascade_matcher

    cache_path = tmp_path / "embedding_cache.npz"
    monkeypatch.setattr(cascade_matcher, "_EMBED_CACHE_PATH", cache_path)
    monkeypatch.setattr(cascade_matcher, "_CACHE_DISABLED", False)
    cascade_matcher.clear_embedding_cache()

    fake = _RerankFakeModel(identity="rerank-test-model")
    monkeypatch.setattr(cascade_matcher, "get_shared_model", lambda: fake)

    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="PoRとは何か",
        reference="ref_A",
        source="test",
    ))
    store.add("b", GoldenEntry(
        question="PoRの定義",
        reference="ref_B",
        source="test",
    ))

    # Stale な 16 次元のエントリで cache を汚染（モデル重み更新後を模倣）
    cascade_matcher._load_embedding_cache()
    for q in ["PoRとは何か", "PoRの定義"]:
        key = cascade_matcher._make_cache_key(q, "rerank-test-model")
        cascade_matcher._embedding_cache[key] = np.zeros(16, dtype=np.float32)
    cascade_matcher._cache_embed_dim = 16

    # fake は 8 次元を返す → 次元不一致を検出 → invalidate → re-encode
    result = store.find_reference("PoRなる何か")

    # 例外なく結果が返る（ref_A or ref_B のいずれか）
    assert result in ("ref_A", "ref_B")

    # cache は 8 次元で refresh されている
    for q in ["PoRとは何か", "PoRの定義"]:
        key = cascade_matcher._make_cache_key(q, "rerank-test-model")
        assert key in cascade_matcher._embedding_cache
        assert cascade_matcher._embedding_cache[key].shape == (8,)
    assert cascade_matcher._cache_embed_dim == 8

    cascade_matcher.clear_embedding_cache()


def test_sbert_rerank_does_not_cache_user_query(tmp_path, monkeypatch):
    """find_reference が呼ばれても、ユーザークエリ自体は cache に入らない。

    entry.question（リファレンス）は cache に入る。
    Codex review r3067133071 の回帰テスト。
    """
    import cascade_matcher

    # キャッシュを隔離
    cache_path = tmp_path / "embedding_cache.npz"
    monkeypatch.setattr(cascade_matcher, "_EMBED_CACHE_PATH", cache_path)
    monkeypatch.setattr(cascade_matcher, "_CACHE_DISABLED", False)
    cascade_matcher.clear_embedding_cache()

    # 本物の SBert ではなく fake をシングルトンとして差し込む
    fake = _RerankFakeModel(identity="rerank-test-model")
    monkeypatch.setattr(cascade_matcher, "get_shared_model", lambda: fake)

    store = _empty_store(tmp_path)
    store.add("a", GoldenEntry(
        question="PoRとは何か",
        reference="ref_A",
        source="test",
    ))
    store.add("b", GoldenEntry(
        question="PoRの定義",
        reference="ref_B",
        source="test",
    ))

    unique_query = "PoRなる何か"
    # 2 件候補が取れて Stage 3 が走るクエリ
    store.find_reference(unique_query)

    # query 自体は cache に入っていない
    query_key = cascade_matcher._make_cache_key(
        unique_query, "rerank-test-model"
    )
    assert query_key not in cascade_matcher._embedding_cache

    # entry.question は cache に入っている
    entry_a_key = cascade_matcher._make_cache_key(
        "PoRとは何か", "rerank-test-model"
    )
    entry_b_key = cascade_matcher._make_cache_key(
        "PoRの定義", "rerank-test-model"
    )
    assert entry_a_key in cascade_matcher._embedding_cache
    assert entry_b_key in cascade_matcher._embedding_cache


# --- seed loading (P2-8) ---

def test_seed_loaded_from_json_on_first_init(tmp_path):
    """新規 store 初期化時、既定 seed JSON (examples/seed_references.json)

    の 3 エントリがロードされる。
    """
    store = GoldenStore(path=tmp_path / "golden.json")
    keys = store.list_keys()
    assert "ugh_definition" in keys
    assert "por_definition" in keys
    assert "delta_e_definition" in keys

    por = store.get("por_definition")
    assert por is not None
    assert por.question == "PoRとは何か？"
    assert por.por_floor == 0.82
    assert por.delta_e_ceiling == 0.05


def test_custom_seed_path(tmp_path):
    """seed_path 引数で別の JSON を seed として指定できる"""
    seed = tmp_path / "custom_seed.json"
    seed.write_text(
        '{"custom_key": {"question": "q", "reference": "r", "source": "s"}}',
        encoding="utf-8",
    )
    store = GoldenStore(
        path=tmp_path / "golden.json",
        seed_path=seed,
    )
    assert store.list_keys() == ["custom_key"]
    entry = store.get("custom_key")
    assert entry.reference == "r"


def test_missing_seed_graceful_fallback(tmp_path):
    """seed JSON が存在しない場合は空 store で起動し crash しない"""
    missing = tmp_path / "does_not_exist.json"
    store = GoldenStore(
        path=tmp_path / "golden.json",
        seed_path=missing,
    )
    assert store.list_keys() == []
    # find も None を返す (既存 _empty_store パターンと同じ挙動)
    assert store.find_reference("何か") is None

    # 別のユニーククエリで再呼び出ししても cache サイズは増えない
    # （entry は既に cache hit、query は bypass）
    initial_cache_size = len(cascade_matcher._embedding_cache)
    store.find_reference("PoRあれこれ疑問")
    assert len(cascade_matcher._embedding_cache) == initial_cache_size

    cascade_matcher.clear_embedding_cache()
