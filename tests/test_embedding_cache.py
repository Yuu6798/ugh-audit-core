"""tests/test_embedding_cache.py — 永続埋め込みキャッシュテスト

SBert 本体を必要としない純粋なキャッシュレイヤーの単体テスト。
実モデルを使わずに numpy 直返しの fake encoder で挙動検証する。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import cascade_matcher


class _FakeModel:
    """SBert 互換の最小 fake encoder。

    各テキストを文字列ハッシュから決定的に 8 次元ベクトル化し、
    呼び出し履歴で実際にエンコードが走ったテキストを追跡する。
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts, batch_size=64, convert_to_numpy=True):
        self.calls.append(list(texts))
        rng = np.random.default_rng(42)
        base = rng.standard_normal((len(texts), 8)).astype(np.float32)
        # テキスト依存性を持たせる（同一テキスト→同一ベクトル）
        for i, t in enumerate(texts):
            seed = abs(hash(t)) % (2**31)
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
