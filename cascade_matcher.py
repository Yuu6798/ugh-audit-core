"""cascade_matcher.py — cascade Tier 2 候補生成 + Tier 3 多条件フィルタ

SBert embedding による命題×response の文レベルマッチング（Tier 2）と、
多条件 AND フィルタによる精密判定（Tier 3）。
detector.py の既存ロジックは一切変更しない。
判定層（ugh_calculator 等）から呼ばれる補助モジュール。
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

# --- SBert optional import ---
try:
    from sentence_transformers import SentenceTransformer
    _HAS_SBERT = True
except ImportError:
    _HAS_SBERT = False

_logger = logging.getLogger(__name__)

# --- パラメータ ---
THETA_SBERT: float = 0.50   # cosine similarity 閾値（dev_cascade_20 で校正済み）
DELTA_GAP: float = 0.04     # top1 - top2 ギャップ閾値（dev_cascade_20 で校正済み）
HIGH_SCORE_THRESHOLD: float = 0.70  # c3 緩和発動の top1_score 閾値
RELAXED_DELTA_GAP: float = 0.02    # 高スコア時の緩和 δ_gap
MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"

# 括弧保護用プレースホルダ
_PAREN_PLACEHOLDER = "\x00PERIOD\x00"

# ============================================================
# 永続埋め込みキャッシュ
# ============================================================
#
# 同一テキスト・同一モデルの embedding 再計算を避けるための永続キャッシュ。
# HA48 反復評価や GoldenStore 再スコアのように同じリファレンスを繰り返し
# エンコードするワークロードで体感的な高速化が見込める。
#
# 設計方針:
#   - キーは sha256(model_name || '\x00' || text) の先頭 24hex 文字
#     （96bit、衝突確率は本プロジェクト規模では実質ゼロ）
#   - .npz 形式で永続化。キーは単一 hex 文字列で np.savez の制約を満たす
#   - プロセス終了時に atexit で save（書き込み頻度を抑える）
#   - UGH_AUDIT_EMBED_CACHE_DISABLE=1 で無効化可能
#   - UGH_AUDIT_CACHE_DIR 環境変数でディレクトリ変更可能

_DEFAULT_CACHE_DIR = Path(
    os.environ.get("UGH_AUDIT_CACHE_DIR", str(Path.home() / ".ugh_audit"))
)
_EMBED_CACHE_PATH = _DEFAULT_CACHE_DIR / "embedding_cache.npz"
_CACHE_DISABLED = os.environ.get("UGH_AUDIT_EMBED_CACHE_DISABLE", "").lower() in (
    "1", "true", "yes", "on"
)

# 容量上限（defense-in-depth）。runaway growth を防ぐため、上限に達したら
# 新規エントリは返却はするがディスクには永続化しない（LRU ではなく単純な
# hard cap）。_MAX_CACHE_ENTRIES 環境変数で調整可能。
try:
    _MAX_CACHE_ENTRIES: int = int(
        os.environ.get("UGH_AUDIT_EMBED_CACHE_MAX", "10000")
    )
except ValueError:
    _MAX_CACHE_ENTRIES = 10000

_embedding_cache: Dict[str, np.ndarray] = {}
_cache_loaded: bool = False
_cache_dirty: bool = False
# 現在のキャッシュ内エントリの次元数。異なる次元のベクトルが混在しないよう、
# 新規エンコード時に不一致を検知したら invalidate_embedding_cache() で全破棄する。
# None の場合は未確定（空キャッシュ or ロード直後）。
_cache_embed_dim: Optional[int] = None


def _make_cache_key(text: str, model_name: str) -> str:
    """テキスト + モデル名から衝突確率の低いキャッシュキーを生成する。"""
    combined = f"{model_name}\x00{text}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:24]


def _load_embedding_cache() -> None:
    """ディスク上の永続キャッシュを初回アクセス時に読み込む。"""
    global _cache_loaded, _embedding_cache, _cache_embed_dim
    if _cache_loaded or _CACHE_DISABLED:
        _cache_loaded = True
        return
    _cache_loaded = True
    try:
        if _EMBED_CACHE_PATH.exists():
            with np.load(_EMBED_CACHE_PATH) as npz:
                _embedding_cache = {k: npz[k] for k in npz.files}
    except Exception as e:  # noqa: BLE001
        _logger.warning("embedding cache load failed (starting empty): %s", e)
        _embedding_cache = {}

    # ロードしたキャッシュの次元を記録しておく（以降の dim 不一致検出の基準）
    if _embedding_cache and _cache_embed_dim is None:
        first_vec = next(iter(_embedding_cache.values()))
        try:
            _cache_embed_dim = int(first_vec.shape[0])
        except (AttributeError, IndexError):
            _cache_embed_dim = None


def _save_embedding_cache() -> None:
    """ダーティな永続キャッシュをディスクへ書き出す（atomic write）。

    空状態で dirty の場合（invalidation 後など）は、ディスク上の .npz を
    削除することで stale state の持ち越しを防ぐ（Codex review r3067162768）。
    """
    global _cache_dirty
    if _CACHE_DISABLED or not _cache_dirty:
        return
    try:
        _EMBED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _embedding_cache:
            # 空状態の persist: 古い .npz を unlink することで次回起動時に
            # stale なベクトルが再ロードされないようにする
            if _EMBED_CACHE_PATH.exists():
                _EMBED_CACHE_PATH.unlink()
            _cache_dirty = False
            return
        tmp_path = _EMBED_CACHE_PATH.with_name(_EMBED_CACHE_PATH.name + ".tmp")
        # np.savez は file-like に対しては拡張子を追加しないため、
        # 開いたバイナリハンドルを渡すことで衝突の無い atomic write を実現する。
        with open(tmp_path, "wb") as fh:
            np.savez(fh, **_embedding_cache)
        tmp_path.replace(_EMBED_CACHE_PATH)
        _cache_dirty = False
    except Exception as e:  # noqa: BLE001
        _logger.warning("embedding cache save failed: %s", e)


def clear_embedding_cache() -> None:
    """メモリ上のキャッシュをクリアする（テスト用）。ディスクは触らない。"""
    global _embedding_cache, _cache_loaded, _cache_dirty, _cache_embed_dim
    _embedding_cache = {}
    _cache_loaded = False
    _cache_dirty = False
    _cache_embed_dim = None


def invalidate_embedding_cache(reason: str = "") -> None:
    """メモリ上のキャッシュを破棄し、次回 save 時にディスクも空で上書きする。

    モデル重み更新時の stale vector 検出（shape/dim 不一致）など、
    キャッシュ全体を破棄すべき場面で呼ぶ。呼び出し元で graceful に
    re-encode する前提の公開 API。

    プロセス起動直後（lazy load 前）で disk に stale .npz が残っている
    ケースでも機能するよう、まず `_load_embedding_cache()` で disk 状態を
    メモリに読み込んでから破棄する（Codex review r3067177175 対応）。
    これにより「モデル更新後に手動で invalidate を呼ぶ」という推奨リカバリ
    フローが、起動時点で実際に disk ファイルを purge するようになる。

    Args:
        reason: ログに残す破棄理由（任意）。
    """
    global _embedding_cache, _cache_dirty, _cache_embed_dim

    # disk 上のキャッシュを先に読み込む。これにより起動直後（メモリ空）の
    # invalidate 呼び出しが no-op にならない。
    _load_embedding_cache()

    n_cleared = len(_embedding_cache)
    # _load_embedding_cache() が破損ファイルで失敗した場合もあり得るので、
    # disk ファイル自体の存在も no-op 判定に入れる
    file_exists = (not _CACHE_DISABLED) and _EMBED_CACHE_PATH.exists()

    if n_cleared == 0 and _cache_embed_dim is None and not file_exists:
        return

    _embedding_cache = {}
    _cache_embed_dim = None
    _cache_dirty = True
    _logger.warning(
        "embedding cache invalidated: %d entries cleared%s%s",
        n_cleared,
        " (disk file present)" if file_exists and n_cleared == 0 else "",
        f" ({reason})" if reason else "",
    )


def flush_embedding_cache() -> None:
    """現時点のキャッシュを即座にディスクへ永続化する。"""
    global _cache_dirty
    _cache_dirty = True
    _save_embedding_cache()


def embedding_cache_stats() -> Dict[str, int]:
    """キャッシュの状態を返す（テスト / 診断用）。"""
    return {
        "entries": len(_embedding_cache),
        "loaded": int(_cache_loaded),
        "dirty": int(_cache_dirty),
        "disabled": int(_CACHE_DISABLED),
    }


atexit.register(_save_embedding_cache)


# ============================================================
# 共有モデルシングルトン
# ============================================================

_shared_model: Optional["SentenceTransformer"] = None
_shared_load_attempted: bool = False


def get_shared_model() -> Optional["SentenceTransformer"]:
    """プロセス内で共有される SBert モデルを返す。

    detector.py, golden_store.py など複数箇所から呼ばれる想定。
    初回ロードに失敗した場合は以降 None を返し再試行しない。
    sentence-transformers 未導入時も None を返す。
    """
    global _shared_model, _shared_load_attempted
    if _shared_model is not None:
        return _shared_model
    if _shared_load_attempted or not _HAS_SBERT:
        return None
    _shared_load_attempted = True
    try:
        _shared_model = load_model()
        return _shared_model
    except Exception as e:  # noqa: BLE001
        _logger.warning("shared SBert model load failed: %s", e)
        return None


def load_model(model_name: str = MODEL_NAME) -> SentenceTransformer:
    """SentenceTransformer モデルをロードする。

    Args:
        model_name: HuggingFace モデル名。

    Returns:
        SentenceTransformer インスタンス。

    Raises:
        ImportError: sentence-transformers がインストールされていない場合。
    """
    if not _HAS_SBERT:
        raise ImportError(
            "sentence-transformers is required for cascade Tier 2. "
            "Install with: pip install sentence-transformers"
        )
    return SentenceTransformer(model_name)


def encode_texts(
    model: SentenceTransformer,
    texts: List[str],
    batch_size: int = 64,
) -> np.ndarray:
    """テキストリストを batch encoding する（キャッシュ非経由）。

    Args:
        model: SentenceTransformer インスタンス。
        texts: エンコード対象のテキストリスト。
        batch_size: バッチサイズ。

    Returns:
        (N, D) の numpy 配列。各行が1テキストの embedding。
    """
    return model.encode(texts, batch_size=batch_size, convert_to_numpy=True)


def _infer_model_id(model) -> str:
    """SentenceTransformer インスタンスから stable な識別子を best-effort で抽出する。

    複数の属性パスを順番に試し、どれも取れなければクラス名ベースの
    フォールバック識別子を返す。デフォルト `MODEL_NAME` にフォールバック
    しないのは、別モデルを渡した呼び出しが silent にキャッシュ衝突する
    のを防ぐため（Codex review r3067060572 の指摘事項）。

    Args:
        model: SentenceTransformer インスタンス。

    Returns:
        モデル識別子文字列。未解決時は `"unknown-model:<ClassName>"` 形式。
    """
    # 方法 1: 下位 transformer の HF config._name_or_path
    try:
        first_module = model._first_module()
        auto_model = getattr(first_module, "auto_model", None)
        if auto_model is not None:
            config = getattr(auto_model, "config", None)
            if config is not None:
                name = (
                    getattr(config, "_name_or_path", None)
                    or getattr(config, "name_or_path", None)
                )
                if name:
                    return str(name)
    except Exception:  # noqa: BLE001
        pass

    # 方法 2: model_card_data.base_model（新しめの sentence-transformers）
    try:
        card = getattr(model, "model_card_data", None)
        if card is not None:
            name = getattr(card, "base_model", None)
            if name:
                return str(name)
    except Exception:  # noqa: BLE001
        pass

    # 方法 3: tokenizer.name_or_path
    try:
        first_module = model._first_module()
        tokenizer = getattr(first_module, "tokenizer", None) or getattr(
            model, "tokenizer", None
        )
        if tokenizer is not None:
            name = getattr(tokenizer, "name_or_path", None)
            if name:
                return str(name)
    except Exception:  # noqa: BLE001
        pass

    # 最終フォールバック: クラス名（default MODEL_NAME には落とさない）
    return f"unknown-model:{type(model).__name__}"


def encode_texts_cached(
    model: SentenceTransformer,
    texts: List[str],
    model_name: Optional[str] = None,
    batch_size: int = 64,
) -> np.ndarray:
    """永続キャッシュを経由して encode する。

    キャッシュに存在するテキストはディスク / メモリから取得し、未キャッシュ
    のものだけを 1 バッチでモデルに投げる。戻り値の順序は `texts` と一致する。

    **使い分け指針**: この関数は **再利用性の高いテキスト**
    （リファレンス命題、GoldenStore の question など）にのみ使うこと。
    AI回答セグメントなど一回限りのテキストを渡すとキャッシュが単調増加し、
    `.npz` 全体ロード / 書き出しのコストが累積的に悪化する
    （Codex review #60 r3067115341 の指摘事項）。
    一回限りテキストは `encode_texts()` を直接呼ぶこと。

    容量上限 `_MAX_CACHE_ENTRIES` を超えた場合、未キャッシュ分のベクトルは
    返却はするがメモリ / ディスクには永続化しない（hard cap、LRU なし）。

    Args:
        model: SentenceTransformer インスタンス。
        texts: エンコード対象のテキストリスト。
        model_name: キャッシュキーに埋め込むモデル識別子。
            None の場合 `_infer_model_id(model)` で実インスタンスから自動推論する
            （異なるモデル間のキャッシュ混線を防ぐため）。明示指定すれば推論を上書き。
        batch_size: 未キャッシュ分に対するバッチサイズ。

    Returns:
        (N, D) の numpy 配列。
    """
    global _cache_dirty, _cache_embed_dim

    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    if _CACHE_DISABLED:
        return encode_texts(model, texts, batch_size=batch_size)

    _load_embedding_cache()

    if model_name is None:
        model_name = _infer_model_id(model)

    keys = [_make_cache_key(t, model_name) for t in texts]
    missing_indices: List[int] = []
    missing_texts: List[str] = []
    for i, (k, t) in enumerate(zip(keys, texts)):
        if k not in _embedding_cache:
            missing_indices.append(i)
            missing_texts.append(t)

    # 未キャッシュ分を一括 encode し、idx → vec の対応を作る
    new_vec_by_idx: Dict[int, np.ndarray] = {}
    if missing_texts:
        new_vecs = encode_texts(model, missing_texts, batch_size=batch_size)

        # Dim drift check (Codex review r3067162770):
        # 既存 cache の次元と新規エンコード結果の次元が違う場合、
        # 古いキャッシュエントリは別モデルのもの（stale）と判断して全破棄し、
        # 再帰的に本関数を呼び直して一貫した次元で再構築する。これが無いと
        # 既存 cached entry (old dim) と new_vec_by_idx (new dim) が混在し、
        # 末尾の np.stack で shape 不一致 exception が飛ぶ。
        new_dim: Optional[int] = None
        if new_vecs.ndim == 2 and new_vecs.shape[0] > 0:
            new_dim = int(new_vecs.shape[1])
        if (
            new_dim is not None
            and _cache_embed_dim is not None
            and _cache_embed_dim != new_dim
        ):
            invalidate_embedding_cache(
                reason=f"dim drift {_cache_embed_dim} -> {new_dim} "
                       f"detected in encode_texts_cached"
            )
            # 再帰呼び出し: invalidation 後は _cache_embed_dim=None なので
            # drift check が再発火せず 1 段だけで終わる。全 texts が
            # missing 扱いになり 1 batch で一貫してエンコードされる。
            return encode_texts_cached(
                model, texts, model_name=model_name, batch_size=batch_size
            )

        for idx, vec in zip(missing_indices, new_vecs):
            new_vec_by_idx[idx] = np.asarray(vec)

    # 容量上限に達するまでのみ永続化する（hard cap）
    promoted = 0
    skipped = 0
    for idx, vec in new_vec_by_idx.items():
        if len(_embedding_cache) >= _MAX_CACHE_ENTRIES:
            skipped += 1
            continue
        # 初回投入時に dim を記録。以降の guard 基準となる
        if _cache_embed_dim is None and vec.ndim >= 1:
            _cache_embed_dim = int(vec.shape[0])
        _embedding_cache[keys[idx]] = vec
        promoted += 1
    if promoted > 0:
        _cache_dirty = True
    if skipped > 0:
        _logger.warning(
            "embedding cache at capacity %d; %d new entries not persisted "
            "(consider pruning cache or raising UGH_AUDIT_EMBED_CACHE_MAX)",
            _MAX_CACHE_ENTRIES, skipped,
        )

    # 結果アセンブル: キャッシュに入った分は cache から、
    # cap で弾かれた分は new_vec_by_idx から取る
    result: List[np.ndarray] = []
    for i, k in enumerate(keys):
        if k in _embedding_cache:
            result.append(_embedding_cache[k])
        else:
            result.append(new_vec_by_idx[i])
    return np.stack(result)


def split_response(response: str) -> List[str]:
    """response を文/節に分割する。

    分割ルール:
    1. 改行で分割（暗黙の文境界）
    2. 括弧内の句点を保護
    3. 句点「。」で分割
    4. 80字超の文は読点「、」でさらに分割を試行
    5. 空文字列・空白のみは除外
    6. 前後空白を strip

    Args:
        response: AI回答の全文。

    Returns:
        文/節のリスト。
    """
    if not response or not response.strip():
        return []

    # Step 1: 改行で分割
    lines = response.split("\n")

    result: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Step 2: 括弧内の句点を保護
        protected = _protect_paren_periods(line)

        # Step 3: 句点で分割
        sentences = protected.split("。")

        for sent in sentences:
            # プレースホルダを復元
            sent = sent.replace(_PAREN_PLACEHOLDER, "。").strip()
            if not sent:
                continue

            # Step 4: 80字超は読点で分割を試行
            if len(sent) > 80:
                clauses = _split_by_comma(sent)
                result.extend(clauses)
            else:
                result.append(sent)

    return result


def _protect_paren_periods(text: str) -> str:
    """括弧内の句点をプレースホルダに置換する。"""
    # 全角括弧
    text = re.sub(
        r"（[^）]*?）",
        lambda m: m.group(0).replace("。", _PAREN_PLACEHOLDER),
        text,
    )
    # 半角括弧
    text = re.sub(
        r"\([^)]*?\)",
        lambda m: m.group(0).replace("。", _PAREN_PLACEHOLDER),
        text,
    )
    return text


def _split_by_comma(text: str) -> List[str]:
    """80字超の文を読点「、」で分割する。

    分割後に空・空白のみの要素は除外する。
    分割しても全パーツが短くならない場合はそのまま返す。
    """
    parts = text.split("、")
    if len(parts) <= 1:
        return [text]

    # 読点で分割した各部分を結合して適度な長さにする
    # 単純分割（各パーツを独立節として扱う）
    result = []
    for p in parts:
        p = p.strip()
        if p:
            result.append(p)
    return result if result else [text]


def tier2_candidate(
    proposition: str,
    response: str,
    model: SentenceTransformer,
    theta: float = THETA_SBERT,
    delta: float = DELTA_GAP,
) -> Dict:
    """response を文/節に分割し、proposition との cosine similarity を計算。

    Args:
        proposition: 命題テキスト。
        response: AI回答全文。
        model: SentenceTransformer インスタンス。
        theta: cosine similarity 閾値。
        delta: top1 - top2 ギャップ閾値。

    Returns:
        {
            "top1_sentence": str,
            "top1_score": float,
            "top2_score": float,
            "gap": float,
            "all_scores": list[float],
            "pass_tier2": bool,
        }
    """
    segments = split_response(response)
    if not segments:
        return {
            "top1_sentence": "",
            "top1_score": 0.0,
            "top2_sentence": "",
            "top2_score": 0.0,
            "gap": 0.0,
            "all_scores": [],
            "pass_tier2": False,
        }

    # Proposition は HA48 繰り返し評価などで再利用されるため永続キャッシュ経由。
    # 一方で response segments は AI回答ごとに一意で二度と使われないため、
    # キャッシュに入れると単調増加して .npz の load/save コストが悪化する
    # （Codex review #60 r3067115341）。そのため segments は encode_texts で
    # 直接エンコードし、キャッシュを経由しない。
    prop_emb = encode_texts_cached(model, [proposition])[0]  # (D,)
    seg_embs = encode_texts(model, segments)  # (N, D)

    # Shape guard: cached prop_emb が古いモデル重みの stale vector である
    # 可能性に備えて、seg_embs の次元と一致しない場合は cache を invalidate
    # して再エンコードする（Codex review #60 r3067145596）。
    # これが無いと _cosine_similarity_batch が numpy shape error で abort し、
    # detector flow 全体が graceful degradation できない。
    if prop_emb.shape[0] != seg_embs.shape[1]:
        _logger.warning(
            "proposition embedding dim %d != segment embedding dim %d; "
            "invalidating stale cache and re-encoding proposition "
            "(likely model weights updated without identifier change)",
            prop_emb.shape[0],
            seg_embs.shape[1],
        )
        invalidate_embedding_cache(
            reason=f"dim mismatch prop={prop_emb.shape[0]} seg={seg_embs.shape[1]}"
        )
        prop_emb = encode_texts_cached(model, [proposition])[0]
        if prop_emb.shape[0] != seg_embs.shape[1]:
            # invalidation 後も一致しないのは呼び出し側のモデル不整合
            # （1 回の呼び出しで異なるモデル同士を混ぜた等）。安全に degrade。
            return {
                "top1_sentence": "",
                "top1_score": 0.0,
                "top2_sentence": "",
                "top2_score": 0.0,
                "gap": 0.0,
                "all_scores": [],
                "pass_tier2": False,
            }

    # Cosine similarity
    scores = _cosine_similarity_batch(prop_emb, seg_embs)

    # Sort descending
    sorted_indices = np.argsort(scores)[::-1]
    top1_idx = sorted_indices[0]
    top1_score = float(scores[top1_idx])
    top1_sentence = segments[top1_idx]

    top2_idx = sorted_indices[1] if len(sorted_indices) > 1 else None
    top2_score = float(scores[top2_idx]) if top2_idx is not None else 0.0
    top2_sentence = segments[top2_idx] if top2_idx is not None else ""
    gap = top1_score - top2_score

    # セグメント1件のみの場合、gap は弁別不能（実質 undefined）→ pass しない
    gap_valid = len(sorted_indices) > 1
    pass_tier2 = (top1_score >= theta) and gap_valid and (gap >= delta)

    return {
        "top1_sentence": top1_sentence,
        "top1_score": top1_score,
        "top2_sentence": top2_sentence,
        "top2_score": top2_score,
        "gap": gap,
        "all_scores": [float(s) for s in scores],
        "pass_tier2": pass_tier2,
    }


def _cosine_similarity_batch(query: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """query (D,) と targets (N, D) のコサイン類似度を計算。"""
    query_norm = query / (np.linalg.norm(query) + 1e-10)
    targets_norm = targets / (np.linalg.norm(targets, axis=1, keepdims=True) + 1e-10)
    return targets_norm @ query_norm


# ============================================================
# Tier 3: 多条件フィルタ
# ============================================================

# atomic 整合で部分文字列一致とみなす最小長
_MIN_SUBSTRING_LEN = 3


def check_atomic_alignment(
    atomic_units: List[str],
    candidate_sentence: str,
    synonym_dict: Optional[Dict[str, List[str]]] = None,
) -> Dict:
    """atomic 単位の整合チェック。

    各 atomic を "|" で split し、左辺（主語/対象）と右辺（述語/属性）の
    両方が candidate_sentence に含まれるかを判定。

    含有判定（OR で評価）:
    - 完全一致
    - synonym_dict での展開後の一致
    - 3文字以上の部分文字列一致

    Args:
        atomic_units: ["left|right", ...] 形式の atomic リスト。
        candidate_sentence: 判定対象テキスト（response 全文 or top1_sentence）。
        synonym_dict: {term: [syn1, syn2, ...]} 形式。None なら synonym 展開なし。

    Returns:
        {
            "aligned_count": int,
            "total_count": int,
            "aligned_units": [{"atomic": str, "left_match": bool, "right_match": bool}],
            "pass": bool,  # aligned_count >= 1
        }
    """
    if not atomic_units or not candidate_sentence:
        return {
            "aligned_count": 0,
            "total_count": len(atomic_units) if atomic_units else 0,
            "aligned_units": [],
            "pass": False,
        }

    syn = synonym_dict or {}
    aligned_units = []
    aligned_count = 0

    for atomic in atomic_units:
        parts = atomic.split("|", 1)
        if len(parts) != 2:
            aligned_units.append({"atomic": atomic, "left_match": False, "right_match": False})
            continue

        left, right = parts[0].strip(), parts[1].strip()
        left_match = _term_in_text(left, candidate_sentence, syn)
        right_match = _term_in_text(right, candidate_sentence, syn)

        if left_match and right_match:
            aligned_count += 1

        aligned_units.append({
            "atomic": atomic,
            "left_match": left_match,
            "right_match": right_match,
        })

    return {
        "aligned_count": aligned_count,
        "total_count": len(atomic_units),
        "aligned_units": aligned_units,
        "pass": aligned_count >= 1,
    }


def _term_in_text(
    term: str,
    text: str,
    synonym_dict: Dict[str, List[str]],
) -> bool:
    """term が text 内に含まれるかを判定。

    1. 完全一致（term が text 内に出現）
    2. synonym_dict 展開後の一致
    3. 3文字以上の部分文字列一致（term の連続部分文字列）
    """
    # 1. 完全一致
    if term in text:
        return True

    # 2. synonym 展開
    # synonym_dict のキーは bigram 等の短い単位。term 内の各キーで展開を試みる。
    for key, synonyms in synonym_dict.items():
        if key in term:
            for syn in synonyms:
                # 元の term の key 部分を syn に置換して text 内検索
                expanded = term.replace(key, syn)
                if expanded in text:
                    return True
        # 逆方向: term 内に synonym 値が含まれる場合、key で置換して text を検索
        for syn in synonyms:
            if syn in term:
                expanded = term.replace(syn, key)
                if expanded in text:
                    return True

    # 3. 部分文字列一致（3文字以上）
    if len(term) >= _MIN_SUBSTRING_LEN:
        for i in range(len(term)):
            for j in range(i + _MIN_SUBSTRING_LEN, len(term) + 1):
                sub = term[i:j]
                if len(sub) >= _MIN_SUBSTRING_LEN and sub in text:
                    return True

    return False


def tier3_filter(
    tier2_result: Dict,
    tier1_hit: bool,
    f4_flag: float,
    atomic_units: List[str],
    synonym_dict: Optional[Dict[str, List[str]]] = None,
    response: Optional[str] = None,
    theta: float = THETA_SBERT,
    delta: float = DELTA_GAP,
    high_score_threshold: float = HIGH_SCORE_THRESHOLD,
    relaxed_delta: float = RELAXED_DELTA_GAP,
) -> Dict:
    """Tier 3 多条件フィルタ。全条件 AND で判定。

    条件:
    c1: tfidf miss 確認（tier1_hit == False）
    c2: embedding 閾値（top1_score >= θ_sbert）
    c3: gap 閾値（gap >= δ_gap）
    c4: f4 非発火（f4_flag == 0.0）
    c5: atomic 整合（1単位以上が response 全文に含まれる）

    Args:
        tier2_result: tier2_candidate() の返却値。
        tier1_hit: Tier 1 (tfidf) での hit フラグ。True = 既に hit 済み。
        f4_flag: structural_gate_summary の f4_flag (0.0 / 0.5 / 1.0)。
        atomic_units: ["left|right", ...] 形式の atomic リスト。
        synonym_dict: synonym 辞書。
        response: AI回答全文。None の場合は top1_sentence にフォールバック。
        theta: cosine similarity 閾値。
        delta: gap 閾値。

    Returns:
        {
            "verdict": "Z_RESCUED" | "miss",
            "conditions": {
                "c1_tfidf_miss": bool,
                "c2_embedding": bool,
                "c3_gap": bool,
                "c4_f4_clear": bool,
                "c5_atomic": bool,
            },
            "fail_reason": str | None,
            "details": dict,
        }
    """
    c1 = not tier1_hit  # Tier 1 で miss であること（二重カウント防止）
    # c2/c3: 個別条件を独立に評価（診断用）+ pass_tier2 でゲート
    # 高スコア時は δ_gap を緩和（gap が小さくても score の信頼度で補う）
    top1_score = tier2_result.get("top1_score", 0.0)
    gap = tier2_result.get("gap", 0.0)
    effective_delta = relaxed_delta if top1_score > high_score_threshold else delta
    n_segments = len(tier2_result.get("all_scores", []))
    gap_valid = n_segments > 1
    pass_t2_eff = (top1_score >= theta) and gap_valid and (gap >= effective_delta)
    score_ok = top1_score >= theta
    gap_ok = gap >= effective_delta
    c2 = pass_t2_eff and score_ok
    c3 = pass_t2_eff and gap_ok
    c4 = f4_flag < 1.0  # f4=0.0/0.5 → PASS, f4=1.0 → FAIL
    # c5: response 全文で atomic 整合チェック（未指定時は top1_sentence にフォールバック）
    c5_text = response if response else tier2_result.get("top1_sentence", "")
    atomic_result = check_atomic_alignment(
        atomic_units, c5_text, synonym_dict
    )
    c5 = atomic_result["pass"]

    conditions = {
        "c1_tfidf_miss": c1,
        "c2_embedding": c2,
        "c3_gap": c3,
        "c4_f4_clear": c4,
        "c5_atomic": c5,
    }

    all_pass = all(conditions.values())

    # fail_reason: 最初に fail した条件
    fail_reason = None
    if not all_pass:
        fail_names = {
            "c1_tfidf_miss": "Tier 1 already hit (duplicate)",
            "c2_embedding": f"top1_score ({top1_score:.4f}) < θ ({theta})" if not score_ok else f"gap ({gap:.4f}) < effective_δ ({effective_delta}) or gap_valid=False",
            "c3_gap": f"gap ({gap:.4f}) < effective_δ ({effective_delta})" if not gap_ok else "gap_valid=False",
            "c4_f4_clear": f"f4_flag={f4_flag} (premise concern)",
            "c5_atomic": "no atomic unit aligned with response",
        }
        for key, msg in fail_names.items():
            if not conditions[key]:
                fail_reason = msg
                break

    return {
        "verdict": "Z_RESCUED" if all_pass else "miss",
        "conditions": conditions,
        "fail_reason": fail_reason,
        "details": {
            "tier2": tier2_result,
            "atomic_alignment": atomic_result,
        },
    }


def run_cascade_full(
    proposition: str,
    response: str,
    model: SentenceTransformer,
    tier1_hit: bool,
    f4_flag: float,
    atomic_units: List[str],
    synonym_dict: Optional[Dict[str, List[str]]] = None,
    theta: float = THETA_SBERT,
    delta: float = DELTA_GAP,
) -> Dict:
    """Tier 1 miss 判定 → Tier 2 → Tier 3 のフルパイプライン。

    Args:
        proposition: 命題テキスト。
        response: AI回答全文。
        model: SentenceTransformer インスタンス。
        tier1_hit: Tier 1 (tfidf) での hit フラグ。
        f4_flag: f4_flag 値。
        atomic_units: atomic リスト。
        synonym_dict: synonym 辞書。
        theta: cosine similarity 閾値。
        delta: gap 閾値。

    Returns:
        tier3_filter の返却値（verdict, conditions, fail_reason, details）。
    """
    # Tier 1 で既に hit → cascade 不要
    if tier1_hit:
        return {
            "verdict": "hit_tier1",
            "conditions": {"c1_tfidf_miss": False},
            "fail_reason": "Tier 1 already hit (duplicate)",
            "details": {},
        }

    # Tier 2: 候補生成
    t2 = tier2_candidate(proposition, response, model, theta=theta, delta=delta)

    # Tier 3: 多条件フィルタ
    return tier3_filter(
        tier2_result=t2,
        tier1_hit=tier1_hit,
        f4_flag=f4_flag,
        atomic_units=atomic_units,
        synonym_dict=synonym_dict,
        response=response,
        theta=theta,
        delta=delta,
    )
