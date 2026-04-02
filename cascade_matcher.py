"""cascade_matcher.py — cascade Tier 2 候補生成 + Tier 3 多条件フィルタ

SBert embedding による命題×response の文レベルマッチング（Tier 2）と、
多条件 AND フィルタによる精密判定（Tier 3）。
detector.py の既存ロジックは一切変更しない。
判定層（ugh_calculator 等）から呼ばれる補助モジュール。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import numpy as np

# --- SBert optional import ---
try:
    from sentence_transformers import SentenceTransformer
    _HAS_SBERT = True
except ImportError:
    _HAS_SBERT = False

# --- パラメータ ---
THETA_SBERT: float = 0.50   # cosine similarity 閾値（dev_cascade_20 で校正済み）
DELTA_GAP: float = 0.04     # top1 - top2 ギャップ閾値（dev_cascade_20 で校正済み）
HIGH_SCORE_THRESHOLD: float = 0.70  # c3 緩和発動の top1_score 閾値
RELAXED_DELTA_GAP: float = 0.02    # 高スコア時の緩和 δ_gap
MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"

# 括弧保護用プレースホルダ
_PAREN_PLACEHOLDER = "\x00PERIOD\x00"


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
    """テキストリストを batch encoding する。

    Args:
        model: SentenceTransformer インスタンス。
        texts: エンコード対象のテキストリスト。
        batch_size: バッチサイズ。

    Returns:
        (N, D) の numpy 配列。各行が1テキストの embedding。
    """
    return model.encode(texts, batch_size=batch_size, convert_to_numpy=True)


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
            "top2_score": 0.0,
            "gap": 0.0,
            "all_scores": [],
            "pass_tier2": False,
        }

    # Encode proposition + all segments in one batch
    all_texts = [proposition] + segments
    embeddings = encode_texts(model, all_texts)

    prop_emb = embeddings[0]  # (D,)
    seg_embs = embeddings[1:]  # (N, D)

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
        candidate_sentence: Tier 2 の top1_sentence。
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
            "c5_atomic": "no atomic unit aligned with top1_sentence",
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
