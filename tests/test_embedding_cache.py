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


# ============================================================
# Stale cache dim recovery (Codex review r3067145596 対応)
# ============================================================


def test_invalidate_embedding_cache_clears_state(isolated_cache):
    """invalidate_embedding_cache がメモリ cache と dim tracker をクリアする。"""
    fake = _FakeModel(identity="m")
    cascade_matcher.encode_texts_cached(fake, ["a", "b"], model_name="m")
    assert len(cascade_matcher._embedding_cache) == 2
    assert cascade_matcher._cache_embed_dim == 8

    cascade_matcher.invalidate_embedding_cache(reason="test")

    assert len(cascade_matcher._embedding_cache) == 0
    assert cascade_matcher._cache_embed_dim is None
    assert cascade_matcher._cache_dirty is True  # 空状態も persist 対象


def test_cache_embed_dim_tracked_on_new_entries(isolated_cache):
    """初回投入時にキャッシュの次元が記録される。"""
    fake = _FakeModel(identity="m")
    assert cascade_matcher._cache_embed_dim is None

    cascade_matcher.encode_texts_cached(fake, ["x"], model_name="m")
    assert cascade_matcher._cache_embed_dim == 8


def test_tier2_candidate_recovers_from_stale_cache_dim(isolated_cache):
    """異なる次元のベクトルが cache に残っていても、tier2_candidate は
    gracefully recover して正しい結果を返す（shape error を raise しない）。
    """
    proposition = "命題A"
    response = "セグメントAです。セグメントBです。セグメントCです。"

    # 初回エンコードで cache に proposition を投入（dim=8）
    fake_old = _FakeModel(identity="cached-test-model")
    cascade_matcher.tier2_candidate(proposition, response, fake_old)
    prop_key = cascade_matcher._make_cache_key(proposition, "cached-test-model")
    assert cascade_matcher._embedding_cache[prop_key].shape == (8,)

    # 意図的に stale な 16 次元ベクトルで proposition のエントリを上書き
    # （= モデル重み更新後の stale cache の状態を模倣）
    cascade_matcher._embedding_cache[prop_key] = np.zeros(16, dtype=np.float32)
    cascade_matcher._cache_embed_dim = 16

    # 新しいモデル（8 次元）で同じ proposition を tier2_candidate にかける。
    # invariant: shape mismatch を検出 → invalidate → re-encode → 正常完了
    fake_new = _FakeModel(identity="cached-test-model")
    result = cascade_matcher.tier2_candidate(proposition, response, fake_new)

    # shape error で abort していないこと
    assert "top1_score" in result
    assert "pass_tier2" in result
    assert isinstance(result["top1_score"], float)

    # キャッシュは新しい 8 次元でリフレッシュされている
    refreshed_key = cascade_matcher._make_cache_key(
        proposition, "cached-test-model"
    )
    assert refreshed_key in cascade_matcher._embedding_cache
    assert cascade_matcher._embedding_cache[refreshed_key].shape == (8,)
    assert cascade_matcher._cache_embed_dim == 8


def test_invalidate_empty_cache_deletes_stale_file_on_save(isolated_cache):
    """invalidate 後に新規追加がなくても、次の save で .npz が削除される。
    Codex review r3067162768 の回帰テスト。
    """
    fake = _FakeModel(identity="m")
    # 初回: cache にエントリを入れて disk に flush
    cascade_matcher.encode_texts_cached(fake, ["x", "y"], model_name="m")
    cascade_matcher.flush_embedding_cache()
    assert isolated_cache.exists()

    # invalidate: メモリ空 + dirty=True になる
    cascade_matcher.invalidate_embedding_cache(reason="test")
    assert len(cascade_matcher._embedding_cache) == 0
    assert cascade_matcher._cache_dirty is True

    # save: 空状態を persist → .npz 削除
    cascade_matcher._save_embedding_cache()
    assert not isolated_cache.exists()
    assert cascade_matcher._cache_dirty is False


