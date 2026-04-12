"""experiments/meta_generator.py
Claude API による question_meta 動的生成

自由質問から detect() が消費する question_meta を生成する。
ANTHROPIC_API_KEY 環境変数が必要。未設定時は最小限の fallback を返す。
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from .prompts.meta_generation_v1 import (
    SYSTEM_PROMPT as META_GEN_SYSTEM,
    USER_TEMPLATE as META_GEN_USER,
)
from .prompts.meta_improvement_v1 import (
    SYSTEM_PROMPT as META_IMP_SYSTEM,
    USER_TEMPLATE as META_IMP_USER,
)

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

logger = logging.getLogger(__name__)

# メタ言語的記述を検出するパターン
# 「...」と〜 (鉤括弧で引用した上での動作記述) / と全否定 はショートカットとして不適切
# 注: 「と断言」「と主張する」は鉤括弧なしの表層フレーズとして有効なため除外
_META_DESCRIPTION_RE = re.compile(
    r'「.+」と|と全否定|のみで答える$'
)

# デフォルトモデル
DEFAULT_MODEL = "claude-sonnet-4-6"

# 有効な trap_type (premise_frames.yaml から)
_VALID_TRAP_TYPES = frozenset({
    "premise_acceptance",
    "binary_reduction",
    "scope_deflection",
    "metric_omnipotence",
    "authority_appeal",
    "safety_boilerplate",
    "relativism_drift",
    "",  # 罠なしも許容
})


def _parse_json_response(text: str) -> Optional[dict]:
    """LLM 応答から JSON を抽出してパースする"""
    text = text.strip()
    # ```json ... ``` ブロックの除去
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON 部分だけ抽出を試みる
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None


def _coerce_str_list(value: object) -> List[str]:
    """値を List[str] に正規化する。文字列なら1要素リストに、非リストなら空に。"""
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [p for p in value if isinstance(p, str) and len(p) > 0]


def _validate_meta(meta: dict, question: str) -> dict:
    """question_meta のスキーマバリデーションと正規化

    question は常に入力値を使用（LLM の言い換え/切り詰めを防止）。
    """
    # trap_type バリデーション: 不明な値は空文字に落とす
    trap_type = meta.get("trap_type", "")
    if not isinstance(trap_type, str):
        trap_type = ""
    if trap_type not in _VALID_TRAP_TYPES:
        logger.warning("不明な trap_type '%s' を空文字に修正しました", trap_type)
        trap_type = ""

    # disqualifying_shortcuts のメタ言語的記述をフィルタ
    # 「...」と全否定する のようなメタ記述は表層文字列ではないため除外
    raw_shortcuts = _coerce_str_list(meta.get("disqualifying_shortcuts"))
    filtered_shortcuts = [
        s for s in raw_shortcuts
        if not _META_DESCRIPTION_RE.search(s)
    ]
    if len(filtered_shortcuts) < len(raw_shortcuts):
        dropped = set(raw_shortcuts) - set(filtered_shortcuts)
        logger.warning("メタ言語的ショートカットを除外: %s", dropped)

    # metadata_confidence を保持 (soft_rescue のガード条件で使用)
    raw_confidence = meta.get("metadata_confidence")
    confidence: Optional[float] = None
    if raw_confidence is not None:
        try:
            confidence = float(max(0.0, min(1.0, float(raw_confidence))))
        except (TypeError, ValueError):
            pass

    valid = {
        "question": str(question),  # 常に入力値を使用、str に強制
        "core_propositions": _coerce_str_list(meta.get("core_propositions")),
        "disqualifying_shortcuts": filtered_shortcuts,
        "acceptable_variants": _coerce_str_list(meta.get("acceptable_variants")),
        "trap_type": trap_type,
    }
    if confidence is not None:
        valid["metadata_confidence"] = confidence
    return valid


def _fallback_meta(question: str) -> dict:
    """最小限のフォールバック meta（LLM 不使用時）"""
    return {
        "question": str(question),
        "core_propositions": [],
        "disqualifying_shortcuts": [],
        "acceptable_variants": [],
        "trap_type": "",
    }


def generate_meta(
    question: str,
    model: str = DEFAULT_MODEL,
    use_cache: bool = True,
) -> dict:
    """自由質問から question_meta を生成する

    Args:
        question: 質問テキスト
        model: 使用する Claude モデル
        use_cache: キャッシュを使用するか

    Returns:
        detect() が消費する question_meta dict
    """
    # キャッシュチェック
    if use_cache:
        from .meta_cache import get_cached_meta, save_cached_meta
        cached = get_cached_meta(question, model)
        if cached is not None:
            return cached

    if not _HAS_ANTHROPIC:
        logger.warning("anthropic SDK 未インストール: fallback meta を返します")
        return _fallback_meta(question)

    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            system=META_GEN_SYSTEM,
            messages=[
                {"role": "user", "content": META_GEN_USER.format(question=question)},
            ],
        )
        response_text = message.content[0].text
        parsed = _parse_json_response(response_text)
        if parsed is None:
            logger.warning("JSON パース失敗: fallback meta を返します")
            return _fallback_meta(question)
        result = _validate_meta(parsed, question)
        # キャッシュ保存（core_propositions が非空の場合のみ）
        if use_cache and result.get("core_propositions"):
            save_cached_meta(question, result, model)
        return result
    except Exception:
        logger.exception("meta 生成に失敗: fallback meta を返します")
        return _fallback_meta(question)


def _guard_hit_propositions(
    original_meta: dict,
    improved_meta: dict,
    hit_ids: List[int],
) -> dict:
    """hit 命題が改変されていないかガードする

    hit した命題は変更禁止。LLM が勝手に変えた場合は元に戻す。
    インデックス位置を保持する（append ではなく pad + assign）。
    """
    orig_props = original_meta.get("core_propositions", [])
    new_props = improved_meta.get("core_propositions", [])

    # 必要な最大インデックスまでパディング
    max_idx = max(hit_ids) if hit_ids else -1
    required_len = max(len(new_props), max_idx + 1)
    guarded_props = list(new_props) + [""] * (required_len - len(new_props))
    restored = []

    for idx in hit_ids:
        if idx < len(orig_props):
            if guarded_props[idx] != orig_props[idx]:
                guarded_props[idx] = orig_props[idx]
                restored.append(idx)

    # パディングで追加された空文字を除去
    guarded_props = [p for p in guarded_props if p]

    if restored:
        logger.warning("ガード発動: hit 命題 %s を元に復元しました", restored)

    improved_meta = {**improved_meta, "core_propositions": guarded_props}
    return improved_meta


def improve_meta(
    question: str,
    current_meta: dict,
    audit_result: dict,
    response_text: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """監査結果を見て meta を改善する

    Args:
        question: 質問テキスト
        current_meta: 現在の question_meta
        audit_result: audit() の戻り値
        response_text: 監査対象の回答テキスト
        model: 使用する Claude モデル

    Returns:
        改善された question_meta dict
    """
    if not _HAS_ANTHROPIC:
        logger.warning("anthropic SDK 未インストール: 現在の meta をそのまま返します")
        return current_meta

    state = audit_result.get("state", {})
    evidence = audit_result.get("evidence", {})
    policy = audit_result.get("policy", {})
    verdict = policy.get("decision", policy.get("verdict", "unknown"))

    # 改善ヒント生成
    hints = []
    c_val = state.get("C")
    if c_val is not None and c_val < 0.5:
        hints.append(
            "C が低い: 命題が回答テキストの表現と乖離している可能性。"
            "同じ意味を保ったまま、より照合しやすい表現に調整してください。"
        )
    miss_ids = evidence.get("miss_ids", [])
    if miss_ids:
        missed_props = [
            current_meta["core_propositions"][i]
            for i in miss_ids
            if i < len(current_meta["core_propositions"])
        ]
        if missed_props:
            hints.append(f"miss した命題: {missed_props}")

    try:
        client = anthropic.Anthropic()
        user_content = META_IMP_USER.format(
            question=question,
            current_meta_json=json.dumps(current_meta, ensure_ascii=False, indent=2),
            response_text=response_text[:2000],
            verdict=verdict,
            S=round(state.get("S", 0.0), 4),
            C=state.get("C", "N/A"),
            delta_e=state.get("delta_e", "N/A"),
            hit_ids=evidence.get("hit_ids", []),
            miss_ids=miss_ids,
            hit_rate=f"{evidence.get('propositions_hit', 0)}/{evidence.get('propositions_total', 0)}",
            improvement_hint="\n".join(hints) if hints else "特になし",
        )
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            system=META_IMP_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        parsed = _parse_json_response(message.content[0].text)
        if parsed is None:
            logger.warning("改善 JSON パース失敗: 現在の meta を返します")
            return current_meta
        improved = _validate_meta(parsed, question)

        # ガード: hit 命題が改変されていたら復元
        hit_ids = evidence.get("hit_ids", [])
        improved = _guard_hit_propositions(current_meta, improved, hit_ids)

        # refinement_notes をログ用に保持
        improved["refinement_notes"] = parsed.get("refinement_notes", [])

        return improved
    except Exception:
        logger.exception("meta 改善に失敗: 現在の meta を返します")
        return current_meta
