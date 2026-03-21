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
    (re.compile(r"本質的に"), "limiter", "問い直す or 再定義する"),
    (re.compile(r"にすぎない"), "limiter", "問い直す or 再定義する"),
    (re.compile(r"単なる"), "limiter", "問い直す or 再定義する"),
    (re.compile(r"ただの"), "limiter", "問い直す or 再定義する"),
    (re.compile(r"本当に"), "skeptical_modality", "疑いを認識して応答する"),
    (re.compile(r"果たして"), "skeptical_modality", "疑いを認識して応答する"),
    (re.compile(r"していないか"), "negative_question", "指摘を検討して応答する"),
    (re.compile(r"ではないか"), "negative_question", "指摘を検討して応答する"),
    (re.compile(r"ないのか"), "negative_question", "指摘を検討して応答する"),
]

# なぜパターン
WHY_PATTERN = re.compile(r"なぜ")

# 前提検出の問い文パターン
PREMISE_QUESTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"すべきか"), "当為を前提化"),
    (re.compile(r"(.+)は(.+)より優れている"), "優劣を前提化"),
    (re.compile(r"犠牲にしていないか"), "犠牲を前提化"),
    (re.compile(r"のだから"), "因果を前提化"),
    (re.compile(r"犠牲者が減る"), "犠牲者減少を前提化"),
]

# カッコ書き抽出
PAREN_PATTERN = re.compile(r"（([^）]+)）")
KAKKO_PATTERN = re.compile(r"「([^」]+)」")

# 略語パターン（大文字2〜5文字）
ABBREVIATION_PATTERN = re.compile(r"\b([A-Z][A-Za-z0-9]{1,4})\b")


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
        # 直前の単語を取得（英字略語など）
        prefix_match = re.search(r"([A-Za-zΔ][A-Za-z0-9_]+)$", prefix)
        if prefix_match:
            add(prefix_match.group(1))
        add(inner)

    # 2. 鉤括弧で囲まれた概念
    for m in KAKKO_PATTERN.finditer(question):
        add(m.group(1))

    # 3. UGH固有用語（カテゴリがugh_theoryの場合に優先）
    if category == "ugh_theory":
        for pat in UGH_TERM_PATTERNS:
            for m in pat.finditer(question):
                add(m.group())

    # 4. core_propositions / reference_core から重要語を抽出
    all_text = reference_core + " " + " ".join(core_props)
    # 英字の専門用語
    for m in re.finditer(r"\b([A-ZΔ][A-Za-z0-9_Δ]+)\b", all_text):
        candidate = m.group(1)
        if len(candidate) >= 2 and candidate not in KNOWN_TERMS:
            # questionにも出現する場合のみ追加
            if candidate in question:
                add(candidate)

    # 5. 日本語の重要概念: questionから2文字以上のカタカナ語を抽出
    for m in re.finditer(r"[ァ-ヴー]{2,}", question):
        word = m.group()
        if word not in {"プロンプト", "モデル", "データ", "テスト", "システム", "ベース"}:
            add(word)

    # questionの主題となる漢字語（reference_coreにも出現するもの）
    for m in re.finditer(r"[一-龥]{2,}", question):
        word = m.group()
        if word in reference_core and len(word) >= 2:
            add(word)

    return terms


def extract_unknown_terms(q: dict) -> tuple[list[str], str | None]:
    """unknown_terms と unknown_default_action を抽出する。"""
    question = q["question"]

    unknowns: list[str] = []
    seen: set[str] = set()

    # 1. 略語を候補とする
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
        # UGH固有でない略語で、既知でもないもの
        elif len(abbr) >= 2:
            seen.add(abbr)
            unknowns.append(abbr)

    # 2. カッコ内の英語表現でUGH固有のもの
    for m in PAREN_PATTERN.finditer(question):
        inner = m.group(1)
        if is_ugh_term(inner):
            if inner not in seen:
                seen.add(inner)
                unknowns.append(inner)

    # 3. UGH固有用語（日本語含む）でquestion中に出現するもの
    for term in UGH_TERMS:
        if term in question and term not in seen:
            seen.add(term)
            unknowns.append(term)

    # 一般的に既知のものを除外
    unknowns = [t for t in unknowns if t not in KNOWN_TERMS]

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

    for pat, op_type, req_action in OPERATOR_PATTERNS:
        m = pat.search(question)
        if m:
            term = m.group()
            # scopeを推定: 演算子以降の文を取得
            after = question[m.end():]
            # 句読点か疑問符までをスコープとする
            scope_match = re.match(r"(.+?)[。？?、]", after)
            scope = scope_match.group(1) if scope_match else after.rstrip("？?。")
            scope = scope.strip()
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
        # なぜ単独は演算子として追加しない（前提なしの理由要求は正当）
        pass

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
    if trap_type in ("premise_acceptance", "binary_reduction"):
        premise_present = True

    # 2. 問い文パターンからの検出
    detected_patterns: list[str] = []
    for pat, desc in PREMISE_QUESTION_PATTERNS:
        if pat.search(question):
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

    # f2: UGH固有語が主対象 → high
    ugh_unknowns = [t for t in unknown_terms if is_ugh_term(t)]
    if ugh_unknowns:
        # 主対象かどうか: questionの冒頭〜中盤に出現するか
        for t in ugh_unknowns:
            if t in question:
                sev["f2"] = "high"
                break

    # f3: 全称表現あり → high
    for op in operators:
        if op["type"] in ("universal", "reason_request_with_premise"):
            sev["f3"] = "high"
            break

    # f4: premise
    if premise["premise_present"]:
        if trap_type == "premise_acceptance":
            sev["f4"] = "medium"
        elif trap_type == "binary_reduction":
            sev["f4"] = "medium"
        else:
            sev["f4"] = "medium"

    return sev


def compute_review_flags(severity: dict[str, str]) -> dict:
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

    needs_review = max_sev in ("high", "medium")

    # confidence
    if max_sev == "high":
        confidence = "medium"
    elif max_sev == "medium":
        confidence = "high"
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

    severity = compute_severity(q, unknown_terms, operators, premise)
    review_flags = compute_review_flags(severity)

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

    print(f"\n【全体統計】")
    print(f"  総問数: {total}")
    print(f"  needs_human_review = true: {needs_review_count}件")

    print(f"\n  severity分布:")
    for fname in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise"):
        s = stats[fname]
        print(f"    {fname}: high={s['high']} / medium={s['medium']} / low={s['low']}")

    print(f"\n【要注意問題】(severity=high が1つ以上)")
    if high_severity_items:
        for qid, highs in high_severity_items:
            print(f"  {qid}: {', '.join(highs)}")
    else:
        print("  なし")

    # 番兵問プレビュー
    sentinel_ids = ["q032", "q024", "q095", "q015", "q025", "q033", "q100"]
    sentinel_map = {r["id"]: r for r in results}

    print(f"\n【番兵問プレビュー】")
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
            print(f"  f3_operator.terms: []")
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
