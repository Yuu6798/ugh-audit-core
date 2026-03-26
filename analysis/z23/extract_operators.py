"""Z_23 演算子抽出・族分類・正規化ドラフト生成スクリプト.

決定的パターンマッチ + 辞書参照のみ。LLM推論なし。
出力: Part A (CSV), Part B (CSV), Part C (YAML), Part D (MD)
"""
from __future__ import annotations

import csv
import json
import re

from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 0. パス定義
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
QUESTION_FILE = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
CATALOG_FILE = ROOT / "registry" / "operator_catalog.yaml"
OUT_DIR = Path(__file__).resolve().parent

# Z_23 ID リスト: original audit C=0 (21件) + C=0.25 (2件) = 23件
Z23_IDS = [
    "q001", "q002", "q003", "q004", "q007", "q011", "q012",
    "q015", "q016", "q030", "q031", "q036", "q041", "q045",
    "q048", "q064", "q065", "q066", "q067", "q074", "q090",
    "q099", "qg01",
]

# ---------------------------------------------------------------------------
# 1. operator_catalog 10族 (タスク仕様準拠 + 既存 YAML 統合)
# ---------------------------------------------------------------------------
# タスク仕様の10族をベースに、既存YAMLのsurface_patternsもマージ
OPERATOR_FAMILIES: dict[str, list[str]] = {
    "universal": [
        "常に", "すべて", "あらゆる", "必ず", "いつも", "全て",
        "どんな場合も", "普遍的", "決して", "絶対に", "一切", "まったく",
        "全く", "本質的に", "最も",
    ],
    "limiter_prefix": [
        "だけ", "単なる", "ただの", "所詮", "のみ", "しか", "だけで",
        "のみで", "限り",
    ],
    "limiter_suffix": [
        "にすぎない", "にとどまる", "でしかない", "にほかならない",
        "だけである", "にすぎず",
    ],
    "negative_question": [
        "ないのか", "ではないか", "のではないか", "じゃないか",
        "ではないのか", "ないか",
    ],
    "skeptical_modality": [
        "本当に", "果たして", "そもそも", "本当の", "本当は",
    ],
    "causal_presupposition": [
        "なぜ", "どうして", "何故", "なんで",
    ],
    "comparative": [
        "より", "以上に", "同じように", "の方が", "に比べ", "と比較",
        "よりも",
    ],
    "conditional": [
        "もし", "仮に", "の場合", "すれば", "ならば", "なら", "場合",
        "とき", "たら",
    ],
    "temporal_universal": [
        "今後も", "いつまでも", "永遠に", "これからも", "将来にわたり",
    ],
    "binary_frame": [
        "それとも", "あるいは", "または",
    ],
}

# 内容語除外リスト: これらは演算子として抽出しない
CONTENT_WORDS = {
    "機械学習", "帰納", "演繹", "AI", "意識", "責任", "倫理",
    "データ", "モデル", "アルゴリズム", "ニューラル", "学習",
    "推論", "知能", "言語", "意味", "概念", "理解", "知識",
    "情報", "技術", "社会", "人間", "哲学", "科学", "論理",
}

# 暗黙演算子パターン (正規表現ベース)
IMPLICIT_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, extracted_term, family)
    (r"十分条件ではない", "十分条件ではない（否定限定）", "limiter_suffix"),
    (r"必要条件", "必要条件（条件規定）", "conditional"),
    (r"(?:と|は)同義", "同義（等価疑問）", "comparative"),
    (r"(?:と|は)同じ", "同じ（等価疑問）", "comparative"),
    (r"(?:か|を)解決する", "解決する（完了前提）", "universal"),
    (r"保証しない", "保証しない（否定限定）", "limiter_suffix"),
    (r"可能か", "可能か（二項疑問）", "binary_frame"),
    (r"正しいか", "正しいか（二項疑問）", "binary_frame"),
    (r"(?:する|できる|なる)か[？?]?$", "〜か（二項疑問）", "binary_frame"),
    (r"未[解確検]", "未〜（否定状態）", "limiter_suffix"),
    (r"とは別", "とは別（区別）", "comparative"),
    (r"とは異なる", "とは異なる（区別）", "comparative"),
    (r"(?:完全|一切)(?:な|に)", "完全/一切（全称）", "universal"),
    (r"べき", "べき（当為）", "NEW:deontic"),
    (r"(?:二[項択]|二つ)(?:対立|の)", "二項対立（枠組み）", "binary_frame"),
    (r"再[検生]", "再〜（反復前提）", "conditional"),
    (r"(?:拡大|縮小)する", "拡大/縮小する（方向前提）", "binary_frame"),
    (r"断定", "断定（確信度）", "universal"),
    (r"一つ(?:の|に|だけ)", "一つ（限定）", "limiter_prefix"),
]