def test_flush_before_lazy_load_preserves_disk_state(isolated_cache):
    """fresh process (lazy load 前) で flush_embedding_cache() を呼んでも、
    disk 上の既存エントリが保持される。Codex review r3067224... の回帰テスト。
    """
    # Phase 1: disk に既存エントリを書き出す
    fake = _FakeModel(identity="m")
    cascade_matcher.encode_texts_cached(
        fake, ["preserved_a", "preserved_b"], model_name="m"
    )
    cascade_matcher.flush_embedding_cache()
    assert isolated_cache.exists()

    # Phase 2: プロセス再起動を模倣: メモリ状態を完全リセット
    cascade_matcher.clear_embedding_cache()
    assert cascade_matcher._cache_loaded is False
    assert len(cascade_matcher._embedding_cache) == 0
    # disk file は残っている（前プロセスの成果物）
    assert isolated_cache.exists()

    # Phase 3: lazy load 前に flush を呼ぶ（診断・メンテ操作を想定）
    cascade_matcher.flush_embedding_cache()

    # 修正前: 空メモリを authoritative とみなして disk が unlink される
    # 修正後: lazy load で disk state を取り込んでから save → disk 維持
    assert isolated_cache.exists()

    # 再ロードして既存エントリが保持されていることを確認
    cascade_matcher.clear_embedding_cache()
    cascade_matcher._load_embedding_cache()
    key_a = cascade_matcher._make_cache_key("preserved_a", "m")
    key_b = cascade_matcher._make_cache_key("preserved_b", "m")
    assert key_a in cascade_matcher._embedding_cache
    assert key_b in cascade_matcher._embedding_cache


def test_invalidate_at_startup_purges_stale_disk_file(isolated_cache):
    """プロセス起動直後（lazy load 前）に invalidate を呼んでも、
    disk 上の stale .npz が次の save で削除される。
    Codex review r3067177175 の回帰テスト。
    """
    # Phase 1: 古い disk state を用意（前回実行時の残骸）
    fake = _FakeModel(identity="m")
    cascade_matcher.encode_texts_cached(fake, ["stale_a", "stale_b"], model_name="m")
    cascade_matcher.flush_embedding_cache()
    assert isolated_cache.exists()

    # Phase 2: プロセス再起動を模倣: メモリ状態を完全リセット
    cascade_matcher.clear_embedding_cache()
    assert len(cascade_matcher._embedding_cache) == 0
    assert cascade_matcher._cache_embed_dim is None
    assert cascade_matcher._cache_loaded is False
    # disk file は残っている（startup の状態）
    assert isolated_cache.exists()

    # Phase 3: 起動直後の手動 invalidate
    # 旧実装では n_cleared=0 && _cache_embed_dim=None で early return して
    # no-op だったが、修正後は disk をロードしてから purge されるべき
    cascade_matcher.invalidate_embedding_cache(reason="post-model-update")
    assert cascade_matcher._cache_dirty is True

    # Phase 4: save → disk file が削除される
    cascade_matcher._save_embedding_cache()
    assert not isolated_cache.exists()


def test_invalidate_is_noop_when_truly_empty(isolated_cache):
    """disk にもメモリにも何もない状態での invalidate は副作用なく no-op。"""
    # 初期状態: 何もない
    cascade_matcher.clear_embedding_cache()
    assert not isolated_cache.exists()

    cascade_matcher.invalidate_embedding_cache(reason="nothing-to-purge")

    # dirty フラグは立たない（書き込みすることがない）
    assert cascade_matcher._cache_dirty is False
    assert not isolated_cache.exists()


def test_save_merges_with_concurrent_disk_additions(isolated_cache):
    """save 時に他プロセスが disk に追加したエントリを merge して保持する。
    Codex review r3067185... 対応。
    """
    fake = _FakeModel(identity="m")

    # Process A: 'a' を encode して in-memory に保持（まだ flush しない）
    cascade_matcher.encode_texts_cached(fake, ["a"], model_name="m")
    key_a = cascade_matcher._make_cache_key("a", "m")
    assert key_a in cascade_matcher._embedding_cache

    # 並行プロセス B を模倣: disk に 'b' を直接書き込む
    # （A の知らないところで別プロセスが追加した状態）
    key_b = cascade_matcher._make_cache_key("b", "m")
    vec_b = np.full(8, 7.5, dtype=np.float32)
    with open(isolated_cache, "wb") as fh:
        np.savez(fh, **{key_b: vec_b})
    assert isolated_cache.exists()

    # Process A が flush: 旧実装だと disk を silent に overwrite して 'b' が消える
    # 新実装だと reload-merge で {a, b} 両方が保存される
    cascade_matcher.flush_embedding_cache()

    # 再ロードして確認
    cascade_matcher.clear_embedding_cache()
    cascade_matcher._load_embedding_cache()

    assert key_a in cascade_matcher._embedding_cache
    assert key_b in cascade_matcher._embedding_cache
    # B の vector が exact に保持されている
    np.testing.assert_array_equal(
        cascade_matcher._embedding_cache[key_b], vec_b
    )


