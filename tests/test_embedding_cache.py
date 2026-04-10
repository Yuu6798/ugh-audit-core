"""tests/test_embedding_cache.py — 永続埋め込みキャッシュテスト

SBert 本体を必要としない純粋なキャッシュレイヤーの単体テスト。
実モデルを使わずに numpy 直返しの fake encoder で挙動検証する。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import cascade_matcher


class _FakeConfig:
    def __init__(self, name: str) -> None:
        self._name_or_path = name


class _FakeAutoModel:
    def __init__(self, name: str) -> None:
        self.config = _FakeConfig(name)


class _FakeFirstModule:
    def __init__(self, name: str) -> None:
        self.auto_model = _FakeAutoModel(name)


class _FakeModel:
    """SBert 互換の最小 fake encoder。

    各テキストを文字列ハッシュから決定的に 8 次元ベクトル化し、
    呼び出し履歴で実際にエンコードが走ったテキストを追跡する。
    `identity` を渡すと `_infer_model_id` がそれを検出できるよう
    最小限の属性構造を持つ（_first_module().auto_model.config._name_or_path）。
    """

    def __init__(self, identity: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self._identity = identity
        self._fake_first_module = (
            _FakeFirstModule(identity) if identity is not None else None
        )

    def _first_module(self):
        if self._fake_first_module is None:
            raise AttributeError("no first module")
        return self._fake_first_module

    def encode(self, texts, batch_size=64, convert_to_numpy=True):
        self.calls.append(list(texts))
        rng = np.random.default_rng(42)
        base = rng.standard_normal((len(texts), 8)).astype(np.float32)
        # テキスト依存性 + モデル識別性を持たせる（= 別モデルは別ベクトル）
        model_seed = abs(hash(self._identity or type(self).__name__))
        for i, t in enumerate(texts):
            seed = (abs(hash(t)) ^ model_seed) % (2**31)
            r = np.random.default_rng(seed)
            base[i] = r.standard_normal(8).astype(np.float32)
        return base


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """キャッシュをテスト用の一時ディレクトリに隔離し、状態をリセットする。"""
    cache_path = tmp_path / "embedding_cache.npz"
    monkeypatch.setattr(cascade_matcher, "_EMBED_CACHE_PATH", cache_path)
    monkeypatch.setattr(cascade_matcher, "_CACHE_DISABLED", False)
    cascade_matcher.clear_embedding_cache()
    yield cache_path
    cascade_matcher.clear_embedding_cache()


def test_cache_miss_then_hit(isolated_cache):
    """1 回目は encode、2 回目は cache hit で encode されない。"""
    fake = _FakeModel()
    texts = ["富士山は高い", "海は青い"]

    v1 = cascade_matcher.encode_texts_cached(fake, texts, model_name="fake-model")
    assert v1.shape == (2, 8)
    assert len(fake.calls) == 1
    assert fake.calls[0] == texts

    v2 = cascade_matcher.encode_texts_cached(fake, texts, model_name="fake-model")
    # 2 回目は encode が走らない
    assert len(fake.calls) == 1
    # ベクトルは一致
    np.testing.assert_array_equal(v1, v2)


def test_partial_cache_hit(isolated_cache):
    """既存 + 新規が混ざった場合、新規分のみ encode される。"""
    fake = _FakeModel()

    cascade_matcher.encode_texts_cached(fake, ["A", "B"], model_name="m")
    assert fake.calls[-1] == ["A", "B"]

    # "A" は既にキャッシュ済み、"C" のみ新規
    cascade_matcher.encode_texts_cached(fake, ["A", "C"], model_name="m")
    assert fake.calls[-1] == ["C"]


def test_order_preservation(isolated_cache):
    """戻り値の順序は入力テキストの順序と一致する。"""
    fake = _FakeModel()
    texts = ["x", "y", "z"]

    v_all = cascade_matcher.encode_texts_cached(fake, texts, model_name="m")

    # 各テキストを単独でエンコードした結果と等しい並びであること
    for i, t in enumerate(texts):
        v_single = cascade_matcher.encode_texts_cached(fake, [t], model_name="m")
        np.testing.assert_allclose(v_all[i], v_single[0])


def test_model_name_isolation(isolated_cache):
    """同一テキストでもモデル名が違えば別キー扱い。"""
    fake_a = _FakeModel()
    fake_b = _FakeModel()

    cascade_matcher.encode_texts_cached(fake_a, ["shared"], model_name="model-a")
    cascade_matcher.encode_texts_cached(fake_b, ["shared"], model_name="model-b")

    assert len(fake_a.calls) == 1
    assert len(fake_b.calls) == 1
    assert fake_a.calls[0] == ["shared"]
    assert fake_b.calls[0] == ["shared"]


def test_persistence_across_reload(isolated_cache, tmp_path):
    """flush_embedding_cache でディスク保存 → clear → 再読み込みでヒット。"""
    fake = _FakeModel()
    cascade_matcher.encode_texts_cached(fake, ["persist_me"], model_name="m")
    cascade_matcher.flush_embedding_cache()

    assert isolated_cache.exists()

    # メモリキャッシュをクリアして再ロード
    cascade_matcher.clear_embedding_cache()

    cascade_matcher.encode_texts_cached(fake, ["persist_me"], model_name="m")
    # 再ロード後も encode が再走しない
    assert len(fake.calls) == 1


def test_disabled_cache_bypasses(isolated_cache, monkeypatch):
    """UGH_AUDIT_EMBED_CACHE_DISABLE 相当のフラグで常に encode が走る。"""
    monkeypatch.setattr(cascade_matcher, "_CACHE_DISABLED", True)
    cascade_matcher.clear_embedding_cache()
    fake = _FakeModel()

    cascade_matcher.encode_texts_cached(fake, ["a"], model_name="m")
    cascade_matcher.encode_texts_cached(fake, ["a"], model_name="m")
    assert len(fake.calls) == 2


def test_empty_input(isolated_cache):
    fake = _FakeModel()
    result = cascade_matcher.encode_texts_cached(fake, [], model_name="m")
    assert result.shape == (0, 0)
    assert fake.calls == []


def test_make_cache_key_stability():
    """同一入力は常に同一キーを生成する（ハッシュ安定性）。"""
    k1 = cascade_matcher._make_cache_key("同じテキスト", "model-x")
    k2 = cascade_matcher._make_cache_key("同じテキスト", "model-x")
    assert k1 == k2
    assert len(k1) == 24

    # モデル名が違えば違うキー
    k3 = cascade_matcher._make_cache_key("同じテキスト", "model-y")
    assert k1 != k3


def test_stats_reports_state(isolated_cache):
    fake = _FakeModel()
    stats0 = cascade_matcher.embedding_cache_stats()
    assert stats0["entries"] == 0

    cascade_matcher.encode_texts_cached(fake, ["a", "b", "c"], model_name="m")
    stats1 = cascade_matcher.embedding_cache_stats()
    assert stats1["entries"] == 3
    assert stats1["dirty"] == 1


def test_corrupted_cache_file_recovers(isolated_cache):
    """破損した .npz があっても空スタートで処理継続する。"""
    isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    Path(isolated_cache).write_bytes(b"not a valid npz file")
    cascade_matcher.clear_embedding_cache()

    fake = _FakeModel()
    v = cascade_matcher.encode_texts_cached(fake, ["recover"], model_name="m")
    assert v.shape == (1, 8)
    assert len(fake.calls) == 1


# ============================================================
# モデル識別子の auto-inference（Codex review r3067060572 対応）
# ============================================================


def test_infer_model_id_from_config_name_or_path():
    """SBert config._name_or_path から識別子を抽出できる。"""
    fake = _FakeModel(identity="paraphrase-multilingual-MiniLM-L12-v2")
    assert cascade_matcher._infer_model_id(fake) == (
        "paraphrase-multilingual-MiniLM-L12-v2"
    )


def test_infer_model_id_fallback_to_class_name():
    """属性が取れないモデルはクラス名ベースの識別子にフォールバックする。

    デフォルト MODEL_NAME にフォールバックすると別モデルの埋め込みが
    silent に共存して誤 cosine スコアを生むため、明示的に unknown を返す。
    """
    fake = _FakeModel(identity=None)  # _first_module が AttributeError を投げる
    inferred = cascade_matcher._infer_model_id(fake)
    assert inferred.startswith("unknown-model:")
    assert "MiniLM" not in inferred
    assert inferred != cascade_matcher.MODEL_NAME


def test_encode_texts_cached_auto_infers_model_id(isolated_cache):
    """model_name=None 時に auto-inference が有効化される。"""
    fake = _FakeModel(identity="model-alpha")

    cascade_matcher.encode_texts_cached(fake, ["hello"])  # model_name 省略
    # 推論された ID ("model-alpha") でキャッシュキーが作られているはず
    expected_key = cascade_matcher._make_cache_key("hello", "model-alpha")
    assert expected_key in cascade_matcher._embedding_cache


def test_different_models_do_not_share_cache_via_tier2_candidate(isolated_cache):
    """同一テキストでも別モデルなら別キャッシュエントリ（Codex 指摘の再現テスト）。

    修正前: tier2_candidate → encode_texts_cached(model, texts) が常に
    default MODEL_NAME で keying していたため、2 つ目のモデルでエンコード
    した結果が 1 つ目のモデルのキャッシュから silent に再利用されていた。

    修正後: モデルごとに identity が推論され、別キーになる。
    """
    fake_a = _FakeModel(identity="model-alpha")
    fake_b = _FakeModel(identity="model-beta")

    v_a = cascade_matcher.encode_texts_cached(fake_a, ["共通テキスト"])
    v_b = cascade_matcher.encode_texts_cached(fake_b, ["共通テキスト"])

    # 両方とも encode が走る（silent hit が起きない）
    assert len(fake_a.calls) == 1
    assert len(fake_b.calls) == 1

    # 生成されたベクトルは別物（同じ値が silent に返っていない）
    assert not np.allclose(v_a, v_b)

    # キャッシュには 2 エントリが入る
    assert len(cascade_matcher._embedding_cache) == 2


def test_explicit_model_name_overrides_inference(isolated_cache):
    """明示的な model_name は auto-inference を上書きする。"""
    fake = _FakeModel(identity="real-model")

    cascade_matcher.encode_texts_cached(fake, ["x"], model_name="override")
    override_key = cascade_matcher._make_cache_key("x", "override")
    inferred_key = cascade_matcher._make_cache_key("x", "real-model")

    assert override_key in cascade_matcher._embedding_cache
    assert inferred_key not in cascade_matcher._embedding_cache


# ============================================================
# 容量上限 (Codex review r3067115341 対応)
# ============================================================


def test_cache_respects_capacity_cap(isolated_cache, monkeypatch):
    """_MAX_CACHE_ENTRIES を超えたら新規エントリを永続化しない。

    ただしベクトル自体は返却されるため呼び出し側の挙動は変わらない。
    """
    monkeypatch.setattr(cascade_matcher, "_MAX_CACHE_ENTRIES", 3)
    fake = _FakeModel(identity="m")

    v = cascade_matcher.encode_texts_cached(
        fake, ["t1", "t2", "t3", "t4", "t5"], model_name="m"
    )

    # 戻り値には 5 件すべてのベクトルが含まれる
    assert v.shape == (5, 8)
    # だが cache には 3 件（上限）しか入らない
    assert len(cascade_matcher._embedding_cache) == 3


def test_capacity_cap_subsequent_call_still_returns_uncached_vectors(
    isolated_cache, monkeypatch
):
    """cap 超過後も同テキストは毎回 encode される（cache miss 扱い）。"""
    monkeypatch.setattr(cascade_matcher, "_MAX_CACHE_ENTRIES", 1)
    fake = _FakeModel(identity="m")

    # 1 回目: t1 のみ cache に入る、t2 は cap 超過で持続化されない
    v1 = cascade_matcher.encode_texts_cached(fake, ["t1", "t2"], model_name="m")
    assert len(cascade_matcher._embedding_cache) == 1
    first_calls = len(fake.calls)

    # 2 回目: t2 は cache ミス → 再 encode される
    v2 = cascade_matcher.encode_texts_cached(fake, ["t1", "t2"], model_name="m")
    assert len(fake.calls) == first_calls + 1  # t2 だけ再 encode
    # t1 は hit するので再 encode されない
    assert fake.calls[-1] == ["t2"]

    # ベクトルは一貫（同じテキスト → 同じベクトル）
    np.testing.assert_allclose(v1[0], v2[0])
    np.testing.assert_allclose(v1[1], v2[1])


# ============================================================
# tier2_candidate: proposition のみキャッシュ、segments は bypass
# (Codex review r3067115341 対応)
# ============================================================


def test_tier2_candidate_only_caches_proposition(isolated_cache):
    """tier2_candidate はリファレンス命題のみキャッシュし、
    response セグメントはキャッシュしない。

    これにより AI回答ごとに一意な segment 群でキャッシュが単調増加するのを防ぐ。
    """
    fake = _FakeModel(identity="cached-test-model")
    proposition = "リファレンス命題A"
    response = "セグメント1です。セグメント2です。セグメント3です。"

    cascade_matcher.tier2_candidate(proposition, response, fake)

    # cache には proposition 1 エントリのみ
    assert len(cascade_matcher._embedding_cache) == 1
    prop_key = cascade_matcher._make_cache_key(proposition, "cached-test-model")
    assert prop_key in cascade_matcher._embedding_cache

    # セグメントは cache に入っていない
    for seg in ["セグメント1です", "セグメント2です", "セグメント3です"]:
        seg_key = cascade_matcher._make_cache_key(seg, "cached-test-model")
        assert seg_key not in cascade_matcher._embedding_cache


def test_tier2_candidate_reuses_cached_proposition_across_responses(isolated_cache):
    """同じ proposition で別 response を評価すると、prop は cache hit で
    再 encode されず、セグメントのみ毎回 encode される。"""
    fake = _FakeModel(identity="cached-test-model")
    proposition = "共通リファレンス命題"

    cascade_matcher.tier2_candidate(proposition, "回答A1です。回答A2です。", fake)
    # 初回: prop encode (1 call) + segs encode (1 call) = 2 calls
    first_calls_count = len(fake.calls)
    assert first_calls_count == 2

    cascade_matcher.tier2_candidate(proposition, "回答B1です。回答B2です。", fake)
    # 2 回目: prop は hit でスキップ、segs だけ encode = +1 call
    assert len(fake.calls) == first_calls_count + 1

    # 最後の call には proposition が含まれていない（segments のみ）
    last_call = fake.calls[-1]
    assert proposition not in last_call
    assert all("回答B" in t for t in last_call)


def test_tier2_candidate_segments_do_not_fill_cache_over_runs(isolated_cache):
    """複数回の tier2_candidate 呼び出しで cache サイズは
    proposition 数と同数に収束し、セグメント数では増えない。"""
    fake = _FakeModel(identity="cached-test-model")

    responses = [
        "応答Aの第一文。応答Aの第二文。応答Aの第三文。",
        "応答Bの第一文。応答Bの第二文。応答Bの第三文。",
        "応答Cの第一文。応答Cの第二文。応答Cの第三文。",
    ]
    for resp in responses:
        cascade_matcher.tier2_candidate("命題X", resp, fake)

    # 命題は 1 つだけなので cache サイズも 1
    assert len(cascade_matcher._embedding_cache) == 1