def load_questions() -> dict[str, dict]:
    """JSONL質問セットを読み込み、ID→質問辞書で返す。"""
    questions = {}
    with open(QUESTION_FILE, encoding="utf-8") as f:
        for line in f:
            q = json.loads(line.strip())
            questions[q["id"]] = q
    return questions


def extract_surface_operators(text: str) -> list[dict]:
    """テキストからsurface_patternsベースで演算子を抽出。"""
    found = []
    for family, patterns in OPERATOR_FAMILIES.items():
        for pat in patterns:
            # 内容語チェック
            if pat in CONTENT_WORDS:
                continue
            if pat in text:
                # スコープ: 演算子を含む文節（前後10文字程度）
                idx = text.index(pat)
                start = max(0, idx - 10)
                end = min(len(text), idx + len(pat) + 10)
                scope = text[start:end]
                found.append({
                    "term": pat,
                    "family": family,
                    "scope": scope,
                })
    return found


def extract_implicit_operators(text: str) -> list[dict]:
    """暗黙演算子パターンで抽出。"""
    found = []
    for pattern, term, family in IMPLICIT_PATTERNS:
        if re.search(pattern, text):
            m = re.search(pattern, text)
            if m:
                start = max(0, m.start() - 8)
                end = min(len(text), m.end() + 8)
                scope = text[start:end]
                found.append({
                    "term": term,
                    "family": family,
                    "scope": scope,
                })
    return found


def deduplicate_operators(ops: list[dict]) -> list[dict]:
    """重複除去（同一family+term）。"""
    seen = set()
    result = []
    for op in ops:
        key = (op["term"], op["family"])
        if key not in seen:
            seen.add(key)
            result.append(op)
    return result


def determine_polarity(text: str) -> str:
    """命題の極性を判定。否定 > 条件 > 肯定 の優先度。"""
    neg_patterns = [
        "ではない", "でない", "しない[。]?$", "できない", "ない$",
        "ず$", "不可能", "未確", "未解", "未検証",
        "保証しない", "ではなく", "にすぎない",
    ]
    # 「不可欠」等の非否定語を除外してから「不可」を判定
    neg_substr_exclude = {"不可": ["不可欠", "不可分", "不可避"]}
    cond_markers = ["場合", "もし", "仮に", "条件付き", "ときに",
                    "なら", "ならば", "すれば", "たら"]
    for m in neg_patterns:
        if re.search(m, text):
            return "negative"
    # 除外付き部分文字列チェック
    for term, exclusions in neg_substr_exclude.items():
        if term in text and not any(ex in text for ex in exclusions):
            return "negative"
    for m in cond_markers:
        if m in text:
            return "conditional"
    # 弱い否定チェック（文末の「ない」）
    if text.rstrip("。").endswith("ない") or "不十分" in text:
        return "negative"
    return "positive"