def test_save_uses_unique_tmp_file_name(isolated_cache, monkeypatch):
    """並行 save で tmp file collision が起きないよう、各呼び出しで
    pid + random suffix を含むユニークな tmp 名が使われる。
    Codex review r3067190373 対応。
    """
    fake = _FakeModel(identity="m")
    cascade_matcher.encode_texts_cached(fake, ["a"], model_name="m")

    observed_tmp_names: list[str] = []
    orig_open = open

    def tracking_open(path, mode="r", *args, **kwargs):
        # tmp ファイルへの書き込みだけを記録
        p = str(path)
        if ".tmp" in p and "w" in mode:
            observed_tmp_names.append(p)
        return orig_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)

    cascade_matcher.flush_embedding_cache()
    assert len(observed_tmp_names) == 1
    tmp_name = observed_tmp_names[0]

    # tmp 名には pid が含まれている（per-process uniqueness）
    import os as _os
    assert str(_os.getpid()) in tmp_name
    # 固定名 embedding_cache.npz.tmp では**ない**（collision 防止）
    assert not tmp_name.endswith("embedding_cache.npz.tmp")
    # 最終 .npz は正しく生成されている
    assert isolated_cache.exists()


def test_save_different_calls_use_different_tmp_names(isolated_cache, monkeypatch):
    """連続した save 呼び出しでも tmp 名は毎回ユニーク（random suffix）。"""
    fake = _FakeModel(identity="m")

    observed: list[str] = []
    orig_open = open

    def tracking_open(path, mode="r", *args, **kwargs):
        p = str(path)
        if ".tmp" in p and "w" in mode:
            observed.append(p)
        return orig_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)

    # 2 回 save して tmp 名が異なることを確認
    cascade_matcher.encode_texts_cached(fake, ["a"], model_name="m")
    cascade_matcher.flush_embedding_cache()
    cascade_matcher.encode_texts_cached(fake, ["b"], model_name="m")
    cascade_matcher.flush_embedding_cache()

    assert len(observed) == 2
    assert observed[0] != observed[1]


def test_save_cleans_up_tmp_on_failure(isolated_cache, monkeypatch):
    """write 途中で例外が起きても tmp file が残骸として残らない。"""
    fake = _FakeModel(identity="m")
    cascade_matcher.encode_texts_cached(fake, ["a"], model_name="m")

    # np.savez を失敗させる
    def failing_savez(fh, **kwargs):
        # file に少し書いた状態で失敗（tmp file は作られている）
        fh.write(b"partial")
        raise OSError("simulated write failure")

    monkeypatch.setattr(cascade_matcher.np, "savez", failing_savez)

    # save は exception を raise するが、warning を吐いて吸収する
    cascade_matcher.flush_embedding_cache()

    # tmp file の残骸が存在しない（cleanup が走った）
    parent = isolated_cache.parent
    tmp_leftovers = list(parent.glob("*.tmp"))
    assert tmp_leftovers == []


def test_merge_skips_disk_entries_with_wrong_dim(isolated_cache):
    """merge 時に次元が合わない disk エントリはスキップされる。"""
    fake = _FakeModel(identity="m")

    # A: 8-dim entry を in-memory
    cascade_matcher.encode_texts_cached(fake, ["a"], model_name="m")
    assert cascade_matcher._cache_embed_dim == 8

    # Disk に 16-dim の stale エントリが存在する状況を模倣
    # （別モデルが過去に書いた残骸）
    key_stale = cascade_matcher._make_cache_key("stale", "m")
    vec_stale = np.zeros(16, dtype=np.float32)
    with open(isolated_cache, "wb") as fh:
        np.savez(fh, **{key_stale: vec_stale})

    # Flush: merge でスキャンするが dim 不一致で stale をスキップ
    cascade_matcher.flush_embedding_cache()

    # 再ロード
    cascade_matcher.clear_embedding_cache()
    cascade_matcher._load_embedding_cache()

    # A のエントリだけが残り、stale エントリは含まれない
    key_a = cascade_matcher._make_cache_key("a", "m")
    assert key_a in cascade_matcher._embedding_cache
    assert key_stale not in cascade_matcher._embedding_cache


