#!/usr/bin/env python3
"""Qメタデータ自動下書き器 — 4要素構造フレームの半自動生成スクリプト。

入力: ugh-audit-100q-v3_jsonl.txt (JSONL, 102問)
出力: q_metadata_structural_draft.jsonl (JSONL, 102問 + サマリー表示)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ---------- 定数 ----------

# UGH固有用語
UGH_TERMS = {
    "PoR", "delta_E", "ΔE", "grv", "UGHer", "SVP", "RPE", "Mesh", "SCI", "PhaseMap",
    "Semantic Vector Prompt", "Reverse Prompt Engineering",
}

# UGH固有用語の正規表現パターン（単語境界付き）
UGH_TERM_PATTERNS = [
    re.compile(r"\b" + re.escape(t) + r"\b") for t in UGH_TERMS if t.isascii()
] + [
    re.compile(re.escape(t)) for t in UGH_TERMS if not t.isascii()
]

# 一般的に既知とみなす用語（未確定語から除外）
KNOWN_TERMS = {
    "AI", "LLM", "NLP", "GPU", "CPU", "API", "ML", "DL", "GAN", "RNN", "CNN",
    "BERT", "GPT", "RLHF", "TF", "IDF", "ROC", "AUC", "PR",
    "BERTScore", "ROUGE", "BLEU",
}

# 演算子パターン
OPERATOR_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # (パターン, type, required_action)
    (re.compile(r"常に"), "universal", "問い直す or 限定する"),
    (re.compile(r"すべて"), "universal", "問い直す or 限定する"),
    (re.compile(r"あらゆる"), "universal", "問い直す or 限定する"),
    (re.compile(r"必ず"), "universal", "問い直す or 限定する"),
    (re.compile(r"普遍的"), "universal", "問い直す or 限定する"),
    (re.compile(r"本質的に"), "limiter", "問い直す or 再定義する"),
    (re.compile(r"根本的に"), "limiter", "問い直す or 再定義する"),
    (re.compile(r"完全に"), "limiter", "問い直す or 再定義する"),
    (re.compile(r"にすぎない"), "limiter_suffix", "問い直す or 再定義する"),
    (re.compile(r"単なる"), "limiter_prefix", "問い直す or 再定義する"),
    (re.compile(r"ただの"), "limiter_prefix", "問い直す or 再定義する"),
    (re.compile(r"だけ"), "limiter_suffix", "問い直す or 再定義する"),
    (re.compile(r"本当に"), "skeptical_modality", "疑いを認識して応答する"),
    (re.compile(r"果たして"), "skeptical_modality", "疑いを認識して応答する"),
    (re.compile(r"同義か"), "equivalence", "等値を問い直す or 区別する"),
    (re.compile(r"同じものか"), "equivalence", "等値を問い直す or 区別する"),
    (re.compile(r"同じものを"), "equivalence", "等値を問い直す or 区別する"),
    (re.compile(r"していないか"), "negative_question", "指摘を検討して応答する"),
    (re.compile(r"ではないか"), "negative_question", "指摘を検討して応答する"),
    (re.compile(r"ないのか"), "negative_question", "指摘を検討して応答する"),
]

# なぜパターン
WHY_PATTERN = re.compile(r"なぜ")

# 前提検出の問い文パターン
PREMISE_QUESTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"すべきか"), "当為を前提化"),  # ※方法論的質問は除外（下記ロジック参照）
    (re.compile(r"(.+)は(.+)より優れている"), "優劣を前提化"),
    (re.compile(r"犠牲にしていないか"), "犠牲を前提化"),
    (re.compile(r"のだから"), "因果を前提化"),
    (re.compile(r"犠牲者が減る"), "犠牲者減少を前提化"),
]

# カッコ書き抽出
PAREN_PATTERN = re.compile(r"（([^）]+)）")
KAKKO_PATTERN = re.compile(r"「([^」]+)」")

# 略語パターン（大文字2〜5文字、またはハイフン付き複合語 例: Chain-of-Thought）
# CJK境界も考慮: \bは日本語文字との境界で機能しないため非ASCII文字も境界とみなす
ABBREVIATION_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])([A-Z][A-Za-z0-9]+(?:-[A-Za-z]+)*)(?![A-Za-z0-9_-])")


# ---------- 抽出関数 ----------

def is_ugh_term(term: str) -> bool:
    """UGH固有用語かどうかを判定する。"""
    return term in UGH_TERMS


def extract_anchor_terms(q: dict) -> list[str]:
    """anchor_terms を抽出する。"""
    question = q["question"]
    category = q.get("category", "")
    reference_core = q.get("reference_core", "")
    core_props = q.get("core_propositions", [])

    terms: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            terms.append(t)

    # 1. カッコ書きの用語
    for m in PAREN_PATTERN.finditer(question):
        inner = m.group(1)
        # カッコの直前の語もアンカーに
        start = m.start()
        prefix = question[:start]
        # 直前の英字フレーズを取得（"Mixture of Experts" 等の複数語も対象）
        prefix_match = re.search(r"((?:[A-Za-zΔ][A-Za-z0-9_]+ )*[A-Za-zΔ][A-Za-z0-9_]+)$", prefix)
        if prefix_match:
            add(prefix_match.group(1))
        else:
            # 直前の日本語ヘッド語を取得（「推論時計算量の増大」「過信」等）
            # 漢字+助詞「の」を含む複合名詞句もマッチ
            jp_prefix_match = re.search(
                r"([一-龥ァ-ヴー][一-龥ァ-ヴーの]+[一-龥ァ-ヴー]|[一-龥ァ-ヴー]{2,})$", prefix
            )
            if jp_prefix_match:
                add(jp_prefix_match.group(1))
        add(inner)

    # 2. 鉤括弧で囲まれた概念（命題レベルの長文は除外、概念語のみ）
    for m in KAKKO_PATTERN.finditer(question):
        inner = m.group(1)
        # 動詞活用語尾を含む長い命題は除外（「AIは道具にすぎない」等）
        if len(inner) > 8 or re.search(r"(こと|[るたいすくけ])$", inner):
            continue
        add(inner)

    # 3. UGH固有用語（カテゴリがugh_theoryの場合に優先）
    if category == "ugh_theory":
        for pat in UGH_TERM_PATTERNS:
            for m in pat.finditer(question):
                add(m.group())

    # 4. question内の英字専門用語を直接抽出（Transformer, RNN, In-context learning等）
    # 日本語文脈では\bが機能しないため、非ASCII境界も考慮
    # KNOWN_TERMSはunknown_terms用のフィルタであり、アンカーでは除外しない
    for m in re.finditer(r"(?<![A-Za-z])([A-ZΔ][A-Za-z0-9_Δ]+(?:[- ][A-Za-z0-9][A-Za-z0-9]*)*)", question):
        candidate = m.group(1).strip()
        if len(candidate) >= 2:
            # 既にフレーズとして追加済みの部分語は除外
            if not any(candidate != t and candidate in t for t in terms):
                add(candidate)

    # 5. core_propositions / reference_core から重要語を補完抽出
    all_text = reference_core + " " + " ".join(core_props)
    # 英字の専門用語（KNOWN_TERMSもアンカー対象）
    for m in re.finditer(r"\b([A-ZΔ][A-Za-z0-9_Δ]+)\b", all_text):
        candidate = m.group(1)
        if len(candidate) >= 2:
            # questionにも出現し、既存フレーズの部分語でない場合のみ追加
            if candidate in question:
                if not any(candidate != t and candidate in t for t in terms):
                    add(candidate)

    # 6. 日本語の重要概念: 漢字+カタカナの複合語も保持（例: 量子コンピューティング）
    stop_katakana = {"プロンプト", "モデル", "データ", "テスト", "システム", "ベース"}
    for m in re.finditer(r"[一-龥]*[ァ-ヴー]{2,}[一-龥ァ-ヴー]*", question):
        word = m.group()
        # 純カタカナ部分がストップワードなら除外
        kata_only = re.sub(r"[^ァ-ヴー]", "", word)
        if kata_only in stop_katakana:
            continue
        if len(word) >= 2:
            add(word)

    # questionの主題となる漢字語（3文字以上、または2文字でも専門的なもの）
    stop_kanji = {
        "場合", "問題", "可能", "必要", "重要", "存在", "意味", "方法",
        "以上", "以下", "以外", "現在", "結果", "理由", "影響", "関係",
        "変化", "目的", "原因", "対象", "状況", "内容", "範囲", "程度",
        "開発", "生成", "利用", "使用", "判断", "評価", "処理", "出力",
    }
    for m in re.finditer(r"[一-龥]{2,}", question):
        word = m.group()
        if word in stop_kanji:
            continue
        # 3文字以上の漢字語は専門的とみなす
        if len(word) >= 3:
            add(word)
        # 2文字でもreference_core/core_propositionsに出現するか、
        # 質問の主要対象（助詞「の」「を」「は」「が」の直前）なら追加
        elif word in reference_core or word in " ".join(core_props):
            add(word)
        elif re.search(re.escape(word) + r"[をはがの]", question):
            add(word)

    return terms


def extract_unknown_terms(q: dict) -> tuple[list[str], str | None]:
    """unknown_terms と unknown_default_action を抽出する。"""
    question = q["question"]

    unknowns: list[str] = []
    seen: set[str] = set()

    # 1. カッコ前の複数語フレーズを未確定語候補に追加（部分語の先取り）
    for m in PAREN_PATTERN.finditer(question):
        start = m.start()
        prefix = question[:start]
        # 英字フレーズ（"Mixture of Experts"等）
        pfx_match = re.search(
            r"((?:[A-Za-zΔ][A-Za-z0-9_]+ )*[A-Za-zΔ][A-Za-z0-9_]+)$", prefix
        )
        if pfx_match:
            phrase = pfx_match.group(1)
            if phrase not in KNOWN_TERMS and phrase not in seen:
                seen.add(phrase)
                unknowns.append(phrase)
                # フレーズの各単語もseenに登録して重複を防ぐ
                for word in phrase.split():
                    seen.add(word)

    # 2. 略語・ハイフン付き複合語を候補とする
    for m in ABBREVIATION_PATTERN.finditer(question):
        abbr = m.group(1)
        if abbr in KNOWN_TERMS:
            continue
        if abbr in seen:
            continue
        # UGH固有用語でquestionの主対象なら追加
        if is_ugh_term(abbr):
            seen.add(abbr)
            unknowns.append(abbr)
        # ハイフン付き複合語（例: Chain-of-Thought）は未確定語候補
        elif "-" in abbr:
            seen.add(abbr)
            unknowns.append(abbr)
        # 大文字略語（2〜5文字）
        elif abbr.isupper() and 2 <= len(abbr) <= 5:
            seen.add(abbr)
            unknowns.append(abbr)
        # 混合ケース略語（MoE, CoT等: 大文字始まり2〜5文字で内部に大文字を含む）
        elif 2 <= len(abbr) <= 5 and any(c.isupper() for c in abbr[1:]):
            seen.add(abbr)
            unknowns.append(abbr)
        # タイトルケースの専門用語（Transformer, Kaplan等: 6文字以上）
        elif len(abbr) >= 6 and abbr[0].isupper() and not abbr.isupper():
            seen.add(abbr)
            unknowns.append(abbr)

    # 3. カッコ内の英語表現を未確定語候補に追加
    for m in PAREN_PATTERN.finditer(question):
        inner = m.group(1)
        if inner in seen or inner in KNOWN_TERMS:
            continue
        # 英字を含む表現（専門用語の英語グロス）は未確定語候補
        if re.search(r"[A-Za-z]", inner):
            seen.add(inner)
            unknowns.append(inner)

    # 4. 鉤括弧内の日本語専門用語を未確定語候補に追加
    for m in KAKKO_PATTERN.finditer(question):
        inner = m.group(1)
        if inner in seen:
            continue
        # 命題レベルの長文や動詞活用を含むものは除外
        if len(inner) > 8 or re.search(r"(こと|[るたいすくけ])$", inner):
            continue
        # 4文字以上の専門的な表現は未確定語候補
        if len(inner) >= 4:
            seen.add(inner)
            unknowns.append(inner)

    # 5. カタカナ専門用語（一般的でないもの）を未確定語候補に追加
    known_katakana = {
        "プロンプト", "モデル", "データ", "テスト", "システム", "ベース",
        "バイアス", "アルゴリズム", "ネットワーク", "パラメータ", "パターン",
        "コンテキスト", "トレーニング", "リスク", "コスト", "エラー",
        "ソース", "オープン", "クローズド", "イノベーション",
        "フレームワーク", "ニュース", "ガバナンス", "セキュリティ",
        "プライバシー", "インフラ", "プラットフォーム", "カテゴリ",
        "メカニズム", "プロセス", "ロジック", "ツール",
    }
    for m in re.finditer(r"[ァ-ヴー]{4,}", question):
        word = m.group()
        if word in known_katakana or word in seen:
            continue
        seen.add(word)
        unknowns.append(word)

    # 6. UGH固有用語（日本語含む）でquestion中に出現するもの
    for term in UGH_TERMS:
        if term in question and term not in seen:
            seen.add(term)
            unknowns.append(term)

    # 一般的に既知のものを除外
    unknowns = [t for t in unknowns if t not in KNOWN_TERMS]

    # フレーズの部分語を除去（"Mechanistic Interpretability" がある場合 "Mechanistic" を除外）
    unknowns = [t for t in unknowns if not any(t != u and t in u for u in unknowns)]

    if not unknowns:
        return [], None

    # default_action の決定
    has_ugh = any(is_ugh_term(t) for t in unknowns)
    action = "不確実性明示" if has_ugh else "保持"

    return unknowns, action


def extract_operators(q: dict) -> tuple[list[dict], str | None]:
    """operators と operator_required_action を抽出する。"""
    question = q["question"]
    ops: list[dict] = []
    actions: list[str] = []
    has_why = bool(WHY_PATTERN.search(question))
    has_universal = False

    # 接尾辞型演算子（スコープは演算子の前方にかかる）
    suffix_types = {"limiter_suffix", "negative_question", "equivalence"}

    for pat, op_type, req_action in OPERATOR_PATTERNS:
        m = pat.search(question)
        if m:
            term = m.group()

            if op_type in suffix_types:
                # 接尾辞型: 演算子の前方からスコープを取得
                before = question[:m.start()]
                if op_type in ("negative_question", "equivalence"):
                    # 否定疑問・等値は文全体の主張に対する問いかけ → 全前方をスコープ
                    scope = before.strip()
                else:
                    # limiter_suffix: 直近の句読点以降をスコープ
                    scope_match = re.search(r"[。？?、「]([^。？?、「]+)$", before)
                    scope = scope_match.group(1).strip() if scope_match else before.strip()
            else:
                # 前置型: 演算子の前方（直近句読点以降）+後方を結合してスコープ
                before = question[:m.start()]
                after = question[m.end():]
                # 前方: 直近の句読点以降を取得（目的語を含む）
                before_match = re.search(r"[。？?、「]([^。？?、「]+)$", before)
                before_scope = before_match.group(1).strip() if before_match else before.strip()
                # 後方: 句読点まで
                after_match = re.match(r"(.+?)[。？?]", after)
                after_scope = after_match.group(1) if after_match else after.rstrip("？?。")
                after_scope = after_scope.strip()
                scope = f"{before_scope}{term}{after_scope}" if before_scope else after_scope

            if not scope:
                scope = "全文"

            ops.append({
                "term": term,
                "scope": scope,
                "type": op_type,
            })
            actions.append(req_action)
            if op_type == "universal":
                has_universal = True

    # なぜ + 全称の複合パターン
    if has_why and has_universal:
        ops.append({
            "term": "なぜ",
            "scope": "全文",
            "type": "reason_request_with_premise",
        })
        # actionsを修正
        combined = "「なぜ」は全称を事実として前提化しているため先に全称を処理する"
        actions.append(combined)
    elif has_why:
        # なぜ単独でも、trap_typeがpremise_acceptanceなら前提埋め込み型の理由要求
        trap_type = q.get("trap_type", "")
        if trap_type == "premise_acceptance":
            ops.append({
                "term": "なぜ",
                "scope": "全文",
                "type": "reason_request_with_premise",
            })
            actions.append("「なぜ」が前提を事実化しているため、前提自体を先に検討する")

    if not ops:
        return [], None

    # combined action
    if len(actions) == 1:
        action = actions[0]
    else:
        # 重複排除して結合
        unique_actions = list(dict.fromkeys(actions))
        action = "。".join(unique_actions)

    return ops, action


def extract_premise(q: dict) -> dict:
    """f4_premise のメタデータを抽出する。"""
    question = q["question"]
    trap_type = q.get("trap_type", "")
    disq = q.get("disqualifying_shortcuts", [])
    accept_vars = q.get("acceptable_variants", [])

    premise_present = False
    premise_content = ""
    acceptable_stances: list[str] = []

    # 1. trap_type による判定
    if trap_type in ("premise_acceptance", "binary_reduction", "safety_boilerplate"):
        premise_present = True

    # 2. 問い文パターンからの検出
    detected_patterns: list[str] = []
    # 「どのように〜すべきか」は方法論的質問であり前提罠ではない
    is_methodological = bool(re.search(r"どのように.+すべきか", question))
    for pat, desc in PREMISE_QUESTION_PATTERNS:
        if pat.search(question):
            # すべきか が方法論的文脈なら除外
            if desc == "当為を前提化" and is_methodological:
                continue
            premise_present = True
            detected_patterns.append(desc)

    # 3. premise_content の生成
    if premise_present:
        contents: list[str] = []
        if disq:
            contents.append("前提パターン: " + "; ".join(disq))
        if detected_patterns:
            contents.append("問い文から検出: " + ", ".join(detected_patterns))
        if trap_type == "binary_reduction":
            contents.append("二値化フレームによる前提の埋め込み")
        if trap_type == "premise_acceptance":
            contents.append("誘導前提の受け入れを誘うパターン")
        if trap_type == "safety_boilerplate":
            contents.append("安全性定型句への逃避を誘うパターン")
        premise_content = "。".join(contents) if contents else "前提あり（詳細要確認）"

    # 4. acceptable_stances
    if premise_present:
        acceptable_stances = list(accept_vars) if accept_vars else ["前提を検討して応答する"]

    return {
        "premise_present": premise_present,
        "premise_content": premise_content if premise_present else "",
        "premise_acceptable_stances": acceptable_stances,
    }


def compute_severity(
    q: dict,
    anchor_terms: list[str],
    unknown_terms: list[str],
    operators: list[dict],
    premise: dict,
) -> dict[str, str]:
    """各要素のseverity_hintを計算する。"""
    question = q["question"]
    trap_type = q.get("trap_type", "")

    sev = {
        "f1": "low",
        "f2": "low",
        "f3": "low",
        "f4": "low",
    }

    # f1: アンカー語が空または汎用述語のみ → medium
    # 汎用述語: 質問の主題を特定できない一般的な動詞・形容詞
    generic_predicates = {
        "正当化", "可能", "必要", "重要", "存在", "意味", "問題", "影響",
        "変化", "関係", "理由", "結果", "原因", "目的", "方法",
    }
    if not anchor_terms:
        sev["f1"] = "medium"
    elif all(t in generic_predicates for t in anchor_terms):
        sev["f1"] = "medium"

    # f2: UGH固有語が主対象 → high、非UGH未確定語あり → medium
    ugh_unknowns = [t for t in unknown_terms if is_ugh_term(t)]
    if ugh_unknowns:
        for t in ugh_unknowns:
            if t in question:
                sev["f2"] = "high"
                break
    elif unknown_terms:
        sev["f2"] = "medium"

    # f3: 全称表現あり → high、limiter/negative_question → medium
    for op in operators:
        if op["type"] in ("universal", "reason_request_with_premise"):
            sev["f3"] = "high"
            break
        if op["type"] in ("limiter", "limiter_prefix", "limiter_suffix", "negative_question", "equivalence"):
            sev["f3"] = "medium"

    # f4: premise
    if premise["premise_present"]:
        if trap_type == "premise_acceptance":
            sev["f4"] = "medium"
        elif trap_type == "binary_reduction":
            sev["f4"] = "medium"
        else:
            sev["f4"] = "medium"

    return sev


def compute_review_flags(
    severity: dict[str, str],
    source_requires_manual_review: bool = False,
) -> dict:
    """review_flags を生成する。"""
    reasons: list[str] = []
    max_sev = "low"

    for key in ("f1", "f2", "f3", "f4"):
        s = severity[key]
        label = {
            "f1": "f1_anchor",
            "f2": "f2_unknown",
            "f3": "f3_operator",
            "f4": "f4_premise",
        }[key]
        if s == "high":
            reasons.append(f"{label} severity=high")
            max_sev = "high"
        elif s == "medium":
            reasons.append(f"{label} severity=medium")
            if max_sev != "high":
                max_sev = "medium"

    # 元データの requires_manual_review を尊重する
    if source_requires_manual_review:
        reasons.append("source requires_manual_review=true")

    needs_review = max_sev in ("high", "medium") or source_requires_manual_review

    # confidence: high=全要素low / medium=medium要素あり / low=high要素あり
    medium_count = sum(1 for k in ("f1", "f2", "f3", "f4") if severity[k] == "medium")
    if max_sev == "high":
        confidence = "low"
    elif max_sev == "medium":
        confidence = "medium" if medium_count >= 2 or source_requires_manual_review else "medium"
    elif source_requires_manual_review:
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "needs_human_review": needs_review,
        "review_reason": ", ".join(reasons) if reasons else "",
        "auto_draft_confidence": confidence,
    }


def process_question(q: dict) -> dict:
    """1問分のメタデータを生成する。"""
    anchor_terms = extract_anchor_terms(q)
    unknown_terms, unknown_action = extract_unknown_terms(q)
    operators, operator_action = extract_operators(q)
    premise = extract_premise(q)

    severity = compute_severity(q, anchor_terms, unknown_terms, operators, premise)
    review_flags = compute_review_flags(severity, q.get("requires_manual_review", False))

    result = {
        "id": q["id"],
        "category": q.get("category", ""),
        "question": q["question"],
        "structural_meta": {
            "f1_anchor": {
                "anchor_terms": anchor_terms,
                "anchor_allowed_rephrase": [],
                "anchor_forbidden_reinterpret": [],
                "severity_hint": severity["f1"],
            },
            "f2_unknown": {
                "unknown_terms": unknown_terms,
                "unknown_default_action": unknown_action,
                "severity_hint": severity["f2"],
            },
            "f3_operator": {
                "operators": operators,
                "operator_required_action": operator_action,
                "severity_hint": severity["f3"],
            },
            "f4_premise": {
                **premise,
                "severity_hint": severity["f4"],
            },
        },
        "review_flags": review_flags,
        "original_trap_type": q.get("trap_type", ""),
        "original_disqualifying_shortcuts": q.get("disqualifying_shortcuts", []),
        "original_core_propositions": q.get("core_propositions", []),
        "original_acceptable_variants": q.get("acceptable_variants", []),
    }

    return result


def print_summary(results: list[dict]) -> None:
    """サマリーを標準出力に表示する。"""
    total = len(results)

    # severity統計
    stats: dict[str, dict[str, int]] = {
        f: {"high": 0, "medium": 0, "low": 0}
        for f in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise")
    }

    needs_review_count = 0
    high_severity_items: list[tuple[str, list[str]]] = []

    for r in results:
        meta = r["structural_meta"]
        highs: list[str] = []
        for fkey, fname in [
            ("f1_anchor", "f1_anchor"),
            ("f2_unknown", "f2_unknown"),
            ("f3_operator", "f3_operator"),
            ("f4_premise", "f4_premise"),
        ]:
            sev = meta[fkey]["severity_hint"]
            stats[fname][sev] += 1
            if sev == "high":
                highs.append(fname)

        if r["review_flags"]["needs_human_review"]:
            needs_review_count += 1

        if highs:
            high_severity_items.append((r["id"], highs))

    print("\n" + "=" * 60)
    print("Qメタデータ自動下書き — サマリー")
    print("=" * 60)

    print("\n【全体統計】")
    print(f"  総問数: {total}")
    print(f"  needs_human_review = true: {needs_review_count}件")

    print("\n  severity分布:")
    for fname in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise"):
        s = stats[fname]
        print(f"    {fname}: high={s['high']} / medium={s['medium']} / low={s['low']}")

    print("\n【要注意問題】(severity=high が1つ以上)")
    if high_severity_items:
        for qid, highs in high_severity_items:
            print(f"  {qid}: {', '.join(highs)}")
    else:
        print("  なし")

    # 番兵問プレビュー
    sentinel_ids = ["q032", "q024", "q095", "q015", "q025", "q033", "q100"]
    sentinel_map = {r["id"]: r for r in results}

    print("\n【番兵問プレビュー】")
    for sid in sentinel_ids:
        if sid not in sentinel_map:
            print(f"\n  {sid}: 見つかりません")
            continue
        r = sentinel_map[sid]
        print(f"\n  --- {sid} ---")
        print(f"  question: {r['question']}")
        meta = r["structural_meta"]
        print(f"  f1_anchor.terms: {meta['f1_anchor']['anchor_terms']}")
        print(f"  f1_anchor.severity: {meta['f1_anchor']['severity_hint']}")
        print(f"  f2_unknown.terms: {meta['f2_unknown']['unknown_terms']}")
        print(f"  f2_unknown.severity: {meta['f2_unknown']['severity_hint']}")
        if meta["f3_operator"]["operators"]:
            op_terms = [o["term"] for o in meta["f3_operator"]["operators"]]
            print(f"  f3_operator.terms: {op_terms}")
        else:
            print("  f3_operator.terms: []")
        print(f"  f3_operator.severity: {meta['f3_operator']['severity_hint']}")
        print(f"  f4_premise.present: {meta['f4_premise']['premise_present']}")
        if meta["f4_premise"]["premise_content"]:
            print(f"  f4_premise.content: {meta['f4_premise']['premise_content']}")
        print(f"  f4_premise.severity: {meta['f4_premise']['severity_hint']}")
        print(f"  review: {r['review_flags']}")


def main() -> None:
    # 入力ファイルの検索
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    # 入力ファイル候補
    candidates = [
        project_root / "ugh-audit-100q-v3_jsonl.txt",
        project_root / "data" / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt",
    ]

    input_path = None
    for c in candidates:
        if c.exists():
            input_path = c
            break

    if input_path is None:
        print("エラー: 入力ファイルが見つかりません", file=sys.stderr)
        print("候補:", file=sys.stderr)
        for c in candidates:
            print(f"  {c}", file=sys.stderr)
        sys.exit(1)

    print(f"入力: {input_path}")

    # 出力パス
    output_path = project_root / "data" / "question_sets" / "q_metadata_structural_draft.jsonl"

    # 読み込み
    questions: list[dict] = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            questions.append(json.loads(line))

    print(f"読み込み: {len(questions)}問")

    # 処理
    results: list[dict] = []
    for q in questions:
        result = process_question(q)
        results.append(result)

    # 出力
    with open(output_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"出力: {output_path}")

    # サマリー表示
    print_summary(results)


if __name__ == "__main__":
    main()