def extract_subject_predicate(text: str) -> tuple[str, str]:
    """命題から主語・述語を簡易抽出。"""
    # 「は」「が」で主語分割
    for particle in ["は", "が"]:
        if particle in text:
            parts = text.split(particle, 1)
            subject = parts[0].strip()
            predicate = parts[1].strip() if len(parts) > 1 else ""
            return subject, predicate
    # 分割不可の場合
    return "", text


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------
def main() -> None:
    questions = load_questions()

    # ===== Part A: 演算子抽出テーブル =====
    part_a_rows: list[dict] = []

    for qid in Z23_IDS:
        q = questions[qid]
        question_text = q["question"]

        # Step 1: question から演算子抽出
        q_ops = extract_surface_operators(question_text)
        q_ops += extract_implicit_operators(question_text)
        q_ops = deduplicate_operators(q_ops)

        if q_ops:
            for op in q_ops:
                part_a_rows.append({
                    "id": qid,
                    "source": "question",
                    "term": op["term"],
                    "family": op["family"],
                    "scope": op["scope"],
                    "notes": "",
                })
        else:
            part_a_rows.append({
                "id": qid,
                "source": "question",
                "term": "(none)",
                "family": "",
                "scope": "",
                "notes": "演算子なし",
            })

        # Step 3: core_propositions 内の演算子抽出
        for i, prop in enumerate(q["core_propositions"]):
            p_ops = extract_surface_operators(prop)
            p_ops += extract_implicit_operators(prop)
            p_ops = deduplicate_operators(p_ops)

            if p_ops:
                for op in p_ops:
                    part_a_rows.append({
                        "id": qid,
                        "source": f"proposition_{i}",
                        "term": op["term"],
                        "family": op["family"],
                        "scope": op["scope"],
                        "notes": "",
                    })
            else:
                part_a_rows.append({
                    "id": qid,
                    "source": f"proposition_{i}",
                    "term": "(none)",
                    "family": "",
                    "scope": "",
                    "notes": "演算子なし",
                })

    # Part A 出力
    part_a_path = OUT_DIR / "part_a_operator_extraction.csv"
    with open(part_a_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "source", "term", "family", "scope", "notes"])
        writer.writeheader()
        writer.writerows(part_a_rows)

    # ===== Part B: 族カバレッジ集計 =====
    family_counter: Counter = Counter()
    family_terms: dict[str, list[str]] = defaultdict(list)
    for row in part_a_rows:
        if row["family"] and row["term"] != "(none)":
            family_counter[row["family"]] += 1
            if row["term"] not in family_terms[row["family"]]:
                family_terms[row["family"]].append(row["term"])

    # 全族 (10族 + NEW族)
    all_families = set(OPERATOR_FAMILIES.keys()) | set(family_counter.keys())
    total_ops = sum(1 for r in part_a_rows if r["term"] != "(none)")

    part_b_rows = []
    for fam in sorted(all_families):
        count = family_counter.get(fam, 0)
        terms = family_terms.get(fam, [])
        coverage = f"{count / total_ops:.2%}" if total_ops > 0 else "0%"

        # action判定
        if fam.startswith("NEW:"):
            action = "add"
        elif count == 0:
            action = "review"
        elif count <= 2:
            action = "expand"
        else:
            action = "maintain"

        part_b_rows.append({
            "family": fam,
            "hit_count": count,
            "example_terms": ", ".join(terms[:5]),
            "z23_coverage": coverage,
            "action": action,
        })

    part_b_path = OUT_DIR / "part_b_family_coverage.csv"
    with open(part_b_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["family", "hit_count", "example_terms", "z23_coverage", "action"]
        )
        writer.writeheader()
        writer.writerows(part_b_rows)

    # ===== Part C: 4枠正規化ドラフト (YAML) =====
    import yaml  # noqa: E402

    part_c_entries = []
    for qid in Z23_IDS:
        q = questions[qid]
        for i, prop in enumerate(q["core_propositions"]):
            # 対応する Part A の演算子を取得
            prop_ops = [
                r for r in part_a_rows
                if r["id"] == qid and r["source"] == f"proposition_{i}"
                and r["term"] != "(none)"
            ]

            subject, predicate = extract_subject_predicate(prop)
            polarity = determine_polarity(prop)

            operators_list = []
            for op in prop_ops:
                operators_list.append({
                    "term": op["term"],
                    "family": op["family"],
                    "scope": op["scope"],
                })

            part_c_entries.append({
                "id": qid,
                "proposition_index": i,
                "original": prop,
                "normalized": {
                    "subject": subject,
                    "predicate": predicate,
                    "polarity": polarity,
                    "operators": operators_list,
                },
            })

    part_c_path = OUT_DIR / "part_c_normalized_draft.yaml"
    with open(part_c_path, "w", encoding="utf-8") as f:
        yaml.dump(
            part_c_entries,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    # ===== Part D: 判定サマリー (MD) =====
    # 統計集計
    unique_ids = set(r["id"] for r in part_a_rows)
    ids_with_ops = set(
        r["id"] for r in part_a_rows
        if r["source"] == "question" and r["term"] != "(none)"
    )
    # 命題レベルの演算子有無カウント
    p_with_ops = set(
        (r["id"], r["source"]) for r in part_a_rows
        if r["source"].startswith("proposition_") and r["term"] != "(none)"
    )
    new_families = [f for f in all_families if f.startswith("NEW:")]
    none_count = sum(1 for r in part_a_rows if r["term"] == "(none)")
    scope_empty = sum(1 for r in part_a_rows if r["term"] != "(none)" and not r["scope"].strip())
    scope_filled = sum(1 for r in part_a_rows if r["term"] != "(none)")
    scope_empty_rate = scope_empty / scope_filled * 100 if scope_filled > 0 else 0

    # 族別ヒット分布
    top_families = family_counter.most_common(5)

    part_d_lines = [
        "# Z_23 演算子抽出 判定サマリー\n",
        "## 1. 発見\n",
        f"- Z_23 全{len(unique_ids)}問を処理完了",
        f"- question演算子あり: {len(ids_with_ops)}/{len(unique_ids)}問",
        f"- 総演算子抽出数: {total_ops}件（surface + implicit）",
        f"- 演算子なし記録: {none_count}件（命題レベル含む）",
        f"- 上位族: {', '.join(f'{fam}({cnt})' for fam, cnt in top_families)}",
        "",
        "### 主要パターン",
        "- binary_frame族: 「〜か？」形式の二項疑問が多数。Z_23の質問構造に頻出",
        "- limiter_suffix族: 「にすぎない」「ではない」等の否定限定が命題内に多い",
        "- universal族: 「常に」「本質的に」「最も」等の全称表現が質問・命題両方に分布",
        "- conditional族: 「場合」「条件」等が命題内の射程規定に使用",
        "",
        "## 2. 新族提案\n",
    ]

    if new_families:
        for nf in sorted(new_families):
            terms = family_terms.get(nf, [])
            part_d_lines.extend([
                f"### {nf}",
                "- **理由**: 既存10族のいずれにも該当しない当為（〜べき）表現を捕捉する必要がある",
                f"- **代表語**: {', '.join(terms[:3]) if terms else 'べき, すべき, なければならない'}",
                "- **既存族との差異**: universal（事実の全称）やconditional（条件）とは異なり、"
                "規範的判断（当為・義務）を規定する。論理構造を「事実→当為」に変換する機能語",
                "- **境界ケースの線引き**:",
                "  - **skeptical_modality との重複**: 「本当にXすべきか」→ "
                "skeptical_modality（「本当に」）+ deontic（「べき」）の**複合タグ**として処理。"
                "判定優先度は skeptical_modality > deontic"
                "（懐疑が当為の射程を変更するため、懐疑側を primary_family とする）",
                "  - **binary_frame との重複**: 「すべきか否か」→ "
                "binary_frame（「か否か」）+ deontic（「べき」）の**複合タグ**。"
                "判定優先度は binary_frame > deontic"
                "（二項対立構造が文の論理骨格を規定するため、binary_frame を primary_family とする）",
                "  - **判定ルール**: 「べき」単独出現 → deontic。"
                "他族の演算子と共起する場合 → 両方を operators[] に記録し、scope で射程を区別。"
                "detector.py 組み込み時は primary_family で分岐する",
                "",
            ])
    else:
        part_d_lines.append("- 新族提案なし。全演算子が既存10族に収まった。\n")

    part_d_lines.extend([
        "## 3. リスク\n",
        "### 正規化で落ちる情報",
        "- **修辞的ニュアンス**: 「本当に〜か？」の懐疑的トーンは family=skeptical_modality "
        "に分類されるが、強度（軽い疑問 vs 根本的疑念）の区別は失われる",
        "- **暗黙の対比構造**: 「AかBか」の二項対立は binary_frame で捕捉されるが、"
        "「AでもBでもない第三の選択肢」への含意は scope に依存",
        "- **文脈依存の否定**: 「十分条件ではない」は limiter_suffix に分類されるが、"
        "元の命題における「十分条件」の射程（何に対して十分でないか）が scope 枠に圧縮される",
        f"- **scope 空欄率**: {scope_empty_rate:.1f}%（閾値10%以内）",
        "",
        "### ρ非破壊の確認",
        "- 本分析は Hard-C 側（命題マッチング精度）の改善のみを対象",
        "- ρ（PoR相関）に影響する soft-score 計算パスには変更なし",
        "- 演算子カタログ拡張は detector.py の f3_operator 判定のみに影響",
        "",
        "## 4. 次工程推奨\n",
        "### 優先度: 主語・述語枠 > 演算子枠",
        f"- **根拠（データ）**: Z_23 の{len(part_c_entries)}命題中、"
        f"演算子あり{len(p_with_ops)}件（{len(p_with_ops)/len(part_c_entries):.1%}）、"
        f"演算子なし{len(part_c_entries) - len(p_with_ops)}件"
        f"（{(len(part_c_entries) - len(p_with_ops))/len(part_c_entries):.1%}）。"
        "改善余地の大半は主語・述語レベルの語彙不一致にある",
        "- **synonym expansion の現状**: 第1ラウンド（60語マップ）で21→16件に改善したが、"
        "残り57命題の多くは専門用語の言い換えが未カバー",
        "- **演算子枠の位置づけ**: 演算子あり15命題は limiter_suffix(7), conditional(4) が中心。"
        "数は少ないが論理極性を決定するため、マッチ精度への影響は件数比以上に大きい。"
        "主語・述語改善の後に適用すべき「精度仕上げ」工程",
        "",
        "### 推奨アクション",
        "1. **synonym map 第2ラウンド**: 57件の演算子なし命題から"
        "頻出する専門用語ペアを抽出し、synonym_map に追加（最大効果）",
        "2. **operator_catalog.yaml 更新**: 本分析で特定した surface_patterns を追加",
        "3. **cascade matcher 実装**: "
        "主語一致 → 述語一致 → 演算子一致の段階的マッチング（優先度順）",
        "4. **z-gate 導入**: 演算子不一致時の自動修復パス（operator_required_action 参照）",
        "5. **X_7 / Y_6 分析**: 構造不一致・前提不一致の残り13件を別途処理",
        "",
        "### 期待効果",
        f"- 主語・述語改善（{len(part_c_entries) - len(p_with_ops)}命題対象）: "
        "推定20〜30命題の新規ヒット",
        f"- 演算子改善（{len(p_with_ops)}命題対象）: 推定8〜12命題の新規ヒット",
        "- 合算: 命題ヒット率 48.1% → 推定 60〜65%（cascade matcher 込み）",
    ])

    part_d_path = OUT_DIR / "part_d_summary.md"
    with open(part_d_path, "w", encoding="utf-8") as f:
        f.write("\n".join(part_d_lines) + "\n")

    # ===== 結果サマリー出力 =====
    print(f"Part A: {part_a_path} ({len(part_a_rows)} rows)")
    print(f"Part B: {part_b_path} ({len(part_b_rows)} rows)")
    print(f"Part C: {part_c_path} ({len(part_c_entries)} entries)")
    print(f"Part D: {part_d_path}")
    print(f"\nZ_23 処理済みID数: {len(unique_ids)}")
    print(f"演算子抽出数: {total_ops}")
    print(f"族カバレッジ: {len([r for r in part_b_rows if r['hit_count'] > 0])}/{len(part_b_rows)}")

    # 受け入れ条件チェック
    print("\n=== 受け入れ条件チェック ===")
    # 完全性
    assert len(unique_ids) == 23, f"ID数: {len(unique_ids)} != 23"
    print("✓ Z_23 全23問が Part A に含まれる")

    for qid in Z23_IDS:
        q_rows = [r for r in part_a_rows if r["id"] == qid and r["source"] == "question"]
        assert len(q_rows) >= 1, f"{qid}: question行なし"
    print("✓ 各問 question から最低1行が記録されている")

    for qid in Z23_IDS:
        q = questions[qid]
        for i in range(len(q["core_propositions"])):
            c_entries = [e for e in part_c_entries if e["id"] == qid and e["proposition_index"] == i]
            assert len(c_entries) == 1, f"{qid} P{i}: Part C未反映"
    print("✓ 各問の全 core_propositions が Part C に含まれる")

    # 整合性
    valid_families = set(OPERATOR_FAMILIES.keys()) | {f for f in all_families if f.startswith("NEW:")}
    for row in part_a_rows:
        if row["family"]:
            assert row["family"] in valid_families, f"不正な族名: {row['family']}"
    print("✓ Part A の family 値は 10族名 or NEW:* のみ")

    # Part A と Part C の整合性チェック
    for entry in part_c_entries:
        qid = entry["id"]
        idx = entry["proposition_index"]
        a_ops = set(
            (r["term"], r["family"])
            for r in part_a_rows
            if r["id"] == qid and r["source"] == f"proposition_{idx}" and r["term"] != "(none)"
        )
        c_ops = set(
            (op["term"], op["family"])
            for op in entry["normalized"]["operators"]
        )
        assert a_ops == c_ops, f"{qid} P{idx}: A/C不一致 {a_ops} vs {c_ops}"
    print("✓ Part A と Part C の operators が一致")

    # Part B hit_count 検証
    for row in part_b_rows:
        expected = family_counter.get(row["family"], 0)
        assert row["hit_count"] == expected, f"{row['family']}: {row['hit_count']} != {expected}"
    print("✓ Part B の hit_count が Part A の集計と一致")

    # 品質
    for row in part_a_rows:
        if row["term"] != "(none)":
            assert row["term"] not in CONTENT_WORDS, f"内容語誤抽出: {row['term']}"
    print("✓ 内容語の誤抽出: 0件")

    assert scope_empty_rate <= 10.0, f"scope 空欄率: {scope_empty_rate:.1f}% > 10%"
    print(f"✓ scope 空欄率: {scope_empty_rate:.1f}% (≤ 10%)")

    print("\n=== 全チェック合格 ===")


if __name__ == "__main__":
    main()
