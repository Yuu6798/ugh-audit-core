"""detector.py — 検出層

テキスト（question + response）から Evidence を生成する。
推論ゼロ: パターンマッチと文字列照合のみ。embedding/LLM呼び出し禁止。
辞書ファイル（YAML）を実行時にロードする。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from ugh_calculator import Evidence

# --- YAML辞書のロード ---
_REGISTRY_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "registry"


def _load_yaml(filename: str) -> dict:
    """registry/ 配下のYAMLファイルをロードする"""
    path = _REGISTRY_DIR / filename
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_reserved_terms() -> List[dict]:
    data = _load_yaml("reserved_terms.yaml")
    return data.get("terms", [])


def _load_operators() -> List[dict]:
    data = _load_yaml("operator_catalog.yaml")
    return data.get("operators", [])


def _load_premise_frames() -> Dict[str, dict]:
    data = _load_yaml("premise_frames.yaml")
    return data.get("frames", {})


# --- 補助関数 ---

def _extract_keywords(text: str) -> List[str]:
    """テキストからキーワードを抽出する（簡易: 漢字列、カタカナ列、英単語）"""
    patterns = [
        r'[\u4e00-\u9fff]{2,}',       # 漢字2文字以上
        r'[\u30a0-\u30ff]{2,}',       # カタカナ2文字以上
        r'[A-Za-zΔ][A-Za-z0-9Δ]{1,}', # 英単語2文字以上（ΔE対応）
    ]
    keywords = []
    for pat in patterns:
        keywords.extend(re.findall(pat, text))
    return keywords


def _extract_content_chunks(text: str) -> List[str]:
    """命題マッチング用: テキストから内容語チャンクを抽出する

    粒度を細かくして柔軟にマッチさせる。
    """
    chunks: List[str] = []

    # 漢字ブロック（2+）
    kanji_blocks = re.findall(r'[\u4e00-\u9fff]{2,}', text)
    chunks.extend(kanji_blocks)

    # 長い漢字ブロックを2文字ペアにも分解
    for block in kanji_blocks:
        if len(block) >= 3:
            for i in range(len(block) - 1):
                chunks.append(block[i:i + 2])

    # カタカナ語（2+）
    kata = re.findall(r'[\u30a0-\u30ff]{2,}', text)
    chunks.extend(kata)

    # 英単語（2+、ΔE対応）
    eng = re.findall(r'[A-Za-zΔ][A-Za-z0-9Δ]{1,}', text)
    chunks.extend(eng)

    # ストップワード除去: 1文字漢字ペアで汎用すぎるもの
    stopwords = {"場合", "以上", "以下", "可能", "必要", "問題"}
    return list(set(ch for ch in chunks if ch not in stopwords))


def _sentence_contains(sentence: str, surface: str) -> bool:
    """文中にsurfaceが含まれるか（大文字小文字区別なし）"""
    return surface.lower() in sentence.lower()


def _split_sentences(text: str) -> List[str]:
    """テキストを文に分割する"""
    parts = re.split(r'[。！？\n]+', text)
    return [p.strip() for p in parts if p.strip()]


def check_f1_anchor(
    question_text: str,
    response_text: str,
    reserved_terms: Optional[List[dict]] = None,
) -> float:
    """f1_anchor（主題逸脱）を検出する

    question_textから主題語を抽出し、response_textでの出現率を計算。
    出現率 < 0.3 → 1.0（逸脱）、< 0.6 → 0.5（やや逸脱）、else → 0.0
    """
    # 主題語: 質問文からキーワードを抽出
    q_keywords = _extract_keywords(question_text)
    if not q_keywords:
        return 0.0

    # 予約語グループ: canonical/aliasesのいずれかがヒットすれば1カウント
    # (エイリアスは代替であり累積要件ではない)
    term_groups: List[List[str]] = []
    if reserved_terms:
        for term_def in reserved_terms:
            canonical = term_def.get("canonical", "")
            if canonical in question_text:
                group = [canonical] + [
                    a for a in term_def.get("aliases", []) if a
                ]
                term_groups.append(group)

    # 重複排除
    q_keywords = list(set(q_keywords))
    if not q_keywords and not term_groups:
        return 0.0

    # response_textでの出現率
    # 通常キーワード: 各キーワードが含まれていれば1ヒット
    hit_count = sum(1 for kw in q_keywords if kw in response_text)
    total = len(q_keywords)

    # 予約語グループ: グループ内のいずれかが含まれていれば1ヒット
    for group in term_groups:
        total += 1
        if any(term in response_text for term in group):
            hit_count += 1

    if total == 0:
        return 0.0
    coverage = hit_count / total

    if coverage < 0.3:
        return 1.0
    if coverage < 0.6:
        return 0.5
    return 0.0


def check_f2_unknown(
    response_text: str,
    reserved_terms: List[dict],
) -> Tuple[float, str]:
    """f2_unknown（用語捏造）を検出する

    reserved_terms.yaml の各 term について:
    1. response_text に canonical または aliases が出現するか確認
    2. 出現する場合、同一文中に forbidden_reinterpretations の surface が現れるか確認
    """
    max_severity = 0.0
    detail = ""

    sentences = _split_sentences(response_text)

    for term_def in reserved_terms:
        canonical = term_def.get("canonical", "")
        aliases = term_def.get("aliases", [])
        forbidden = term_def.get("forbidden_reinterpretations", [])

        # 予約語がresponseに出現するかチェック
        term_surfaces = [canonical] + aliases
        term_found_in_response = any(
            s in response_text for s in term_surfaces if s
        )

        if not term_found_in_response:
            # 質問に含まれる予約語がresponseに一切出現しない場合もf2リスク
            continue

        # 各文について forbidden surface をチェック
        for sentence in sentences:
            has_term = any(s in sentence for s in term_surfaces if s)
            if not has_term:
                continue

            for fb in forbidden:
                fb_surface = fb.get("surface", "")
                if fb_surface and _sentence_contains(sentence, fb_surface):
                    max_severity = 1.0
                    detail = (
                        f"「{canonical}」を「{fb_surface}」に勝手展開"
                    )
                    return max_severity, detail

        # 予約語はあるが留保表現がない場合 → 0.5
        # 留保表現: 「と定義される」「と呼ばれる」「独自の概念」等
        caution_indicators = [
            "と定義", "と呼ばれ", "独自の", "特有の", "固有の",
            "という概念", "とされる", "意味する",
        ]
        has_caution = any(ind in response_text for ind in caution_indicators)
        if not has_caution and max_severity < 0.5:
            max_severity = 0.5
            detail = f"「{canonical}」に対する留保表現なし"

    return max_severity, detail


def check_f3_operator(
    question_text: str,
    response_text: str,
    operators: List[dict],
) -> float:
    """f3_operator（演算子無処理）を検出する

    question_text 中の演算子パターンをスキャン → response_text が対応しているか判定
    """
    max_severity = 0.0

    for op in operators:
        surface_patterns = op.get("surface_patterns", [])
        response_indicators = op.get("response_indicators", [])

        # 質問文中に演算子が存在するか
        op_found = any(pat in question_text for pat in surface_patterns)
        if not op_found:
            continue

        # 回答文中に対応表現があるか
        has_response = any(ind in response_text for ind in response_indicators)

        if not has_response:
            max_severity = max(max_severity, 1.0)
        else:
            # 部分的対応: 対応表現が1つだけ
            response_count = sum(1 for ind in response_indicators if ind in response_text)
            if response_count <= 1:
                max_severity = max(max_severity, 0.5)

    return max_severity


def check_f4_premise(
    question_text: str,
    response_text: str,
    trap_type: str,
    frames: Dict[str, dict],
) -> Tuple[float, str]:
    """f4_premise（前提受容）を検出する

    trap_typeに基づき、質問の前提に対する回答の態度を判定。
    """
    frame = frames.get(trap_type)
    if not frame:
        return 0.0, ""

    detection_hint = frame.get("detection_hint", {})
    challenge_indicators = detection_hint.get("challenge_indicators", [])

    # 回答文中に前提挑戦の表現があるか
    challenge_count = sum(1 for ind in challenge_indicators if ind in response_text)

    if trap_type == "premise_acceptance":
        # 質問文から前提句を抽出（断定表現 + 疑問形式）
        premise_patterns = [
            r'(.{3,20})は(.{3,20})だ',
            r'(.{3,20})ため',
            r'(.{3,20})である以上',
            r'(.{3,20})という前提',
        ]
        premise_found = any(
            re.search(pat, question_text) for pat in premise_patterns
        )

        # 疑問形式（...か？/...か。/...か$）も前提が埋め込まれている
        if not premise_found and re.search(r'か[？\?。]?\s*$', question_text):
            premise_found = True

        if premise_found and challenge_count == 0:
            return 1.0, "前提への対応表現なし"
        if premise_found and challenge_count <= 1:
            return 0.5, "前提への対応が部分的"
        return 0.0, ""

    if trap_type == "binary_reduction":
        # 回答が二択のみか、多面的に考察しているか
        binary_indicators = [
            "第三の", "別の視点", "二項対立", "多面的", "グラデーション",
            "スペクトラム", "だけでなく", "それ以外", "他の可能性",
            "複数", "様々", "いくつかの", "一方で", "しかし",
            "観点", "側面", "条件", "場合分け",
        ]
        has_third = any(ind in response_text for ind in binary_indicators)
        if not has_third and challenge_count == 0:
            return 1.0, "二項対立を崩していない"
        if not has_third:
            return 0.5, "二項対立への対応が部分的"
        return 0.0, ""

    # その他のtrap_type: challenge_indicatorsの有無で判定
    # ただし汎用的な批判的思考表現もカウントする
    generic_challenge = [
        "しかし", "一方で", "ただし", "必ずしも", "とは限らない",
        "問題", "懸念", "批判", "疑問", "限界",
    ]
    generic_count = sum(1 for ind in generic_challenge if ind in response_text)

    if challenge_count == 0 and generic_count == 0:
        return 0.5, "前提への対応表現なし"
    return 0.0, ""


def _extract_content_bigrams(text: str) -> set:
    """テキストから内容語バイグラム集合を抽出する

    漢字2文字ペア、カタカナ3文字以上、英単語をキーとして使用。
    ひらがな・句読点・空白は除去して漢字/カタカナ/英字のみ対象。
    """
    bigrams: set = set()

    # 漢字2文字ペア（連続する漢字からすべての2文字組を生成）
    kanji_runs = re.findall(r'[\u4e00-\u9fff]+', text)
    for run in kanji_runs:
        for i in range(len(run) - 1):
            bigrams.add(run[i:i + 2])

    # カタカナ語（3文字以上で意味のある語）
    kata_words = re.findall(r'[\u30a0-\u30ff]{3,}', text)
    bigrams.update(kata_words)

    # 英単語（2文字以上）
    eng_words = re.findall(r'[A-Za-zΔ][A-Za-z0-9Δ]{1,}', text)
    bigrams.update(w.lower() for w in eng_words)

    return bigrams


# --- 類義語辞書 ---
# 命題中の語彙を回答中の表現にマッピングするための辞書。
# 決定的（辞書照合のみ）。embedding/推論なし。
# キー: 命題側の表現、値: 回答側で同等の意味を持つ表現のリスト。
_SYNONYM_MAP: Dict[str, List[str]] = {
    # 用語の別称
    "llm": ["ai"],
    # 概念の言い換え
    "条件": ["基準", "要件"],
    "話者": ["証言者", "発話者"],
    "正直": ["信頼", "誠実"],
    "仮説": ["理論", "学説"],
    "反証": ["覆す", "否定", "誤り"],
    "妥当": ["有効", "適切"],
    "拒否": ["否定", "退け"],
    "功利": ["帰結", "効用"],
    "閾値": ["限界", "境界"],
    "検証": ["確認", "実証", "証明"],
    "紛争": ["戦争", "武力", "軍事"],
    "低下": ["下がる", "減少"],
    "空洞": ["形骸", "不在"],
    "集合": ["集約", "総体"],
    "トークン": ["単語"],
    "配分": ["分配", "割当"],
    "事例": ["実例"],
    "帰属": ["所在", "帰責"],
}


def _expand_with_synonyms(bigrams: set) -> set:
    """命題バイグラム集合を類義語で拡張する"""
    expanded = set(bigrams)
    for bg in bigrams:
        if bg in _SYNONYM_MAP:
            for syn in _SYNONYM_MAP[bg]:
                expanded.add(syn)
                # 類義語が漢字2文字以上の場合、そのバイグラムも追加
                if len(syn) >= 2 and all('\u4e00' <= c <= '\u9fff' for c in syn):
                    for i in range(len(syn) - 1):
                        expanded.add(syn[i:i + 2])
    return expanded


# 最小overlap数: 表層一致（2語のみの偶然一致）を排除する
_MIN_OVERLAP = 3


def check_propositions(
    response_text: str,
    core_props: List[str],
    disqualifying: Optional[List[str]] = None,
    acceptable_variants: Optional[List[str]] = None,
) -> Tuple[int, List[int], List[int]]:
    """命題検出: core_propositionsの各命題がresponse中に含まれるかを判定

    方法: 漢字バイグラム（2文字ペア）の再現率 + 類義語拡張で判定。
    漢字2文字ペアは日本語の内容語の最小単位であり、
    表現が異なっても核心概念が共有されていれば一致する。
    類義語辞書により、「LLM→AI」「条件→基準」等の語彙差を吸収。

    閾値: recall >= 0.35 AND overlap >= 3。
    最小overlap要件により、2語のみの偶然一致を排除する。
    """
    if not core_props:
        return 0, [], []

    # disqualifying_shortcuts: 回答に含まれていたら全命題をmissにする
    if disqualifying:
        for shortcut in disqualifying:
            if shortcut and shortcut in response_text:
                miss_ids = list(range(len(core_props)))
                return 0, [], miss_ids

    resp_bigrams = _extract_content_bigrams(response_text)

    # acceptable_variants のバイグラムを回答側に追加（正当な言い換えとして認める）
    if acceptable_variants:
        for variant in acceptable_variants:
            if variant and variant in response_text:
                resp_bigrams |= _extract_content_bigrams(variant)

    hit_ids: List[int] = []
    miss_ids: List[int] = []

    for i, prop in enumerate(core_props):
        prop_bigrams = _extract_content_bigrams(prop)
        if not prop_bigrams:
            miss_ids.append(i)
            continue

        # 類義語拡張: 命題側のバイグラムに類義語を追加
        expanded = _expand_with_synonyms(prop_bigrams)

        # 拡張後のバイグラムがresp側にどれだけ含まれるかを見る（再現率）
        overlap_set = expanded & resp_bigrams
        overlap_count = len(overlap_set)
        recall = overlap_count / len(prop_bigrams)  # 分母は元のバイグラム数

        if recall >= 0.35 and overlap_count >= _MIN_OVERLAP:
            hit_ids.append(i)
        else:
            miss_ids.append(i)

    return len(hit_ids), hit_ids, miss_ids


def detect(
    question_id: str,
    response_text: str,
    question_meta: dict,
) -> Evidence:
    """検出層: テキスト → Evidence

    全計算が決定的パターンマッチ。embedding/LLM呼び出しなし。
    """
    # YAMLデータのロード
    reserved_terms = _load_reserved_terms()
    operators = _load_operators()
    frames = _load_premise_frames()

    # メタデータから必要情報を取得
    question_text = question_meta.get("question", "")
    core_props = question_meta.get("core_propositions", [])
    disqualifying = question_meta.get("disqualifying_shortcuts", [])
    acceptable_variants = question_meta.get("acceptable_variants", [])
    trap_type = question_meta.get("trap_type", "")

    # f1: 主題逸脱
    f1 = check_f1_anchor(question_text, response_text, reserved_terms)

    # f2: 用語捏造
    f2, f2_detail = check_f2_unknown(response_text, reserved_terms)

    # f3: 演算子無処理
    f3 = check_f3_operator(question_text, response_text, operators)

    # f4: 前提受容
    f4, f4_detail = check_f4_premise(question_text, response_text, trap_type, frames)

    # 命題検出
    hits, hit_ids, miss_ids = check_propositions(
        response_text, core_props, disqualifying, acceptable_variants
    )

    return Evidence(
        question_id=question_id,
        f1_anchor=f1,
        f2_unknown=f2,
        f3_operator=f3,
        f4_premise=f4,
        f2_detail=f2_detail,
        f4_detail=f4_detail,
        propositions_hit=hits,
        propositions_total=len(core_props),
        hit_ids=hit_ids,
        miss_ids=miss_ids,
    )