def test_invalidation_pending_skips_merge(isolated_cache):
    """invalidate 後の save は merge を行わず authoritative に上書きする。

    これが無いと invalidate → 再エンコード → save フローで disk 上の
    古いエントリが merge で復活してしまう。
    """
    fake = _FakeModel(identity="m")

    # Disk に古いエントリを用意
    cascade_matcher.encode_texts_cached(fake, ["old1", "old2"], model_name="m")
    cascade_matcher.flush_embedding_cache()
    assert isolated_cache.exists()

    # invalidate → 新しいエントリを追加 → flush
    cascade_matcher.invalidate_embedding_cache(reason="model-update-recovery")
    assert cascade_matcher._invalidation_pending is True
    cascade_matcher.encode_texts_cached(fake, ["new1"], model_name="m")
    cascade_matcher.flush_embedding_cache()
    # フラグは save 後にリセット
    assert cascade_matcher._invalidation_pending is False

    # 再ロードして: new1 のみ、old1/old2 は復活しない
    cascade_matcher.clear_embedding_cache()
    cascade_matcher._load_embedding_cache()

    key_new1 = cascade_matcher._make_cache_key("new1", "m")
    key_old1 = cascade_matcher._make_cache_key("old1", "m")
    assert key_new1 in cascade_matcher._embedding_cache
    assert key_old1 not in cascade_matcher._embedding_cache


def test_invalidate_then_repopulate_overwrites_stale_file(isolated_cache):
    """invalidate 後に別のエントリを入れた場合、disk の .npz が新しい
    エントリのみで上書きされ、古いエントリは含まれない。"""
    fake = _FakeModel(identity="m")
    cascade_matcher.encode_texts_cached(fake, ["old1", "old2"], model_name="m")
    cascade_matcher.flush_embedding_cache()

    cascade_matcher.invalidate_embedding_cache(reason="test")
    cascade_matcher.encode_texts_cached(fake, ["new1"], model_name="m")
    cascade_matcher.flush_embedding_cache()

    # 再ロードして確認
    cascade_matcher.clear_embedding_cache()
    cascade_matcher._load_embedding_cache()

    new_key = cascade_matcher._make_cache_key("new1", "m")
    old_key = cascade_matcher._make_cache_key("old1", "m")
    assert new_key in cascade_matcher._embedding_cache
    assert old_key not in cascade_matcher._embedding_cache


def test_encode_texts_cached_handles_mid_call_dim_drift(isolated_cache):
    """encode_texts_cached 内で old cached + new encoded の dim drift を
    proactive に検出し、invalidate + 再エンコードで安全に処理する。
    Codex review r3067162770 の回帰テスト。
    """
    model_name = "drift-test"

    # Phase 1: 16 次元の stale なエントリを cache に注入
    cascade_matcher._load_embedding_cache()
    stale_key = cascade_matcher._make_cache_key("cached_text", model_name)
    cascade_matcher._embedding_cache[stale_key] = np.zeros(16, dtype=np.float32)
    cascade_matcher._cache_embed_dim = 16

    # Phase 2: 8 次元を返す fake model で、cached_text + new_text を一括リクエスト
    # 既存ガードが無いと: cached_text → cache hit (16-dim) と
    # new_text → encode (8-dim) が混在し np.stack で shape error
    fake_new = _FakeModel(identity=model_name)
    result = cascade_matcher.encode_texts_cached(
        fake_new, ["cached_text", "new_text"], model_name=model_name
    )

    # Phase 3: drift が検出されて cache 全破棄 + 再エンコード
    # 結果はすべて 8 次元で一貫している
    assert result.shape == (2, 8)
    assert cascade_matcher._cache_embed_dim == 8

    # 古い 16 次元エントリは無くなっている
    # 新しい 8 次元エントリがキャッシュに存在する
    refreshed_key = cascade_matcher._make_cache_key("cached_text", model_name)
    assert cascade_matcher._embedding_cache[refreshed_key].shape == (8,)
    new_key = cascade_matcher._make_cache_key("new_text", model_name)
    assert cascade_matcher._embedding_cache[new_key].shape == (8,)


def test_dim_drift_recovery_does_not_infinite_recurse(isolated_cache):
    """dim drift 検出 → 再帰呼び出しのパスが 1 段で確実に終了することを確認。"""
    model_name = "single-recursion"
    cascade_matcher._load_embedding_cache()
    stale_key = cascade_matcher._make_cache_key("a", model_name)
    cascade_matcher._embedding_cache[stale_key] = np.zeros(32, dtype=np.float32)
    cascade_matcher._cache_embed_dim = 32

    fake = _FakeModel(identity=model_name)  # 8-dim
    # call_count = calls before
    call_count_before = len(fake.calls)

    result = cascade_matcher.encode_texts_cached(
        fake, ["a", "b", "c"], model_name=model_name
    )

    # 再帰は 1 段のみ: encode_texts 呼び出しは
    # 1) 最初: [b, c] → drift 検出 → invalidate
    # 2) 再帰: [a, b, c] → 全部 fresh
    # 合計 2 回（単純実装では）
    # ※無限再帰していないことだけ確認すれば十分
    assert len(fake.calls) - call_count_before <= 3
    assert result.shape == (3, 8)


