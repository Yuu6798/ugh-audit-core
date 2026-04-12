"""
ugh_audit/soft_rescue.py
自由質問の AI 草案メタデータ向け soft-hit rescue。
"""
from __future__ import annotations

import re
from typing import Any, Optional


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？!?]\s*", text)
    return [part.strip() for part in parts if part.strip()]


def _split_proposition_phrases(text: str) -> list[str]:
    normalized = text.strip()
    parts = re.split(
        r"[、,]|(?:こと)|(?:ため)|(?:には)|(?:では)|(?:によって)|(?:に依存する)|(?:を)|(?:が)|(?:は)|(?:と)|(?:である)|(?:重要である)|(?:必要である)",
        normalized,
    )
    phrases = [part.strip() for part in parts if len(part.strip()) >= 2]
    enriched: list[str] = []
    for phrase in phrases or [normalized]:
        enriched.append(phrase)
        if len(phrase) >= 4:
            enriched.extend(_char_windows(phrase, 4))
        if len(phrase) >= 5:
            enriched.extend(_char_windows(phrase, 5))
    return list(dict.fromkeys([p for p in enriched if len(p.strip()) >= 2]))


def _char_windows(text: str, size: int) -> list[str]:
    clean = text.strip()
    return [clean[i:i + size] for i in range(max(0, len(clean) - size + 1))]


def _tokenize(text: str) -> set[str]:
    normalized = " ".join(text.lower().split())
    tokens = {
        token for token in re.split(r"[\s、,・/（）()]+", normalized)
        if len(token) >= 2
    }
    if normalized:
        tokens.update(_char_windows(normalized, 2))
    if len(normalized) >= 3:
        tokens.update(normalized[i:i + 3] for i in range(len(normalized) - 2))
    elif normalized:
        tokens.add(normalized)
    return tokens


def _score_overlap(sentence: str, proposition: str) -> tuple[float, list[str]]:
    sent_tokens = _tokenize(sentence)
    prop_tokens = _tokenize(proposition)
    if not sent_tokens or not prop_tokens:
        return 0.0, []
    overlap = sorted(sent_tokens & prop_tokens)
    score = len(overlap) / len(prop_tokens)
    return score, overlap


def _score_phrase_overlap(sentence: str, proposition: str) -> tuple[float, list[str]]:
    sent_tokens = _tokenize(sentence)
    phrases = _split_proposition_phrases(proposition)
    if not sent_tokens or not phrases:
        return 0.0, []

    matched_phrases: list[str] = []
    total = 0
    for phrase in phrases:
        phrase_tokens = _tokenize(phrase)
        if not phrase_tokens:
            continue
        total += 1
        overlap_ratio = len(sent_tokens & phrase_tokens) / max(1, len(phrase_tokens))
        if overlap_ratio >= 0.25:
            matched_phrases.append(phrase)

    if total == 0:
        return 0.0, []
    return len(matched_phrases) / total, matched_phrases


def maybe_build_soft_rescue(
    *,
    question: str,
    response: str,
    question_meta: Optional[dict[str, Any]],
    mode: str,
    metadata_confidence: Optional[float],
    S: Optional[float],
    C: Optional[float],
    f2: Optional[float],
    f3: Optional[float],
) -> Optional[dict[str, Any]]:
    if mode != "computed_ai_draft":
        return None
    if question_meta is None:
        return None
    if C != 0.0:
        return None
    if S is None or S < 0.85:
        return None
    if (metadata_confidence or 0.0) < 0.8:
        return None
    if (f2 or 0.0) > 0.0:
        return None
    if (f3 or 0.0) >= 1.0:
        return None

    propositions = question_meta.get("core_propositions") or []
    if not propositions:
        return None

    best: Optional[dict[str, Any]] = None
    # 文ごとのトークンを事前計算 (N+M → O(N+M) tokenize 呼び出し)
    sentences = _split_sentences(response)
    sent_cache = [(s, _tokenize(s)) for s in sentences]
    for index, proposition in enumerate(propositions):
        prop_tokens = _tokenize(proposition)
        phrases = _split_proposition_phrases(proposition)
        for sentence, sent_tokens in sent_cache:
            # _score_overlap inline (事前計算トークンを再利用)
            if not sent_tokens or not prop_tokens:
                score, overlap = 0.0, []
            else:
                overlap_set = sorted(sent_tokens & prop_tokens)
                score = len(overlap_set) / len(prop_tokens)
                overlap = overlap_set

            # _score_phrase_overlap inline (sent_tokens 再利用)
            matched_phrases: list[str] = []
            total = 0
            for phrase in phrases:
                phrase_tokens = _tokenize(phrase)
                if not phrase_tokens:
                    continue
                total += 1
                overlap_ratio = len(sent_tokens & phrase_tokens) / max(1, len(phrase_tokens))
                if overlap_ratio >= 0.25:
                    matched_phrases.append(phrase)
            phrase_score = len(matched_phrases) / total if total > 0 else 0.0

            combined_score = max(score, phrase_score)
            if not overlap and not matched_phrases:
                continue
            candidate = {
                "type": "ai_draft_c_floor",
                "target_proposition_index": index,
                "target_proposition": proposition,
                "evidence_span": sentence,
                "confidence": round(combined_score, 4),
                "overlap_terms": overlap[:6],
                "matched_phrases": matched_phrases[:6],
            }
            if best is None or candidate["confidence"] > best["confidence"]:
                best = candidate

    if best is None or best["confidence"] < 0.08:
        return None
    return best
