#!/usr/bin/env python3
"""構造ゲート判定スクリプト — AI回答の構造的破綻を検出する。

確定済みQメタデータと AI 回答を入力として、
各回答の構造的破綻を fail / warn / pass の三値で判定する。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# パターン辞書（定数）
# ---------------------------------------------------------------------------

# f2: 留保表現
RESERVATION_EXPRESSIONS: List[str] = [
    "不明",
    "確認が必要",
    "定義によるが",
    "定かではない",
    "正確には",
    "仮に",
    "一般的には",
    "文脈によるが",
    "明確ではない",
    "推測になるが",
    "断定はできない",
    "と思われる",
    "可能性がある",
    "詳細は不明",
    "についてはわからない",
]

# f3: 限定表現
LIMITING_EXPRESSIONS: List[str] = [
    "必ずしも",
    "場合による",
    "一概には",
    "文脈に依存",
    "条件次第",
]

# f3: 修正表現
CORRECTION_EXPRESSIONS: List[str] = [
    "ただし",
    "しかし",
    "一方で",
    "とは限らない",
    "ではない場合もある",
]

# f3: 反証表現
COUNTER_EXPRESSIONS: List[str] = [
    "反例として",
    "例外として",
    "逆に",
]

# f3: 問い直し表現
REFRAMING_EXPRESSIONS: List[str] = [
    "そもそも",
    "前提として",
    "本当に",
]

# f4: 前提対応表現（Tier 1: 明確な前提問い直し → f4=0.0）
PREMISE_RESPONSE_PATTERNS: Dict[str, List[str]] = {
    "問い直し": ["前提として", "そもそも", "仮定が", "とは限らない", "限りません"],
    "限定": ["一面では", "部分的には", "条件付きで"],
    "保留": ["判断を留保", "断定は避け", "一概には", "単純には"],
    "反証": ["実際には", "経験的には", "データによれば", "反論"],
    "再定式化": ["むしろ", "問いを変えると", "本質は", "本質的に"],
    "区別": ["区別"],
    "必要性指摘": ["考慮する必要", "検討する必要", "注意が必要"],
}

# f4: 部分的前提応答（Tier 2: 前提への部分的対応 → f4=0.5 に留める）
PREMISE_PARTIAL_PATTERNS: List[str] = [
    "慎重に",
    "一理あり",
    "確かに",
]

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> List[str]:
    """句点「。」で文分割する。"""
    parts = text.replace("\n", "。").split("。")
    return [s.strip() for s in parts if s.strip()]


def _normalize_text(text: str) -> str:
    """表記揺れ対応の正規化（カタカナ→ひらがな等は行わず、大文字小文字統一のみ）。"""
    return text.lower().strip()


def _extract_evidence(text: str, keyword: str, max_len: int = 100) -> Optional[str]:
    """テキスト内から keyword を含む箇所を最大 max_len 文字で抜粋する。"""
    idx = text.find(keyword)
    if idx == -1:
        return None
    start = max(0, idx - 20)
    end = min(len(text), idx + len(keyword) + max_len - 20)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _has_pattern(text: str, patterns: List[str]) -> bool:
    """テキスト内にパターンリストのいずれかが含まれるかを判定する。"""
    for p in patterns:
        if p in text:
            return True
    return False


def _find_pattern(text: str, patterns: List[str]) -> Optional[str]:
    """テキスト内に最初にマッチしたパターンを返す。"""
    for p in patterns:
        if p in text:
            return p
    return None


# ---------------------------------------------------------------------------
# f1: 参照一貫性チェック
# ---------------------------------------------------------------------------


def _extract_forbidden_patterns(note: str) -> List[str]:
    """forbidden_reinterpret のアノート文から実際のマッチパターンを抽出する。

    メタデータは『パターン1』『パターン2』形式のレビューアノートを含むことがあるため、
    括弧内のパターンを抽出する。括弧がなければ原文をそのまま返す。
    """
    # 『...』内のパターンを抽出
    patterns: List[str] = re.findall(r"『([^』]+)』", note)
    if patterns:
        return patterns
    # 「...」内のパターンも試行
    patterns = re.findall(r"「([^」]+)」", note)
    if patterns:
        return patterns
    # 括弧がなければ原文をそのまま使用
    return [note]


def check_f1_anchor(
    response: str,
    meta: Dict[str, Any],
) -> Tuple[float, str, Optional[str]]:
    """anchor_terms の出現率と forbidden_reinterpret を検査する。

    Returns:
        (flag, trigger_description, evidence)
    """
    anchor_terms: List[str] = meta.get("anchor_terms", [])
    allowed: List[str] = meta.get("anchor_allowed_rephrase", [])
    forbidden: List[str] = meta.get("anchor_forbidden_reinterpret", [])

    if not anchor_terms:
        return 0.0, "", None

    resp_lower = _normalize_text(response)

    # forbidden チェック
    # メタデータの forbidden_reinterpret はレビューアノート形式のことがあるため、
    # 『...』内のパターンを抽出してマッチングする
    for fb in forbidden:
        if not fb:
            continue
        fb_patterns = _extract_forbidden_patterns(fb)
        for pat in fb_patterns:
            pat_lower = _normalize_text(pat)
            if pat_lower in resp_lower:
                ev = _extract_evidence(response, pat)
                return 1.0, f"forbidden_reinterpret マッチ: 「{pat}」", ev

    # 出現率計算（allowed_rephrase も出現と見なす）
    hit = 0
    for term in anchor_terms:
        term_lower = _normalize_text(term)
        if term_lower in resp_lower:
            hit += 1
        else:
            # allowed_rephrase に含まれる言い換えが出現すれば OK
            for alt in allowed:
                if alt and _normalize_text(alt) in resp_lower:
                    hit += 1
                    break

    rate = hit / len(anchor_terms)
    if rate < 0.3:
        missing = [t for t in anchor_terms if _normalize_text(t) not in resp_lower]
        trigger = f"anchor出現率={rate:.2f} (欠落: {', '.join(missing[:3])})"
        return 0.5, trigger, None

    return 0.0, "", None


# ---------------------------------------------------------------------------
# f2: 未確定語慎重性チェック
# ---------------------------------------------------------------------------


def check_f2_unknown(
    response: str,
    meta: Dict[str, Any],
) -> Tuple[float, str, Optional[str]]:
    """unknown_terms の取り扱いを検査する。

    Returns:
        (flag, trigger_description, evidence)
    """
    unknown_terms: List[str] = meta.get("unknown_terms", [])
    default_action: str = meta.get("unknown_default_action", "")
    severity: str = meta.get("severity_hint", "low")

    if not unknown_terms:
        return 0.0, "", None

    # 展開可の場合は勝手展開チェックをスキップ
    expansion_allowed = default_action and "展開可" in default_action

    resp_lower = _normalize_text(response)
    worst_flag = 0.0
    worst_trigger = ""
    worst_evidence: Optional[str] = None

    for term in unknown_terms:
        term_lower = _normalize_text(term)

        # (a) 勝手展開の検出（severity=high かつ 展開可でない場合のみ）
        if severity == "high" and not expansion_allowed:
            expand_pattern = re.compile(
                re.escape(term) + r"[（\(]([^）\)]+)[）\)]",
                re.IGNORECASE,
            )
            match = expand_pattern.search(response)
            if match:
                expanded = match.group(1)
                # 展開先が unknown_terms に含まれている場合はスキップ（既知の別名）
                expanded_lower = _normalize_text(expanded)
                is_known_variant = any(
                    _normalize_text(ut) == expanded_lower
                    for ut in unknown_terms if ut != term
                )
                if not is_known_variant:
                    # 他の unknown_terms が R 内に存在するかチェック
                    other_terms_present = any(
                        _normalize_text(ut) in resp_lower
                        for ut in unknown_terms
                        if _normalize_text(ut) != term_lower
                        and len(ut) >= 2
                    )
                    ev = _extract_evidence(response, match.group(0))
                    if other_terms_present:
                        flag = 0.5
                        trigger = f"「{term}」を「{expanded}」に展開（関連語あり）"
                    else:
                        flag = 1.0
                        trigger = f"「{term}」を「{expanded}」に勝手展開"
                    if flag > worst_flag:
                        worst_flag = flag
                        worst_trigger = trigger
                        worst_evidence = ev
                    continue

        # (a') 別概念へのすり替え：term が一度も登場しない（severity=high かつ 展開可でない場合のみ）
        if severity == "high" and not expansion_allowed and term_lower not in resp_lower:
            # 他の unknown_terms が同じ概念の別表記で出現しているかチェック
            other_present = any(
                _normalize_text(ut) in resp_lower
                for ut in unknown_terms if ut != term
            )
            if not other_present:
                flag = 1.0
                trigger = f"「{term}」がR内に不出現（別概念にすり替え）"
                if flag > worst_flag:
                    worst_flag = flag
                    worst_trigger = trigger
                    worst_evidence = None

    if worst_flag > 0:
        return worst_flag, worst_trigger, worst_evidence

    # (b) 不確実性明示チェック（action が "不確実性明示" の場合のみ）
    if default_action and "不確実性明示" in default_action:
        if not _has_pattern(response, RESERVATION_EXPRESSIONS):
            trigger = f"「{unknown_terms[0]}」に対する留保表現なし"
            return 0.5, trigger, None

    return 0.0, "", None


# ---------------------------------------------------------------------------
# f3: 演算子処理チェック
# ---------------------------------------------------------------------------


def check_f3_operator(
    response: str,
    meta: Dict[str, Any],
) -> Tuple[float, str, Optional[str]]:
    """operators の処理を検査する。

    Returns:
        (flag, trigger_description, evidence)
    """
    operators: List[Dict[str, str]] = meta.get("operators", [])

    if not operators:
        return 0.0, "", None

    worst_flag = 0.0
    worst_trigger = ""
    worst_evidence: Optional[str] = None

    for op in operators:
        term: str = op.get("term", "")
        op_type: str = op.get("type", "")

        if not term:
            continue

        flag, trigger, evidence = _check_single_operator(response, term, op_type)
        if flag > worst_flag:
            worst_flag = flag
            worst_trigger = trigger
            worst_evidence = evidence

    return worst_flag, worst_trigger, worst_evidence


def _check_single_operator(
    response: str,
    term: str,
    op_type: str,
) -> Tuple[float, str, Optional[str]]:
    """単一演算子の処理チェック。"""
    has_limiting = _has_pattern(response, LIMITING_EXPRESSIONS)
    has_correction = _has_pattern(response, CORRECTION_EXPRESSIONS)
    has_counter = _has_pattern(response, COUNTER_EXPRESSIONS)
    has_reframing = _has_pattern(response, REFRAMING_EXPRESSIONS)
    term_repeated = term in response

    if op_type == "universal":
        has_any_modifier = has_limiting or has_correction or has_counter
        if not has_any_modifier:
            ev = _extract_evidence(response, term) if term_repeated else None
            return 1.0, f"「{term}」の全称を未処理、限定・反証表現なし", ev
        if has_any_modifier and term_repeated:
            ev = _extract_evidence(response, term)
            return 0.5, f"「{term}」を繰り返しつつ限定表現あり", ev
        return 0.0, "", None

    if op_type == "reason_request_with_premise":
        # 「なぜ」が前提を事実化しているため、前提自体を先に検討しているか確認
        # 単なる「しかし」等の汎用ヘッジではなく、前提レベルの問い直しを要求する
        premise_challenge_patterns = [
            "前提として", "そもそも", "仮定が", "とは限らない", "限りません",
            "必ずしも", "一概には", "単純には",
        ]
        has_premise_challenge = _has_pattern(response, premise_challenge_patterns)
        if not has_premise_challenge:
            ev = _extract_evidence(response, term) if term_repeated else None
            return 1.0, f"「{term}」＋前提を先に検討せず理由列挙", ev
        return 0.0, "", None

    if op_type in ("limiter_suffix", "limiter_prefix", "limiter"):
        if has_reframing or has_correction:
            return 0.0, "", None
        ev = _extract_evidence(response, term) if term_repeated else None
        return 0.5, f"「{term}」に対する問い直し・再定義なし", ev

    if op_type in ("skeptical", "skeptical_modality"):
        if has_reframing or has_correction or has_limiting:
            return 0.0, "", None
        ev = _extract_evidence(response, term) if term_repeated else None
        return 0.5, f"「{term}」の疑いを認識した応答なし", ev

    if op_type == "negative_question":
        # 明示的な立場表明
        stance_patterns = [
            "と考えます",
            "と思います",
            "と言えます",
            "ではありません",
            "ではない",
            "必要があります",
            "べきです",
            "重要です",
        ]
        if _has_pattern(response, stance_patterns):
            return 0.0, "", None
        ev = _extract_evidence(response, term) if term_repeated else None
        return 0.5, f"「{term}」に対する明示的立場表明なし", ev

    if op_type == "equivalence":
        # 等値を問い直す or 区別する
        distinguish_patterns = ["違い", "異なる", "区別", "一方", "対して"]
        if _has_pattern(response, distinguish_patterns):
            return 0.0, "", None
        ev = _extract_evidence(response, term) if term_repeated else None
        return 0.5, f"「{term}」の等値を問い直していない", ev

    # 未知の type — デフォルト
    if not (has_limiting or has_correction or has_counter or has_reframing):
        ev = _extract_evidence(response, term) if term_repeated else None
        return 0.5, f"「{term}」(type={op_type}) に対する処理なし", ev

    return 0.0, "", None


# ---------------------------------------------------------------------------
# f4: 前提フレーム処理チェック
# ---------------------------------------------------------------------------


def check_f4_premise(
    response: str,
    meta: Dict[str, Any],
) -> Tuple[float, str, Optional[str]]:
    """premise の処理を検査する。

    Returns:
        (flag, trigger_description, evidence)
    """
    if not meta.get("premise_present", False):
        return 0.0, "", None

    premise_content: str = meta.get("premise_content", "")

    # Tier 1: 明確な前提対応表現の検出
    for category, patterns in PREMISE_RESPONSE_PATTERNS.items():
        if _has_pattern(response, patterns):
            return 0.0, "", None

    # Tier 2: 部分的な対応表現の検出
    if _has_pattern(response, PREMISE_PARTIAL_PATTERNS):
        return 0.5, "前提への部分的対応のみ", None

    # Tier 3: 対応表現なし — premise_content のキーワード使用を確認
    if premise_content:
        keywords = _extract_premise_keywords(premise_content)
        used_keywords = [kw for kw in keywords if kw in response]
        if used_keywords:
            ev = _extract_evidence(response, used_keywords[0])
            trigger = f"前提への対応表現なし、前提キーワード「{used_keywords[0]}」をそのまま使用"
            return 1.0, trigger, ev

    return 0.5, "前提への対応表現なし", None


def _extract_premise_keywords(premise_content: str) -> List[str]:
    """premise_content から検索用キーワードを抽出する。"""
    # 「〜を前提化」「前提パターン:」等のメタ表現を除いた実質的なキーワードを取る
    # 鍵となる名詞句を抽出
    keywords: List[str] = []
    # 「犠牲」「自由度」「構造化」等の実質語を拾う
    # 簡易的に：漢字+カタカナの連続2文字以上を抽出
    for m in re.finditer(r"[一-龥ァ-ヶー]{2,}", premise_content):
        word = m.group(0)
        # メタ表現を除外
        if word not in ("前提", "パターン", "前提パターン", "誘導前提", "受け入れ", "埋め込み"):
            keywords.append(word)
    return keywords


# ---------------------------------------------------------------------------
# 総合判定
# ---------------------------------------------------------------------------

# 二重減点防止のための優先順位
ELEMENT_PRIORITY = {"f2_unknown": 0, "f3_operator": 1, "f4_premise": 2, "f1_anchor": 3}


def compute_verdict(
    f1_flag: float,
    f2_flag: float,
    f3_flag: float,
    f4_flag: float,
    f1_meta: Dict[str, Any],
    f2_meta: Dict[str, Any],
    f3_meta: Dict[str, Any],
    f4_meta: Dict[str, Any],
    f1_trigger: str,
    f2_trigger: str,
    f3_trigger: str,
    f4_trigger: str,
    f1_evidence: Optional[str],
    f2_evidence: Optional[str],
    f3_evidence: Optional[str],
    f4_evidence: Optional[str],
    threshold_fail: float = 1.0,
    threshold_warn: float = 0.5,
) -> Dict[str, Any]:
    """4要素のフラグから総合判定を算出する。"""
    elements = {
        "f1_anchor": (f1_flag, f1_meta.get("severity_hint", "low"), f1_trigger, f1_evidence),
        "f2_unknown": (f2_flag, f2_meta.get("severity_hint", "low"), f2_trigger, f2_evidence),
        "f3_operator": (f3_flag, f3_meta.get("severity_hint", "low"), f3_trigger, f3_evidence),
        "f4_premise": (f4_flag, f4_meta.get("severity_hint", "low"), f4_trigger, f4_evidence),
    }

    fail_max = max(f1_flag, f2_flag, f3_flag, f4_flag)

    if fail_max >= threshold_fail:
        verdict = "fail"
    elif fail_max >= threshold_warn:
        verdict = "warn"
    else:
        verdict = "pass"

    # primary_fail の選定
    flagged = {k: v for k, v in elements.items() if v[0] > 0}

    primary_fail: Optional[Dict[str, Any]] = None
    secondary_flags: List[Dict[str, Any]] = []

    if flagged:
        # flag=1.0 の要素を優先
        max_flag = max(v[0] for v in flagged.values())
        top_elements = {k: v for k, v in flagged.items() if v[0] == max_flag}

        if len(top_elements) == 1:
            pk = next(iter(top_elements))
        else:
            # severity_hint で比較
            severity_order = {"high": 0, "medium": 1, "low": 2}
            candidates = sorted(
                top_elements.items(),
                key=lambda x: (severity_order.get(x[1][1], 3), ELEMENT_PRIORITY.get(x[0], 9)),
            )
            pk = candidates[0][0]

        pv = elements[pk]
        primary_fail = {
            "element": pk,
            "flag": pv[0],
            "severity_hint": pv[1],
            "trigger": pv[2],
            "evidence": pv[3],
        }

        for k, v in flagged.items():
            if k != pk:
                secondary_flags.append({
                    "element": k,
                    "flag": v[0],
                    "severity_hint": v[1],
                    "trigger": v[2],
                })

    return {
        "verdict": verdict,
        "fail_max": fail_max,
        "primary_fail": primary_fail,
        "secondary_flags": secondary_flags,
        "element_scores": {
            "f1_anchor": f1_flag,
            "f2_unknown": f2_flag,
            "f3_operator": f3_flag,
            "f4_premise": f4_flag,
        },
        "evidence_texts": {
            "f1_anchor": f1_evidence,
            "f2_unknown": f2_evidence,
            "f3_operator": f3_evidence,
            "f4_premise": f4_evidence,
        },
    }


# ---------------------------------------------------------------------------
# 入出力
# ---------------------------------------------------------------------------


def load_q_metadata(path: Path) -> Dict[str, Dict[str, Any]]:
    """Qメタデータを読み込み、id → metadata の辞書を返す。"""
    metadata: Dict[str, Dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            metadata[record["id"]] = record
    return metadata


def load_responses(path: Path) -> List[Dict[str, str]]:
    """AI回答を読み込む。CSV と JSONL の両方に対応する。"""
    suffix = path.suffix.lower()
    responses: List[Dict[str, str]] = []

    if suffix == ".csv":
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                responses.append({"id": row["id"], "response": row["response"]})
    elif suffix in (".jsonl", ".json"):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                responses.append({"id": record["id"], "response": record["response"]})
    else:
        raise ValueError(f"未対応のファイル形式: {suffix} (.csv または .jsonl を指定してください)")

    return responses


def judge_single(
    response_text: str,
    structural_meta: Dict[str, Any],
    threshold_fail: float = 1.0,
    threshold_warn: float = 0.5,
    verbose: bool = False,
) -> Dict[str, Any]:
    """単一の回答に対して構造ゲート判定を行う。"""
    f1_meta = structural_meta.get("f1_anchor", {})
    f2_meta = structural_meta.get("f2_unknown", {})
    f3_meta = structural_meta.get("f3_operator", {})
    f4_meta = structural_meta.get("f4_premise", {})

    f1_flag, f1_trigger, f1_ev = check_f1_anchor(response_text, f1_meta)
    f2_flag, f2_trigger, f2_ev = check_f2_unknown(response_text, f2_meta)
    f3_flag, f3_trigger, f3_ev = check_f3_operator(response_text, f3_meta)
    f4_flag, f4_trigger, f4_ev = check_f4_premise(response_text, f4_meta)

    result = compute_verdict(
        f1_flag, f2_flag, f3_flag, f4_flag,
        f1_meta, f2_meta, f3_meta, f4_meta,
        f1_trigger, f2_trigger, f3_trigger, f4_trigger,
        f1_ev, f2_ev, f3_ev, f4_ev,
        threshold_fail=threshold_fail,
        threshold_warn=threshold_warn,
    )

    if verbose:
        print(f"  f1={f1_flag} f2={f2_flag} f3={f3_flag} f4={f4_flag}")
        for name, (flag, trigger) in [
            ("f1", (f1_flag, f1_trigger)),
            ("f2", (f2_flag, f2_trigger)),
            ("f3", (f3_flag, f3_trigger)),
            ("f4", (f4_flag, f4_trigger)),
        ]:
            if flag > 0:
                print(f"    {name}: {trigger}")

    return result


# ---------------------------------------------------------------------------
# メインパイプライン
# ---------------------------------------------------------------------------

# 番兵問の期待値
SENTINEL_EXPECTED: Dict[str, str] = {
    "q032": "fail",
    "q024": "fail",
    "q095": "fail",
    "q015": "pass",
    "q025": "warn",
    "q033": "warn",
    "q100": "warn",
}


def run_gate(
    q_metadata: Dict[str, Dict[str, Any]],
    responses: List[Dict[str, str]],
    threshold_fail: float = 1.0,
    threshold_warn: float = 0.5,
    sentinel_only: bool = False,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """全回答に対して構造ゲート判定を実行し、結果リストを返す。"""
    results: List[Dict[str, Any]] = []

    for resp in responses:
        rid = resp["id"]

        if sentinel_only and rid not in SENTINEL_EXPECTED:
            continue

        if rid not in q_metadata:
            if verbose:
                print(f"[SKIP] {rid}: Qメタデータに存在しない")
            continue

        meta = q_metadata[rid]
        structural_meta = meta.get("structural_meta", {})

        if verbose:
            print(f"[{rid}]")

        result = judge_single(
            resp["response"],
            structural_meta,
            threshold_fail=threshold_fail,
            threshold_warn=threshold_warn,
            verbose=verbose,
        )
        result["id"] = rid
        results.append(result)

    return results


def write_results_jsonl(results: List[Dict[str, Any]], path: Path) -> None:
    """結果を JSONL 形式で書き出す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_summary_csv(results: List[Dict[str, Any]], path: Path) -> None:
    """結果を CSV 形式で書き出す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id", "verdict", "fail_max", "primary_element", "primary_flag",
        "primary_trigger", "f1_flag", "f2_flag", "f3_flag", "f4_flag",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            pf = r.get("primary_fail") or {}
            scores = r.get("element_scores", {})
            writer.writerow({
                "id": r["id"],
                "verdict": r["verdict"],
                "fail_max": r["fail_max"],
                "primary_element": pf.get("element", ""),
                "primary_flag": pf.get("flag", ""),
                "primary_trigger": pf.get("trigger", ""),
                "f1_flag": scores.get("f1_anchor", 0),
                "f2_flag": scores.get("f2_unknown", 0),
                "f3_flag": scores.get("f3_operator", 0),
                "f4_flag": scores.get("f4_premise", 0),
            })


def print_summary(results: List[Dict[str, Any]]) -> None:
    """標準出力にサマリーを表示する。"""
    total = len(results)
    if total == 0:
        print("=== STRUCTURAL GATE RESULTS ===")
        print("Total: 0 responses")
        return

    fail_count = sum(1 for r in results if r["verdict"] == "fail")
    warn_count = sum(1 for r in results if r["verdict"] == "warn")
    pass_count = sum(1 for r in results if r["verdict"] == "pass")

    print("=== STRUCTURAL GATE RESULTS ===")
    print(f"Total: {total} responses")
    print(f"fail:  {fail_count} ({fail_count * 100 // total}%)")
    print(f"warn:  {warn_count} ({warn_count * 100 // total}%)")
    print(f"pass:  {pass_count} ({pass_count * 100 // total}%)")

    # FAIL CASES
    fails = [r for r in results if r["verdict"] == "fail"]
    if fails:
        print()
        print("=== FAIL CASES ===")
        for r in fails:
            pf = r.get("primary_fail") or {}
            elem = pf.get("element", "?")
            flag = pf.get("flag", "?")
            trigger = pf.get("trigger", "")
            print(f"{r['id']}: {elem}={flag} ({trigger})")

    # WARN CASES
    warns = [r for r in results if r["verdict"] == "warn"]
    if warns:
        print()
        print("=== WARN CASES ===")
        for r in warns:
            pf = r.get("primary_fail") or {}
            elem = pf.get("element", "?")
            flag = pf.get("flag", "?")
            trigger = pf.get("trigger", "")
            print(f"{r['id']}: {elem}={flag} ({trigger})")

    # SENTINEL CHECK（最初の結果を使用 = temp=0.0）
    print()
    print("=== SENTINEL CHECK ===")
    result_map: Dict[str, str] = {}
    for r in results:
        if r["id"] not in result_map:
            result_map[r["id"]] = r["verdict"]
    for sid, expected in SENTINEL_EXPECTED.items():
        actual = result_map.get(sid, "N/A")
        status = "OK" if actual == expected else "NG"
        print(f"{sid}: verdict={actual}  ← expected={expected}  {status}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="構造ゲート判定: AI回答の構造的破綻を fail/warn/pass で判定する",
    )
    parser.add_argument(
        "--q-meta",
        type=Path,
        required=True,
        help="確定済みQメタデータ (JSONL)",
    )
    parser.add_argument(
        "--responses",
        type=Path,
        required=True,
        help="AI回答ファイル (CSV or JSONL)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/gate_results/structural_gate_results.jsonl"),
        help="出力JSONL (デフォルト: data/gate_results/structural_gate_results.jsonl)",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/gate_results/structural_gate_summary.csv"),
        help="出力CSV (デフォルト: data/gate_results/structural_gate_summary.csv)",
    )
    parser.add_argument("--verbose", action="store_true", help="詳細表示")
    parser.add_argument("--sentinel-only", action="store_true", help="番兵問7件のみ判定")
    parser.add_argument(
        "--threshold-fail",
        type=float,
        default=1.0,
        help="fail判定の閾値 (デフォルト: 1.0)",
    )
    parser.add_argument(
        "--threshold-warn",
        type=float,
        default=0.5,
        help="warn判定の閾値 (デフォルト: 0.5)",
    )

    args = parser.parse_args()

    # 入力読み込み
    q_metadata = load_q_metadata(args.q_meta)
    responses = load_responses(args.responses)

    # 判定実行
    results = run_gate(
        q_metadata,
        responses,
        threshold_fail=args.threshold_fail,
        threshold_warn=args.threshold_warn,
        sentinel_only=args.sentinel_only,
        verbose=args.verbose,
    )

    # 出力
    write_results_jsonl(results, args.output)
    write_summary_csv(results, args.summary)
    print_summary(results)

    # 終了コード: fail が1件でもあれば 1
    if any(r["verdict"] == "fail" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
