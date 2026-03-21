#!/usr/bin/env python3
"""Qメタデータ構造トリアージ生成器 — 4要素構造フレームの半自動生成スクリプト。

入力: ugh-audit-100q-v3_jsonl.txt (JSONL, 102問)
出力: q_metadata_structural_draft.jsonl (JSONL, 102問 + サマリー表示)

review_tier:
  pass   — 自動承認可能。全要素 low で構造的リスクなし。
  warn   — 目視推奨だが低リスク（ソフトトリアージ）。
           f4_premise=medium 単独やソース側フラグのみの問はここに入る。
           今後のチューニングでは、不要な warn を減らすことが主な改善方向。
  review — 人間による確認が必須（ハードトリアージ）。
           high 要素あり、または f1-f3 の medium が2つ以上重なった問。

基準値メモ:
  v1 (needs_human_review 二値) では medium が1つでも review 扱いだったため
  93/102 が review に分類されていた。設計仕様書の「71/102」はスクリプト初期版
  での計測値であり、v1 最終版の実測値は 93/102 である。
  v2 で review_tier を導入し 93 → 32 に削減した（65.6%削減）。

source_requires_manual_review:
  入力 JSONL の各問に付与される元データ側フラグ。
  質問作成者が「自動判定だけでは不十分」と判断した問に true を設定する。
  本スクリプトでは severity 計算には影響せず、tier を最低 warn に引き上げる
  ためだけに使用する（pass → warn への昇格、review はそのまま維持）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------- バージョン ----------

SCHEMA_VERSION = "2.0.0"
GENERATOR_VERSION = "2.0.0"

# ---------- 定数 ----------

# UGH固有用語
UGH_TERMS = {
    "PoR", "delta_E", "ΔE", "grv", "UGHer", "SVP", "RPE", "Mesh", "SCI", "PhaseMap",
    "Semantic Vector Prompt", "Reverse Prompt Engineering",
}

# UGH固有用語の正規表現パターン（CJK境界対応）
UGH_TERM_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])")
    for t in UGH_TERMS if t.isascii()
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
    (re.compile(r"それとも"), "二択フレームを前提化"),
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

    # 2. 鉤括弧で囲まれた概念（長文命題は除外、概念的述語は保持）
    for m in KAKKO_PATTERN.finditer(question):
        inner = m.group(1)
        # 8文字超の長文命題は除外
        # 長文命題（10文字超）または主語+述語構造（「は」を含む文）は除外
        if len(inner) > 10 or (len(inner) > 6 and "は" in inner):
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
    # 英字の専門用語
    for m in re.finditer(r"\b([A-ZΔ][A-Za-z0-9_Δ]+)\b", all_text):
        candidate = m.group(1)
        if len(candidate) >= 2:
            if candidate in question:
                if not any(candidate != t and candidate in t for t in terms):
                    add(candidate)
    # ugh_theoryカテゴリ: questionが列挙を求める場合のみ、core_propsのUGH用語を補完
    if category == "ugh_theory" and re.search(r"(三つ|3つ|それぞれ|各|主要)", question):
        for term in UGH_TERMS:
            if term in all_text and term not in seen:
                add(term)

    # 6. 日本語の重要概念: 漢字+カタカナの複合語も保持（例: 量子コンピューティング）
    stop_katakana = {"プロンプト", "モデル", "データ", "テスト", "システム", "ベース"}
    for m in re.finditer(r"[A-Za-z一-龥]*[ァ-ヴー]{2,}[一-龥ァ-ヴー]*", question):
        word = m.group()
        kata_only = re.sub(r"[^ァ-ヴー]", "", word)
        # 純カタカナのみの場合はストップワードで除外、複合語は保持
        if kata_only == word and kata_only in stop_katakana:
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
        # 長文命題（10文字超）または主語+述語構造（「は」を含む文）は除外
        if len(inner) > 10 or (len(inner) > 6 and "は" in inner):
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
                    # 否定疑問・等値は同一文内の主張に対する問いかけ
                    # 文境界（？。）で区切り、最後の文をスコープとする
                    last_sent_match = re.search(r"[。？?]([^。？?]+)$", before)
                    scope = last_sent_match.group(1).strip() if last_sent_match else before.strip()
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


def extract_premise(q: dict) -> tuple[dict, list[str]]:
    """f4_premise のメタデータを抽出する。

    Returns:
        (premise_dict, detected_patterns) — detected_patterns は severity 判定で使用
    """
    question = q["question"]
    trap_type = q.get("trap_type", "")
    disq = q.get("disqualifying_shortcuts", [])
    accept_vars = q.get("acceptable_variants", [])

    premise_present = False
    premise_content = ""
    acceptable_stances: list[str] = []

    # 1. trap_type による判定
    if trap_type in ("premise_acceptance", "binary_reduction", "safety_boilerplate", "relativism_drift"):
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
        if trap_type == "relativism_drift":
            contents.append("二択フレームによる相対化を誘うパターン")
        premise_content = "。".join(contents) if contents else "前提あり（詳細要確認）"

    # 4. acceptable_stances
    if premise_present:
        acceptable_stances = list(accept_vars) if accept_vars else ["前提を検討して応答する"]

    return {
        "premise_present": premise_present,
        "premise_content": premise_content if premise_present else "",
        "premise_acceptable_stances": acceptable_stances,
    }, detected_patterns


def compute_severity(
    q: dict,
    anchor_terms: list[str],
    unknown_terms: list[str],
    operators: list[dict],
    premise: dict,
    detected_premise_patterns: list[str],
) -> dict[str, dict]:
    """各要素のseverity + 根拠情報を計算する。"""
    question = q["question"]
    trap_type = q.get("trap_type", "")

    result: dict[str, dict] = {}

    # --- f1: アンカー語 ---
    generic_predicates = {
        "正当化", "可能", "必要", "重要", "存在", "意味", "問題", "影響",
        "変化", "関係", "理由", "結果", "原因", "目的", "方法",
    }
    if not anchor_terms:
        result["f1"] = {
            "severity": "medium",
            "trigger_text": "",
            "matched_rule": "no_anchor_terms",
        }
    elif all(t in generic_predicates for t in anchor_terms):
        result["f1"] = {
            "severity": "medium",
            "trigger_text": ", ".join(anchor_terms),
            "matched_rule": "generic_predicates_only",
        }
    else:
        result["f1"] = {
            "severity": "low",
            "trigger_text": ", ".join(anchor_terms[:3]),
            "matched_rule": "anchor_terms_present",
        }

    # --- f2: 未確定語 ---
    ugh_unknowns = [t for t in unknown_terms if is_ugh_term(t)]
    if ugh_unknowns and any(t in question for t in ugh_unknowns):
        result["f2"] = {
            "severity": "high",
            "trigger_text": ", ".join(ugh_unknowns),
            "matched_rule": "ugh_term_as_subject",
        }
    elif unknown_terms:
        result["f2"] = {
            "severity": "medium",
            "trigger_text": ", ".join(unknown_terms[:3]),
            "matched_rule": "non_ugh_unknowns",
        }
    else:
        result["f2"] = {
            "severity": "low",
            "trigger_text": "",
            "matched_rule": "no_unknowns",
        }

    # --- f3: 演算子 ---
    f3_sev = "low"
    f3_trigger = ""
    f3_rule = "no_operators"
    for op in operators:
        if op["type"] in ("universal", "reason_request_with_premise"):
            f3_sev = "high"
            f3_trigger = op["term"]
            f3_rule = f"operator_{op['type']}"
            break
        if op["type"] in (
            "limiter", "limiter_prefix", "limiter_suffix",
            "negative_question", "equivalence", "skeptical_modality",
        ):
            if f3_sev != "high":
                f3_sev = "medium"
                f3_trigger = op["term"]
                f3_rule = f"operator_{op['type']}"
    result["f3"] = {
        "severity": f3_sev,
        "trigger_text": f3_trigger,
        "matched_rule": f3_rule,
    }

    # --- f4: 前提 ---
    if premise["premise_present"]:
        # 強い前提指標: premise_acceptance は常に medium
        # binary_reduction は問い文パターン検出ありで medium
        if trap_type == "premise_acceptance":
            result["f4"] = {
                "severity": "medium",
                "trigger_text": premise.get("premise_content", ""),
                "matched_rule": f"trap_type={trap_type}",
            }
        elif trap_type == "binary_reduction" and detected_premise_patterns:
            result["f4"] = {
                "severity": "medium",
                "trigger_text": ", ".join(detected_premise_patterns),
                "matched_rule": f"trap_type={trap_type}+pattern",
            }
        elif trap_type == "binary_reduction":
            # パターン検出なしの binary_reduction → low
            result["f4"] = {
                "severity": "low",
                "trigger_text": premise.get("premise_content", ""),
                "matched_rule": f"trap_type={trap_type}_weak",
            }
        else:
            # safety_boilerplate, relativism_drift → low
            result["f4"] = {
                "severity": "low",
                "trigger_text": premise.get("premise_content", ""),
                "matched_rule": f"trap_type={trap_type}_informational",
            }
    else:
        result["f4"] = {
            "severity": "low",
            "trigger_text": "",
            "matched_rule": "no_premise",
        }

    return result


# ---------- review_tier ----------

_FACTOR_LABELS = {
    "f1": "f1_anchor",
    "f2": "f2_unknown",
    "f3": "f3_operator",
    "f4": "f4_premise",
}


def compute_review_tier(
    severity_info: dict[str, dict],
    source_requires_manual_review: bool = False,
) -> dict:
    """review_tier (pass / warn / review) と根拠を生成する。

    ルール:
    - high が1つでもあれば → review
    - f1〜f3 の medium が2つ以上 → review
    - f4_premise=medium は tier 閾値に寄与しない（単独 or 補助扱い → warn）
    - medium が1つ → warn
    - source_requires_manual_review → 最低 warn
    - low のみ → pass
    """
    highs: list[str] = []
    mediums: list[str] = []
    core_mediums: list[str] = []  # f1-f3 のみ（f4 は含めない）
    all_reasons: list[dict] = []

    for key in ("f1", "f2", "f3", "f4"):
        info = severity_info[key]
        sev = info["severity"]
        label = _FACTOR_LABELS[key]
        if sev == "high":
            highs.append(key)
            all_reasons.append({
                "factor": label,
                "severity": "high",
                "trigger_text": info["trigger_text"],
                "matched_rule": info["matched_rule"],
            })
        elif sev == "medium":
            mediums.append(key)
            if key != "f4":
                core_mediums.append(key)
            all_reasons.append({
                "factor": label,
                "severity": "medium",
                "trigger_text": info["trigger_text"],
                "matched_rule": info["matched_rule"],
            })

    # --- tier 判定 ---
    # review (ハードトリアージ): high が1つでも、または f1-f3 の medium が2つ以上。
    # warn   (ソフトトリアージ): medium が1つ、または source フラグのみ。
    #   f4_premise=medium は閾値に寄与しない — v1 で f4 が review を押し上げていた
    #   主因であり、f4 単独では構造的リスクが低いため core_mediums から除外。
    # pass: 全要素 low かつ source フラグなし。
    #
    # source_requires_manual_review は元データ作成者が付与した外部フラグ。
    # severity 計算には影響せず、tier を最低 warn に引き上げるためだけに使用する。
    if highs:
        tier = "review"
    elif len(core_mediums) >= 2:
        tier = "review"
    elif mediums:
        tier = "warn"
    elif source_requires_manual_review:
        tier = "warn"
    else:
        tier = "pass"

    # source_requires_manual_review は最低 warn に引き上げ
    if source_requires_manual_review and tier == "pass":
        tier = "warn"

    # --- primary_factor（二重計上防止）---
    primary_reason: dict | None = None
    secondary_reasons: list[dict] = []
    suppressed_reasons: list[dict] = []

    if all_reasons:
        # primary: 最も重いもの（high > medium、同レベルなら f3 > f2 > f4 > f1）
        priority_order = {"f3": 0, "f2": 1, "f4": 2, "f1": 3}
        sev_order = {"high": 0, "medium": 1}
        sorted_reasons = sorted(
            all_reasons,
            key=lambda r: (sev_order.get(r["severity"], 2), priority_order.get(r["factor"].split("_")[0][:2], 9)),
        )
        primary_reason = sorted_reasons[0]

        for r in sorted_reasons[1:]:
            # 同一 matched_rule による二重計上を抑制
            if r["matched_rule"] == primary_reason["matched_rule"]:
                suppressed_reasons.append(r)
            else:
                secondary_reasons.append(r)

    if source_requires_manual_review:
        source_entry = {
            "factor": "source",
            "severity": "external",
            "trigger_text": "requires_manual_review=true",
            "matched_rule": "source_flag",
        }
        if primary_reason is None:
            # source_requires_manual_review のみで warn に引き上げたケース
            primary_reason = source_entry
        else:
            secondary_reasons.append(source_entry)

    # confidence
    if highs:
        confidence = "low"
    elif len(mediums) >= 2:
        confidence = "medium"
    elif mediums or source_requires_manual_review:
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "review_tier": tier,
        "primary_reason": primary_reason,
        "secondary_reasons": secondary_reasons,
        "suppressed_reasons": suppressed_reasons,
        "auto_draft_confidence": confidence,
    }


def process_question(q: dict) -> dict:
    """1問分のメタデータを生成する。"""
    anchor_terms = extract_anchor_terms(q)
    unknown_terms, unknown_action = extract_unknown_terms(q)
    operators, operator_action = extract_operators(q)
    premise, detected_premise_patterns = extract_premise(q)

    severity_info = compute_severity(
        q, anchor_terms, unknown_terms, operators, premise, detected_premise_patterns,
    )
    review = compute_review_tier(severity_info, q.get("requires_manual_review", False))

    result = {
        "id": q["id"],
        "category": q.get("category", ""),
        "question": q["question"],
        "structural_meta": {
            "f1_anchor": {
                "anchor_terms": anchor_terms,
                "anchor_allowed_rephrase": [],
                "anchor_forbidden_reinterpret": [],
                "severity_hint": severity_info["f1"]["severity"],
                "trigger_text": severity_info["f1"]["trigger_text"],
                "matched_rule": severity_info["f1"]["matched_rule"],
            },
            "f2_unknown": {
                "unknown_terms": unknown_terms,
                "unknown_default_action": unknown_action,
                "severity_hint": severity_info["f2"]["severity"],
                "trigger_text": severity_info["f2"]["trigger_text"],
                "matched_rule": severity_info["f2"]["matched_rule"],
            },
            "f3_operator": {
                "operators": operators,
                "operator_required_action": operator_action,
                "severity_hint": severity_info["f3"]["severity"],
                "trigger_text": severity_info["f3"]["trigger_text"],
                "matched_rule": severity_info["f3"]["matched_rule"],
            },
            "f4_premise": {
                **premise,
                "severity_hint": severity_info["f4"]["severity"],
                "trigger_text": severity_info["f4"]["trigger_text"],
                "matched_rule": severity_info["f4"]["matched_rule"],
            },
        },
        "review_tier": review["review_tier"],
        "review_detail": {
            "primary_reason": review["primary_reason"],
            "secondary_reasons": review["secondary_reasons"],
            "suppressed_reasons": review["suppressed_reasons"],
            "auto_draft_confidence": review["auto_draft_confidence"],
        },
        "original_trap_type": q.get("trap_type", ""),
        "original_disqualifying_shortcuts": q.get("disqualifying_shortcuts", []),
        "original_core_propositions": q.get("core_propositions", []),
        "original_acceptable_variants": q.get("acceptable_variants", []),
    }

    return result


def print_summary(results: list[dict], old_review_count: int | None = None) -> None:
    """拡張サマリーを標準出力に表示する。"""
    # --- tier 集計 ---
    tier_counts = {"pass": 0, "warn": 0, "review": 0}
    for r in results:
        tier_counts[r["review_tier"]] += 1

    # --- severity × 要素 ---
    sev_stats: dict[str, dict[str, int]] = {
        f: {"high": 0, "medium": 0, "low": 0}
        for f in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise")
    }
    total_high = total_medium = total_low = 0
    high_severity_items: list[tuple[str, list[str]]] = []

    for r in results:
        meta = r["structural_meta"]
        highs: list[str] = []
        for fkey in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise"):
            sev = meta[fkey]["severity_hint"]
            sev_stats[fkey][sev] += 1
            if sev == "high":
                total_high += 1
                highs.append(fkey)
            elif sev == "medium":
                total_medium += 1
            else:
                total_low += 1
        if highs:
            high_severity_items.append((r["id"], highs))

    # --- category 別 ---
    cat_counts: dict[str, dict[str, int]] = {}
    for r in results:
        cat = r.get("category", "unknown")
        if cat not in cat_counts:
            cat_counts[cat] = {"pass": 0, "warn": 0, "review": 0}
        cat_counts[cat][r["review_tier"]] += 1

    # --- primary_factor 分布 ---
    pf_counts: dict[str, int] = {}
    for r in results:
        pf = r["review_detail"].get("primary_reason")
        if pf:
            label = pf["factor"]
            pf_counts[label] = pf_counts.get(label, 0) + 1

    # === 出力 ===
    print("\n" + "=" * 60)
    print("Qメタデータ構造トリアージ — サマリー")
    print("=" * 60)

    print("\n【review_tier 集計】")
    print(f"  pass:   {tier_counts['pass']}件")
    print(f"  warn:   {tier_counts['warn']}件")
    print(f"  review: {tier_counts['review']}件")
    if old_review_count is not None:
        reduction = old_review_count - tier_counts["review"]
        pct = reduction / old_review_count * 100 if old_review_count > 0 else 0
        print(f"\n  推定レビュー削減率: {old_review_count} → {tier_counts['review']} "
              f"({reduction:+d}件, {pct:.1f}%削減)")

    print("\n【severity × 要素】")
    for fname in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise"):
        s = sev_stats[fname]
        print(f"  {fname}: high={s['high']} / medium={s['medium']} / low={s['low']}")
    print(f"\n  合計: high={total_high} / medium={total_medium} / low={total_low}")

    print("\n【category 別 tier 集計】")
    for cat in sorted(cat_counts.keys()):
        c = cat_counts[cat]
        cat_total = c["pass"] + c["warn"] + c["review"]
        print(f"  {cat} ({cat_total}): pass={c['pass']} warn={c['warn']} review={c['review']}")

    print("\n【primary_factor 分布】")
    for label, cnt in sorted(pf_counts.items(), key=lambda x: -x[1]):
        print(f"  {label}: {cnt}")

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
        print(f"  review_tier: {r['review_tier']}")
        pd = r["review_detail"]
        if pd["primary_reason"]:
            pr = pd["primary_reason"]
            print(f"  primary_reason: {pr['factor']} ({pr['severity']}) — {pr['matched_rule']}")


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

    # 運用メタ情報の付与
    source_bytes = input_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()[:16]
    generated_at = datetime.now(timezone.utc).isoformat()

    output_records: list[dict] = []
    for r in results:
        record = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "source_file": input_path.name,
            "source_hash": source_hash,
            "generator_version": GENERATOR_VERSION,
            **r,
        }
        output_records.append(record)

    # 出力
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for rec in output_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"出力: {output_path}")

    # サマリー表示
    # 基準値: v1最終版の needs_human_review=true は 93/102。
    # 設計仕様書の「71/102」は初期版での計測値であり、その後の severity 拡張で
    # 93 まで増加した。ここでは実測値の 93 を基準に削減率を算出する。
    print_summary(results, old_review_count=93)


if __name__ == "__main__":
    main()