# ============================================================
# 共有モデルロードの bounded retry (Codex review r3067206914 対応)
# ============================================================


@pytest.fixture
def reset_shared_model():
    """共有モデルのグローバル状態をテスト前後でリセットする。"""
    cascade_matcher._shared_model = None
    cascade_matcher._shared_load_failures = 0
    yield
    cascade_matcher._shared_model = None
    cascade_matcher._shared_load_failures = 0


def test_shared_model_retries_after_transient_failure(reset_shared_model, monkeypatch):
    """一過性のロード失敗後でも次の呼び出しで再試行して成功を拾える。"""
    monkeypatch.setattr(cascade_matcher, "_HAS_SBERT", True)

    call_count = {"n": 0}
    sentinel_model = object()

    def flaky_load(model_name=cascade_matcher.MODEL_NAME):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise OSError("simulated transient failure")
        return sentinel_model

    monkeypatch.setattr(cascade_matcher, "load_model", flaky_load)

    # 最初の 2 回は失敗 → None
    assert cascade_matcher.get_shared_model() is None
    assert cascade_matcher._shared_load_failures == 1
    assert cascade_matcher.get_shared_model() is None
    assert cascade_matcher._shared_load_failures == 2

    # 3 回目で成功 → モデル取得、失敗カウンタ reset
    assert cascade_matcher.get_shared_model() is sentinel_model
    assert cascade_matcher._shared_load_failures == 0
    assert call_count["n"] == 3

    # 以降は cached model を即返し、load_model は呼ばれない
    assert cascade_matcher.get_shared_model() is sentinel_model
    assert call_count["n"] == 3


def test_shared_model_stops_retrying_after_cap(reset_shared_model, monkeypatch):
    """_MAX_SHARED_LOAD_ATTEMPTS を超えたら以降は load_model を呼ばない。"""
    monkeypatch.setattr(cascade_matcher, "_HAS_SBERT", True)
    monkeypatch.setattr(cascade_matcher, "_MAX_SHARED_LOAD_ATTEMPTS", 2)

    call_count = {"n": 0}

    def always_fail(model_name=cascade_matcher.MODEL_NAME):
        call_count["n"] += 1
        raise OSError("permanent failure")

    monkeypatch.setattr(cascade_matcher, "load_model", always_fail)

    # cap=2 までは試行
    assert cascade_matcher.get_shared_model() is None
    assert cascade_matcher.get_shared_model() is None
    assert call_count["n"] == 2

    # 3 回目以降は試行せずに None を返す（bound cost）
    assert cascade_matcher.get_shared_model() is None
    assert cascade_matcher.get_shared_model() is None
    assert call_count["n"] == 2


def test_shared_model_returns_none_without_sbert(reset_shared_model, monkeypatch):
    """sentence-transformers 未導入時は load_model を呼ばずに None。"""
    monkeypatch.setattr(cascade_matcher, "_HAS_SBERT", False)

    called = {"n": 0}

    def should_not_be_called(model_name=cascade_matcher.MODEL_NAME):
        called["n"] += 1
        return object()

    monkeypatch.setattr(cascade_matcher, "load_model", should_not_be_called)

    assert cascade_matcher.get_shared_model() is None
    assert cascade_matcher.get_shared_model() is None
    assert called["n"] == 0
    # _HAS_SBERT=False の早期リターンは failure カウントを消費しない
    assert cascade_matcher._shared_load_failures == 0


def test_tier2_candidate_does_not_raise_on_persistent_mismatch(isolated_cache):
    """万一 invalidation 後も dim 不一致が解消しない場合でも、
    例外を投げずに空の pass_tier2=False 結果を返す。"""
    proposition = "命題"
    response = "応答1。応答2。"

    # fake_prop は 4 次元しか返さない（segments は 8 次元）
    class _BadModel:
        def __init__(self):
            self.calls: list[list[str]] = []

            class _Config:
                _name_or_path = "bad-model"

            class _AutoModel:
                config = _Config()

            class _FM:
                auto_model = _AutoModel()

            self._first = _FM()

        def _first_module(self):
            return self._first

        def encode(self, texts, batch_size=64, convert_to_numpy=True):
            self.calls.append(list(texts))
            # proposition は 4 次元、segments は 8 次元を返す異常モデル
            if len(texts) == 1:
                return np.zeros((1, 4), dtype=np.float32)
            return np.zeros((len(texts), 8), dtype=np.float32)

    bad = _BadModel()
    result = cascade_matcher.tier2_candidate(proposition, response, bad)

    # 例外を投げない
    assert result["pass_tier2"] is False
    assert result["top1_score"] == 0.0
