"""detector.py — 検出層

テキスト（question + response）から Evidence を生成する。
推論ゼロ: パターンマッチと文字列照合のみ。embedding/LLM呼び出し禁止。
辞書ファイル（YAML）を実行時にロードする。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

import yaml

from ugh_calculator import Evidence, _compute_delta_e, _compute_s

# --- cascade フォールバック import ---
try:
    from cascade_matcher import (
        load_model as _cascade_load_model,
        tier2_candidate,
        tier3_filter,
    )
    _HAS_CASCADE = True
except ImportError:
    _HAS_CASCADE = False

_logger = logging.getLogger(__name__)

# --- Model C' (bottleneck) parameters ---
# 暫定値: n=20 LOO-CV で検証済み (ρ=0.8018)
# n=48 アノテーション完了後に再校正する
QUALITY_ALPHA = 0.4
QUALITY_BETA = 0.0
QUALITY_GAMMA = 0.8
QUALITY_MODEL_NAME = "bottleneck_v1"

# SBert モデルキャッシュ（初回ロード時に設定）
_cascade_model = None
_cascade_load_attempted = False


def _get_cascade_model():
    """SBert モデルをキャッシュ付きでロードする。失敗時は None を返し、再試行しない。"""
    global _cascade_model, _cascade_load_attempted
    if _cascade_model is not None:
        return _cascade_model
    if _cascade_load_attempted or not _HAS_CASCADE:
        return None
    _cascade_load_attempted = True
    try:
        _cascade_model = _cascade_load_model()
        return _cascade_model
    except Exception as e:
        _logger.warning("cascade SBert モデルロード失敗（Tier 1 のみで動作）: %s", e)
        return None


# --- 演算子検出 ---

class OperatorInfo(NamedTuple):
    """命題中の演算子検出結果"""
    family: str      # 演算子族 (negation / deontic / skeptical_modality / binary_frame)
    token: str       # マッチしたトークン
    position: int    # 命題中の開始位置


# 演算子カタログ: 族定義と検出パターン
# priority が小さいほど共起時に優先される
# 共起ルール:
#   deontic + negation → deontic 優先 (「べきではない」は当為表現)
#   skeptical_modality + binary_frame → binary_frame 優先
OPERATOR_CATALOG: Dict[str, dict] = {
    "negation": {
        "patterns": [
            r"ではない",
            r"でない",
            r"にならない",
            r"しない",
            r"できない",
            r"不十分",
            r"不可能",
            r"未(?!来|満)[\u4e00-\u9fff]{1,4}",
            r"保証しない",
            r"[\u4e00-\u9fff]ない$",
        ],
        "effect": "polarity_flip",
        "priority": 2,
        "response_markers": [
            "ではない", "ではなく", "しない", "できない",
            "ではありません", "ありません",
            "不十分", "不可能", "限らない",
            "必ずしも", "とは言えない",
            "未解", "未確", "未検", "未整", "未発",
        ],
    },
    "deontic": {
        "patterns": [
            r"べきではない",
            r"すべきではない",
            r"すべき",
            r"べき",
        ],
        "effect": "normative_flag",
        "priority": 1,
        "response_markers": [
            "べき", "すべき", "必要", "求められる",
            "義務", "当為", "規範",
        ],
    },
    "skeptical_modality": {
        "patterns": [
            r"かもしれない",
            r"とは限らない",
            r"可能性がある",
            r"不確[実定]",
            r"明確ではない",
        ],
        "effect": "certainty_downgrade",
        "priority": 3,
        "response_markers": [
            "かもしれない", "可能性", "必ずしも",
            "とは限らない", "不確実", "不明", "断定できない",
        ],
    },
    "binary_frame": {
        "patterns": [
            r"ではなく",
            r"よりも",
            r"二項対立",
            r"二択",
            r"か[\u4e00-\u9fff]*かの",
        ],
        "effect": "contrastive_split",
        "priority": 1,
        "response_markers": [
            "ではなく", "二項対立", "二択", "対立",
            "多面的", "単純化できない", "区別", "異なる",
        ],
    },
}


def detect_operator(proposition: str) -> Optional[OperatorInfo]:
    """命題文字列から演算子を検出し、最優先の1件を返す

    複数族がマッチした場合は priority (小さいほど優先) で解決する。
    同一 priority 内では命題中で先に出現する方を採用する。
    """
    matches: List[OperatorInfo] = []
    for family, config in OPERATOR_CATALOG.items():
        # 族内の全パターンをスキャンし、最早位置のマッチを採用する
        best_in_family: Optional[OperatorInfo] = None
        for pattern in config["patterns"]:
            m = re.search(pattern, proposition)
            if m and (best_in_family is None or m.start() < best_in_family.position):
                best_in_family = OperatorInfo(
                    family=family,
                    token=m.group(),
                    position=m.start(),
                )
        if best_in_family is not None:
            matches.append(best_in_family)
    if not matches:
        return None
    # priority昇順 → position昇順 でソートし、最優先を返す
    matches.sort(key=lambda info: (OPERATOR_CATALOG[info.family]["priority"], info.position))
    return matches[0]

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

    # ドメイン語彙マップ: 質問に出現する予約語のaliasesのみを収集
    # 質問と無関係な予約語で偽ヒットしないよう限定する
    domain_surfaces: List[str] = []
    if reserved_terms:
        for term_def in reserved_terms:
            c = term_def.get("canonical", "")
            aliases = [a for a in term_def.get("aliases", []) if a]
            if c and c in question_text:
                domain_surfaces.extend(aliases)
            else:
                for a in aliases:
                    if a in question_text:
                        domain_surfaces.append(c)
                        domain_surfaces.extend(aliases)
                        break

    # response_textでの出現率
    # 通常キーワード: 直接出現 or ドメイン語彙の存在でヒット
    domain_in_response = [ds for ds in domain_surfaces if ds in response_text]
    hit_count = 0
    for kw in q_keywords:
        if kw in response_text:
            hit_count += 1
        elif domain_in_response:
            # キーワード自体は無いがドメイン語彙が回答にある → 主題に沿っている
            hit_count += 1
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
    question_text: str = "",
) -> Tuple[float, str]:
    """f2_unknown（用語捏造）を検出する

    reserved_terms.yaml の各 term について:
    1. response_text に canonical または aliases が出現するか確認
    2. 出現する場合、同一文中に forbidden_reinterpretations の surface が現れるか確認
    3. 不在検出: 質問にある予約語がresponseに一切ない → 0.5
    4. 未到達検出: 予約語は出現するが「知らない」等の表明がある → 0.5
    5. caution check: 強caution表現のみ、局所窓(予約語出現文±1文)で判定
    """
    max_severity = 0.0
    detail = ""

    sentences = _split_sentences(response_text)

    # 強caution: 予約語の定義・説明を示す表現のみ
    # 局所窓(予約語出現文±1文)内でのみ判定するため、汎用表現の誤ヒットを抑制
    strong_caution = [
        "と定義",       # 「PoRはXXと定義される」
        "と呼ばれ",     # 「XXと呼ばれる概念」
        "特有の",       # 「UGHer特有の」
        "固有の",       # 「UGHer固有の」
        "という概念",   # 局所窓内なら予約語に直結（q029型）
    ]

    # 未到達マーカー: 予約語は出現するが理解していない表明
    uncertainty_markers = [
        "一般的ではありません", "一般的ではない",
        "不明", "知りません", "判断できない",
        "存在しない", "見つかりません",
        "もしかすると", "誤記",
    ]

    for term_def in reserved_terms:
        canonical = term_def.get("canonical", "")
        aliases = term_def.get("aliases", [])
        forbidden = term_def.get("forbidden_reinterpretations", [])

        # 予約語がresponseに出現するかチェック
        term_surfaces = [canonical] + aliases
        term_found_in_response = any(
            s in response_text for s in term_surfaces if s
        )

        # --- 不在検出 ---
        # 質問に予約語があるのにresponseに一切出現しない → f2=0.5
        if not term_found_in_response:
            if question_text:
                question_has_term = any(s in question_text for s in term_surfaces if s)
                if question_has_term:
                    if max_severity < 0.5:
                        max_severity = 0.5
                        detail = f"「{canonical}」が質問にあるが回答に不在"
            continue

        # 各文について forbidden surface をチェック
        for sentence in sentences:
            has_term = any(s in sentence for s in term_surfaces if s)
            if not has_term:
                continue

            for fb in forbidden:
                fb_surface = fb.get("surface", "")
                # forbidden surfaceがcanonicalのsubstringならスキップ（自己ペナルティ防止）
                if fb_surface and fb_surface in canonical:
                    continue
                if fb_surface and _sentence_contains(sentence, fb_surface):
                    max_severity = 1.0
                    detail = (
                        f"「{canonical}」を「{fb_surface}」に勝手展開"
                    )
                    return max_severity, detail

        # 予約語はあるが forbidden_reinterpretations に一つもヒットしなかった場合:
        # forbidden定義がある用語のみ留保チェック（正しい用法の用語はスキップ）
        if not forbidden:
            continue

        # --- 局所窓の構築 ---
        # 予約語が出現する文のインデックスを取得
        term_sentence_indices = set()
        for si, sentence in enumerate(sentences):
            if any(s in sentence for s in term_surfaces if s):
                term_sentence_indices.add(si)

        # 局所窓: 予約語出現文 ± 1文
        local_window_indices = set()
        for si in term_sentence_indices:
            local_window_indices.add(max(0, si - 1))
            local_window_indices.add(si)
            local_window_indices.add(min(len(sentences) - 1, si + 1))

        local_text = "".join(sentences[i] for i in sorted(local_window_indices))

        # --- 未到達検出 ---
        # 予約語は出現するが「知らない」「不明」等がある → f2=0.5
        has_uncertainty = any(um in local_text for um in uncertainty_markers)
        if has_uncertainty:
            if max_severity < 0.5:
                max_severity = 0.5
                detail = f"「{canonical}」に対する未到達表現あり"
            continue  # caution checkに進まない

        # --- 留保表現チェック（局所窓 + 強cautionのみ） ---
        has_caution = any(ind in local_text for ind in strong_caution)
        if not has_caution and max_severity < 0.5:
            max_severity = 0.5
            detail = f"「{canonical}」に対する留保表現なし"

    return max_severity, detail


def check_f3_operator(
    question_text: str,
    response_text: str,
    operators: List[dict],
) -> Tuple[float, str]:
    """f3_operator（演算子無処理）を検出する

    question_text 中の演算子パターンをスキャン → response_text が対応しているか判定。
    (severity, detected_family) を返す。
    """
    max_severity = 0.0
    detected_family = ""

    for op in operators:
        surface_patterns = op.get("surface_patterns", [])
        response_indicators = op.get("response_indicators", [])
        family = op.get("family", "")

        # 質問文中に演算子が存在するか
        # 短い汎用パターン（3文字以下）はregexで文脈を確認して偽陽性を抑制
        _CONTEXTUAL_PATTERNS = {
            "ため": r'[^。]*ため[だである。]',
            "場合": r'[^。]*場合[はにの、]',
            "より": r'[^。]*より[もは]',
            "なら": r'[^。]*なら[ばば、。]',
            "たら": r'[^。]*たら[、。]',
            "ので": r'ので(?!はない)',  # 「のではないか」内の「ので」を除外
        }
        op_found = False
        for pat in surface_patterns:
            if pat not in question_text:
                continue
            ctx_re = _CONTEXTUAL_PATTERNS.get(pat)
            if ctx_re:
                if re.search(ctx_re, question_text):
                    op_found = True
                    break
            else:
                op_found = True
                break
        if not op_found:
            continue

        # 回答文中に対応表現があるか
        # 固定indicatorに加え、意味的に同等な汎用対比・否定表現も認識
        _GENERIC_CONTRAST = [
            "ではない", "ではなく", "とは限らない", "とは異なる",
            "一概に", "必ずしも", "だが", "けれど", "しかし",
            "一方で", "ただし", "むしろ",
        ]
        indicator_count = sum(1 for ind in response_indicators if ind in response_text)
        contrast_count = sum(1 for c in _GENERIC_CONTRAST if c in response_text)
        # 固定indicatorがあれば対応あり、なければ汎用対比3つ以上を要求
        has_response = indicator_count > 0 or contrast_count >= 3

        if not has_response:
            severity = 1.0
        else:
            # 固定indicatorが1つでもあれば対応完了（代替リスト、累積要件ではない）
            if indicator_count >= 1:
                severity = 0.0
            else:
                # 汎用対比のみ → 部分的対応
                severity = 0.5

        if severity > max_severity:
            max_severity = severity
            detected_family = family

    return max_severity, detected_family


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
        # 強マーカー: 明示的に二項対立を崩す表現（1つあれば十分）
        strong_markers = [
            "二項対立", "二択ではない", "二択ではなく", "単純化できない",
            "どちらでもない", "第三の", "グラデーション", "スペクトラム",
            "それ以外", "他の可能性", "別の視点",
            "多面的", "多様な視点", "多角的",
        ]
        # 非汎用弱マーカー: 一定の多面性を示す表現
        non_generic_weak = [
            "だけでなく", "複数", "様々", "いくつかの",
            "場合分け", "両方", "双方", "相互", "補完", "共存",
        ]
        # 汎用弱マーカー: GPT-4oが頻用する接続語。単独では崩した証拠にならない
        generic_weak = ["観点", "側面", "条件", "段階", "程度", "連続", "一概に"]
        # 対比マーカー: 単独では二択を崩した証拠にならない（補助的）
        contrast_markers = [
            "一方で", "しかし", "ただし", "むしろ",
            "ではない", "ではなく", "とは限らない",
            "異なる", "重なる", "単純", "還元", "区別", "境界",
        ]
        strong_count = sum(1 for m in strong_markers if m in response_text)
        has_non_generic = any(m in response_text for m in non_generic_weak)
        has_generic = any(m in response_text for m in generic_weak)
        has_contrast = any(m in response_text for m in contrast_markers)
        contrast_count_local = sum(1 for m in contrast_markers if m in response_text)

        if strong_count >= 1:
            return 0.0, ""

        # 非汎用弱マーカーがあれば多面的議論と認める（旧挙動互換）
        # 「それとも」早期リターンより先に評価し、多面的回答を正しく認識する
        if has_non_generic:
            return 0.0, ""

        # 質問に明示的二項構造（「AかBか、それともCか」）がある場合は厳格判定
        # 対比/汎用マーカーも評価し、多面的回答を過剰ペナルティしない
        if "それとも" in question_text:
            if has_generic and contrast_count_local >= 2:
                return 0.0, ""
            if has_contrast or has_generic:
                return 0.5, "二項対立への対応が部分的（明示的二項質問）"
            if challenge_count == 0:
                return 1.0, "二項対立を崩していない（明示的二項質問）"
            return 0.5, "二項対立への対応が部分的（明示的二項質問）"

        # 汎用弱マーカーのみ + 対比が少ない → 多面性の証拠不十分
        if has_generic and contrast_count_local <= 1:
            return 0.5, "二項対立への対応が部分的（汎用語のみ）"

        # 汎用弱 + 複数対比 → 旧挙動互換（多面性あり）
        if has_generic:
            return 0.0, ""

        # 対比表現のみ（弱マーカーなし）→ 部分的対応
        if has_contrast:
            return 0.5, "二項対立への対応が部分的"

        if challenge_count == 0:
            return 1.0, "二項対立を崩していない"
        return 0.5, "二項対立への対応が部分的"

    if trap_type == "safety_boilerplate":
        # 安全語彙の密度を計測し、実質的内容が薄い場合にフラグを立てる
        safety_vocabulary = [
            "倫理", "安全", "責任", "透明", "公平", "プライバシー",
            "リスク", "規制", "ガイドライン", "配慮", "慎重",
            "バランス", "課題", "懸念", "重要",
        ]
        sentences = _split_sentences(response_text)
        if not sentences:
            return 1.0, "空の応答（安全定型文の検出対象）"
        safety_sentence_count = sum(
            1 for s in sentences if any(w in s for w in safety_vocabulary)
        )
        density = safety_sentence_count / len(sentences)

        substantive_markers = [
            "具体的に", "例えば", "実装", "アルゴリズム",
            "数値", "統計", "証拠", "実験", "研究では",
            "メカニズム", "原因", "構造", "技術的に",
            # 安全トピック固有の実質的議論マーカー
            "事例", "事故", "インシデント", "対策", "防止",
            "法律", "法規", "基準", "手順", "プロセス",
            "分析", "評価", "測定", "検証", "監査",
            "なぜなら", "理由は", "背景として", "歴史的に",
        ]
        substantive_count = sum(1 for m in substantive_markers if m in response_text)

        # 実質的マーカーが十分にあれば密度が高くてもboilerplateではない
        if density >= 0.6 and substantive_count <= 2:
            return 1.0, f"安全語彙密度が高い（{density:.2f}）"
        if density >= 0.4 and substantive_count <= 2:
            return 0.5, f"安全語彙密度がやや高い（{density:.2f}）"
        return 0.0, ""

    # その他のtrap_type: challenge_indicatorsが定義されていればその有無で判定
    # challenge_indicatorsが空なら検出対象外（ペナルティなし）
    if not challenge_indicators:
        return 0.0, ""

    generic_challenge = [
        "しかし", "一方で", "ただし", "必ずしも", "とは限らない",
        "問題", "懸念", "批判", "疑問", "限界",
    ]
    generic_count = sum(1 for ind in generic_challenge if ind in response_text)

    if challenge_count == 0 and generic_count == 0:
        return 0.5, f"{trap_type}への対応表現なし"
    return 0.0, ""


def _extract_content_bigrams(text: str) -> set:
    """テキストから内容語バイグラム集合を抽出する

    漢字2文字ペア、カタカナ3文字以上、英単語をキーとして使用。
    否定表現（ではない、ない等）も極性情報として保持する。
    """
    bigrams: set = set()

    # 漢字2文字ペア（連続する漢字からすべての2文字組を生成）
    kanji_runs = re.findall(r'[\u4e00-\u9fff]+', text)
    for run in kanji_runs:
        for i in range(len(run) - 1):
            bigrams.add(run[i:i + 2])

    # 否定表現: 漢字+否定パターンを極性マーカーとして抽出
    negation_patterns = re.findall(r'([\u4e00-\u9fff]{1,4})(ではない|でない|ではなく|しない|できない|ない)', text)
    for kanji_part, neg in negation_patterns:
        bigrams.add(kanji_part + neg)

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
    # ========================================
    # Layer 1: 共通論理語
    # ========================================
    # -- 既存 (verified on HA20) --
    "llm": ["ai"],
    "条件": ["基準", "要件"],
    "話者": ["証言者", "発話者"],
    "正直": ["信頼", "誠実"],
    "仮説": ["理論", "学説"],
    "反証": ["覆す", "否定", "誤り"],
    "妥当": ["有効", "適切"],
    "拒否": ["否定", "退け"],
    "閾値": ["限界", "境界"],
    "検証": ["確認", "実証", "証明", "評価", "検討"],
    "低下": ["下がる", "減少"],
    "集合": ["集約", "総体"],
    "トークン": ["単語", "情報量"],
    "配分": ["分配", "割当"],
    "事例": ["実例"],
    "帰属": ["所在", "帰責"],
    # -- Priority 6 (user specified) --
    "十分": ["保証", "確実"],
    "複合": ["多角", "総合", "多面"],
    "偏在": ["集中", "偏り", "過度に使用"],
    "核心": ["本質", "要点", "論点"],
    "普遍": ["一律", "固定", "一般"],
    "トレードオフ": ["バランス", "両立", "二律", "背反", "長短"],
    # -- New logic terms --
    "構造": ["仕組み", "体系", "枠組"],
    "機能": ["役割", "動作", "作用", "働き"],
    "依存": ["左右", "影響"],
    "関係": ["関連", "結び"],
    "対応": ["対処", "適合"],
    "同一": ["一致", "等価"],
    "対立": ["矛盾", "衝突", "相反"],
    "排除": ["除外", "防止"],
    "保証": ["担保", "確保", "裏付"],
    "測定": ["計測"],
    "拡張": ["拡大", "展開", "適用"],
    "生成": ["作成", "出力", "産出"],
    "論理": ["推論", "ロジック"],
    "決定": ["判断", "選択"],
    "直接": ["明示"],
    "指標": ["尺度", "メトリクス"],
    "連鎖": ["連続", "波及", "多層"],
    "断絶": ["途切", "切断"],
    "存在": ["実在"],
    "現象": ["事象", "意識的"],

    # ========================================
    # Layer 2: UGH専用語
    # ========================================
    "grv": ["語彙", "偏り", "重力"],
    "δe": ["ズレ", "距離"],
    "por": ["共鳴", "共振"],
    "共振": ["共鳴", "対応"],
    "reference": ["参照", "基準", "正解"],

    # ========================================
    # Layer 3: 政策・哲学語
    # ========================================
    "功利": ["帰結", "効用"],
    "紛争": ["戦争", "武力", "軍事"],
    "空洞": ["形骸", "不在", "欠如", "侵害"],
    "類推": ["推論", "類比", "アナロジー", "類似"],
    "経験": ["実践", "実際", "実証"],
    "根拠": ["理由", "証拠", "裏付"],
    "前提": ["仮定", "想定"],
    "主体": ["当事", "行為", "ステークホルダー"],
    "リスク": ["危険", "恐れ", "懸念"],
    "確立": ["構築", "整備", "定着"],
    "差別": ["不公", "不平", "格差", "偏り"],
    "優劣": ["比較", "上下"],
    "未確": ["未定", "不明", "未知"],

    # ========================================
    # Layer 4: Z_23 Round 2 — 実出現ベース追加 (47ペア)
    # キーは _extract_content_bigrams が生成する単位に合わせる:
    #   漢字 → 2文字ペア、カタカナ → 3文字以上、英語 → 2文字以上
    # ========================================
    # -- 専門用語→一般語 --
    "再帰": ["自己参照", "自己監査"],
    "回路": ["ノード"],
    "欺瞞": ["誤った情報"],
    "統計": ["数学的"],          # 統計的 → 統計(bigram)
    "原理": ["理論"],
    "推定": ["推測"],
    "近傍": ["近い位置"],
    "迷子": ["ノイズ"],
    "モダリティ": ["領域"],
    "テキスト": ["自然言語"],
    "制約": ["限界"],
    "語彙": ["関連性"],          # 語彙一致 → 語彙(bigram)
    "規則": ["パターン"],        # 規則性 → 規則(bigram)
    # 現象→意識的: Layer1 "現象" に統合済み
    # -- 行為・状態の言い換え --
    "振舞": ["行動"],            # 振る舞い → 振舞(bigram)
    "分散": ["共有"],
    "道具": ["機能的"],          # 道具的 → 道具(bigram)
    "再生": ["反映"],            # 再生産 → 再生(bigram)
    "増幅": ["拡大"],
    "従属": ["従って"],
    "自覚": ["認識"],
    "可視": ["明らかに"],        # 可視化 → 可視(bigram)
    # 空洞→侵害: Layer3 "空洞" に統合済み
    "集中": ["偏り"],
    "特定": ["分析"],
    # -- 政策・社会語 --
    "地域": ["国"],
    "地政": ["国際的"],          # 地政学 → 地政(bigram)
    "権力": ["押し付"],
    "選好": ["価値観"],
    "自己": ["同意"],            # 自己決定 → 自己(bigram)
    "異議": ["権利"],            # 異議申立 → 異議(bigram)
    "手続": ["説明責任"],
    "法的": ["著作権法"],
    "段階": ["目的に応じた"],    # 段階的 → 段階(bigram)
    # -- 技術語 --
    "学習": ["トレーニング"],
    "参照": ["処理"],
    "勝敗": ["優位"],
    "多次": ["複雑"],            # 多次元 → 多次(bigram)

    # ========================================
    # Layer 5: Round 3 — 全102問ミス棚卸しベース追加
    # 回答テキスト実出現検証済みペアのみ。推測語ゼロ。
    # ========================================
    # -- 既存キーへの値追加 --
    # 再帰 → 自己監査: q034 回答「自己監査」(Layer4で実施)
    # -- 除外 (レビューで不当と判定) --
    # 保証→意味する: 汎用すぎる / リスク→可能性: 概念が異なる
    # 経験→データ: empiricalとdataは同義でない / 損失→アルゴリズム: 包含関係
    # 帰属→責任: 意味的に妥当だがq015でρ回帰 / 曖昧→複雑: ambiguous≠complex
    # -- 新規キー --
    "報酬": ["フィードバック"],     # q006 RLHF: 報酬モデル→フィードバック
    "上限": ["限界", "限定"],       # q046 上限の拡張→限界
    "不明": ["議論", "未解決"],     # q044,q064 不明→議論中
    "本性": ["本質"],               # q053 意識の本性→本質
    "困難": ["複雑", "課題"],       # q073 構造的に困難→複雑
    "曖昧": ["不明確"],             # q015 (複雑は除外: ambiguous≠complex)
    "匹敵": ["同等"],               # q096 匹敵する性能→同等
    "対称": ["相互"],               # q033 対称↔非対称→相互
    "方向": ["一方"],               # q033 方向性→一方向
    "技術": ["設計", "実装"],       # q016 技術的成功→設計
    # 損失→アルゴリズム: 除外（包含関係であり同義語ではない）
    # メモリ→計算資源: 除外（上位概念置換＋回答が逆方向の主張）

    # ========================================
    # Layer 6: Round 4 — 実出現ベース追加 (4ペア)
    # q040[2], q037[1], q039[2] 回収用
    # ========================================
    "裾野": ["エッジケース"],           # q040[2] 裾野→エッジケース
    "境界": ["分割"],                   # q037[1] 境界→分割
    "ルーティング": ["ゲーティングネットワーク"],  # q039[2] ルーティング→ゲーティングネットワーク
    "負荷": ["オーバーヘッド"],         # q039[2] 負荷→オーバーヘッド
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
_RELAXED_BY_SIZE = (
    (8, 0.10, 0.30, 2),
    (5, 0.12, 0.30, 2),
)
_RELAXED_DELTA_E_MAX = 0.04
_RELAXED_GENERIC_CHUNKS = {
    "問題", "可能", "必要", "評価", "分析", "関係", "概念", "使用",
    "言語", "思考", "測定", "倫理", "技術", "人間", "AI",
}
_RELAXED_REQUIRED_CHUNKS = {
    "技術的成功≠倫理的正当性": ("成功", "正当"),
    "連続指標で再測定すると消える場合がある": ("連続", "再測定", "消える"),
    "思考言語仮説では言語は表層にすぎない": ("表層", "仮説"),
}

# 否定極性マーカー: 回答が否定的文脈を含むかの検証に使用
# 助詞/動詞語幹付きの具体的否定形で定義する
_NEGATION_POLARITY_FORMS = [
    # 動詞・助動詞否定形 (助詞/語幹 + ない)
    "ではない", "ではなく", "でない", "しない", "できない",
    "ならない", "はない", "もない", "がない", "のない",
    # 丁寧否定
    "ません",
    # 連体否定
    "無い",
    # 古典否定 (「〜せず」「〜できず」)
    "せず", "きず", "らず", "ずに",
    # 不X (否定状態)
    "不十分", "不可能", "不明", "不確", "不適", "不足", "不要",
    # 未X (否定状態)
    "未解", "未確", "未検", "未整", "未発", "未到",
    # 非X (否定属性)
    "非対", "非線", "非効", "非合",
]

# 推量表現: 否定形を含むが否定ではない表現
# 極性検証の前にテキストから除外して偽マッチを防ぐ
_SPECULATIVE_EXCLUSIONS = ["かもしれない", "かもしれません"]


def _response_has_negation(
    response_text: str,
    concept_bigrams: Optional[set] = None,
) -> bool:
    """回答テキストに否定形が含まれるか判定する

    推量表現 (かもしれない/かもしれません) を事前除外してから検査する。
    concept_bigrams が指定された場合、概念バイグラムを含む文のみを検査し、
    無関係な副文での偽マッチを防止する。
    """
    if concept_bigrams:
        # 概念近傍スコーピング: 概念を含む節のみで否定形を検査
        # 逆接接続詞 (が、/しかし、/ただし、/けれど、/一方、) で節分割し、
        # 「概念は〜が、別件は不明」型の偽マッチを防止する
        for sent in _split_sentences(response_text):
            clauses = re.split(r'(?:が、|しかし、|ただし、|けれど、|一方、)', sent)
            for clause in clauses:
                clause = clause.strip()
                if not clause:
                    continue
                if not any(bg in clause for bg in concept_bigrams):
                    continue
                cleaned = clause
                for excl in _SPECULATIVE_EXCLUSIONS:
                    cleaned = cleaned.replace(excl, "")
                if any(form in cleaned for form in _NEGATION_POLARITY_FORMS):
                    return True
        return False
    # フォールバック: 全文検査
    cleaned = response_text
    for excl in _SPECULATIVE_EXCLUSIONS:
        cleaned = cleaned.replace(excl, "")
    return any(form in cleaned for form in _NEGATION_POLARITY_FORMS)


def _relaxed_thresholds(
    prop_bigram_count: int,
    operator_family: Optional[str],
) -> Tuple[float, float, int]:
    if operator_family is not None:
        return 0.15, 0.35, _MIN_OVERLAP
    for size_floor, direct_t, full_t, overlap_t in _RELAXED_BY_SIZE:
        if prop_bigram_count >= size_floor:
            return direct_t, full_t, overlap_t
    return 0.15, 0.35, _MIN_OVERLAP


def _best_overlap_sentence(response_text: str, overlap_set: set) -> str:
    best = ""
    best_score = -1
    for sent in _split_sentences(response_text):
        score = sum(1 for token in overlap_set if token in sent)
        if score > best_score:
            best_score = score
            best = sent
    return best.strip()


def _relaxed_candidate_allowed(
    *,
    prop: str,
    operator_family: Optional[str],
    overlap_set: set,
    direct_overlap_set: set,
    response_text: str,
) -> bool:
    sentence = _best_overlap_sentence(response_text, overlap_set)
    if not sentence:
        return False

    chunks = _extract_content_chunks(prop)
    matched_chunks = [chunk for chunk in chunks if chunk in sentence]
    if not matched_chunks:
        return False

    if all(chunk in _RELAXED_GENERIC_CHUNKS for chunk in matched_chunks):
        return False

    if operator_family is None:
        direct_signal = [
            token for token in direct_overlap_set
            if token not in _RELAXED_GENERIC_CHUNKS
        ]
        if not direct_signal:
            return False

    required_chunks = _RELAXED_REQUIRED_CHUNKS.get(prop)
    if required_chunks and not all(chunk in sentence for chunk in required_chunks):
        return False

    return True


def check_propositions(
    response_text: str,
    core_props: List[str],
    disqualifying: Optional[List[str]] = None,
    acceptable_variants: Optional[List[str]] = None,
    relaxed_context: Optional[dict] = None,
) -> Tuple[int, List[int], List[int]]:
    if not core_props:
        return 0, [], []

    negation_cues = [
        "ではなく", "ではない", "のではなく", "のではない",
        "じゃない", "誤り", "不適切", "批判", "安易", "短絡",
        "否定", "不要", "不可能",
    ]
    if disqualifying:
        for shortcut in disqualifying:
            if not shortcut or shortcut not in response_text:
                continue
            is_negated = False
            for sent in _split_sentences(response_text):
                if shortcut in sent:
                    context = sent.replace(shortcut, "")
                    if any(cue in context for cue in negation_cues):
                        is_negated = True
                        break
            if not is_negated:
                miss_ids = list(range(len(core_props)))
                return 0, [], miss_ids

    resp_bigrams = _extract_content_bigrams(response_text)
    all_variant_bigrams: List[set] = []
    if acceptable_variants:
        for variant in acceptable_variants:
            if variant and variant in response_text:
                all_variant_bigrams.append(_extract_content_bigrams(variant))

    hit_ids: List[int] = []
    miss_ids: List[int] = []
    relaxed_candidates: List[int] = []
    neg_deontic = (
        "\u3079\u304d\u3067\u306f\u306a\u3044",
        "\u3059\u3079\u304d\u3067\u306f\u306a\u3044",
        "\u3079\u304d\u3067\u306a\u3044",
        "\u3059\u3079\u304d\u3067\u306a\u3044",
        "\u3079\u304d\u3058\u3083\u306a\u3044",
        "\u3059\u3079\u304d\u3058\u3083\u306a\u3044",
    )
    pos_deontic = ("\u3059\u3079\u304d", "\u3079\u304d")

    for i, prop in enumerate(core_props):
        prop_bigrams = _extract_content_bigrams(prop)
        if not prop_bigrams:
            miss_ids.append(i)
            continue

        expanded = _expand_with_synonyms(prop_bigrams)
        for vbg in all_variant_bigrams:
            common = vbg & prop_bigrams
            if len(common) >= max(2, len(prop_bigrams) * 0.3):
                expanded |= vbg

        overlap_set = expanded & resp_bigrams
        overlap_count = len(overlap_set)
        direct_overlap_set = prop_bigrams & resp_bigrams
        direct_recall = len(direct_overlap_set) / len(prop_bigrams)
        full_recall = overlap_count / len(prop_bigrams)
        op = detect_operator(prop)
        has_neg_deontic = any(token in prop for token in neg_deontic)
        needs_polarity_deontic = has_neg_deontic
        needs_polarity_full = (
            (op is not None and OPERATOR_CATALOG[op.family]["effect"] == "polarity_flip")
            or has_neg_deontic
        )
        is_positive_deontic = bool(
            any(token in prop for token in pos_deontic)
            and not has_neg_deontic
        )

        min_required = min(_MIN_OVERLAP, len(prop_bigrams))
        if direct_recall >= 0.15 and full_recall >= 0.30 and overlap_count >= min_required:
            if needs_polarity_deontic and not _response_has_negation(response_text, overlap_set):
                miss_ids.append(i)
                continue
            if is_positive_deontic and _response_has_negation(response_text, overlap_set):
                miss_ids.append(i)
                continue
            hit_ids.append(i)
            continue

        recovered = False
        if op is not None:
            markers = OPERATOR_CATALOG[op.family]["response_markers"]
            marker_found = False
            for sent in _split_sentences(response_text):
                if any(bg in sent for bg in overlap_set) and any(m in sent for m in markers):
                    marker_found = True
                    break
            if marker_found and direct_recall >= 0.10 and full_recall >= 0.25 and overlap_count >= 2:
                if needs_polarity_full and not _response_has_negation(response_text, overlap_set):
                    miss_ids.append(i)
                    continue
                if is_positive_deontic and _response_has_negation(response_text, overlap_set):
                    miss_ids.append(i)
                    continue
                hit_ids.append(i)
                recovered = True

        if recovered:
            continue

        if relaxed_context:
            direct_t, full_t, overlap_t = _relaxed_thresholds(
                len(prop_bigrams),
                op.family if op is not None else None,
            )
            relaxed_min_required = min(overlap_t, len(prop_bigrams))
            if (
                direct_recall >= direct_t
                and full_recall >= full_t
                and overlap_count >= relaxed_min_required
                and _relaxed_candidate_allowed(
                    prop=prop,
                    operator_family=op.family if op is not None else None,
                    overlap_set=overlap_set,
                    direct_overlap_set=direct_overlap_set,
                    response_text=response_text,
                )
            ):
                # 極性検証: 通常パスと同じガードを適用
                if needs_polarity_full and not _response_has_negation(
                    response_text, overlap_set
                ):
                    pass  # polarity不一致 → relaxed候補に含めない
                elif is_positive_deontic and _response_has_negation(
                    response_text, overlap_set
                ):
                    pass  # 肯定deonticが否定されている → 含めない
                else:
                    relaxed_candidates.append(i)

        miss_ids.append(i)

    if relaxed_context and relaxed_candidates:
        fail_max = relaxed_context.get("fail_max", 1.0)
        if fail_max < 1.0:
            # 現状のΔEを計算し、既に高品質なケースのみ relaxed 昇格を許可
            current_evidence = Evidence(
                question_id=relaxed_context.get("question_id", ""),
                f1_anchor=relaxed_context.get("f1_anchor", 0.0),
                f2_unknown=relaxed_context.get("f2_unknown", 0.0),
                f3_operator=relaxed_context.get("f3_operator", 0.0),
                f4_premise=relaxed_context.get("f4_premise", 0.0),
                propositions_hit=len(hit_ids),
                propositions_total=len(core_props),
            )
            current_s = _compute_s(current_evidence)
            current_c = len(hit_ids) / len(core_props) if core_props else 1.0
            current_delta_e = _compute_delta_e(current_s, current_c)

            relaxed_hits = len(hit_ids) + len(relaxed_candidates)
            relaxed_c = relaxed_hits / len(core_props) if core_props else 1.0
            relaxed_delta_e = _compute_delta_e(current_s, relaxed_c)

            if current_delta_e <= _RELAXED_DELTA_E_MAX and relaxed_delta_e <= _RELAXED_DELTA_E_MAX:
                hit_ids = sorted(set(hit_ids) | set(relaxed_candidates))
                miss_ids = [idx for idx in miss_ids if idx not in relaxed_candidates]

    return len(hit_ids), hit_ids, miss_ids


def compute_quality_score(
    propositions_hit_rate: float,
    fail_max: Optional[float],
    delta_e_full: float,
    alpha: float = QUALITY_ALPHA,
    beta: float = QUALITY_BETA,
    gamma: float = QUALITY_GAMMA,
) -> dict:
    """Model C'（ボトルネック型）による品質スコアを算出する。

    propositions_hit_rate, fail_max, delta_e_full から統合品質スコアを計算。
    既存の propositions_hit_rate は変更しない（追加指標として提供）。

    暫定パラメータ: n=20 LOO-CV 検証済み (ρ=0.8018)。n=48 で再校正予定。
    """
    L_P = 1.0 - propositions_hit_rate
    L_struct = fail_max if fail_max is not None else 0.0
    L_R = delta_e_full
    L_linear = alpha * L_P + beta * L_struct + gamma * L_R
    L_op = max(L_struct, L_linear)
    score = max(1.0, min(5.0, 5.0 - 4.0 * L_op))
    return {
        "quality_score": round(score, 4),
        "quality_model": QUALITY_MODEL_NAME,
        "quality_params": {"alpha": alpha, "beta": beta, "gamma": gamma},
        "quality_loss_breakdown": {
            "L_P": round(L_P, 4),
            "L_struct": round(L_struct, 4),
            "L_R": round(L_R, 4),
            "L_op": round(L_op, 4),
        },
    }


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
    f2, f2_detail = check_f2_unknown(response_text, reserved_terms, question_text)

    # f3: 演算子無処理
    f3, f3_family = check_f3_operator(question_text, response_text, operators)

    # f4: 前提受容
    f4, f4_detail = check_f4_premise(question_text, response_text, trap_type, frames)
    fail_max = max(f1, f2, f3, f4)

    # 命題検出
    hits, hit_ids, miss_ids = check_propositions(
        response_text,
        core_props,
        disqualifying,
        acceptable_variants,
        relaxed_context={
            "question_id": question_id,
            "f1_anchor": f1,
            "f2_unknown": f2,
            "f3_operator": f3,
            "f4_premise": f4,
            "fail_max": fail_max,
        },
    )

    # hit_source 構築: tfidf hit 分を記録
    hit_sources: Dict[int, str] = {}
    for idx in hit_ids:
        hit_sources[idx] = "tfidf"
    for idx in miss_ids:
        hit_sources[idx] = "miss"

    # --- cascade 回収 ---
    # Tier 1 miss のうち、SBert + 多条件フィルタで rescue 可能なものを回収する。
    # cascade_matcher が利用不可の場合は Tier 1 結果のみで動作する。
    # disqualifying_shortcuts 発火時は全 miss が意図的なため cascade をスキップする。
    # check_propositions() と同じ否定文脈チェックを再現し、
    # 否定・批判文脈で引用されているだけの場合は disqualified にしない。
    disqualified = False
    if (hits == 0 and len(miss_ids) == len(core_props)
            and core_props and disqualifying):
        _DQ_NEGATION_CUES = [
            "ではなく", "ではない", "のではなく", "のではない",
            "じゃない", "誤り", "不適切", "批判", "安易", "短絡",
            "否定", "不要", "不可能",
        ]
        for shortcut in disqualifying:
            if not shortcut or shortcut not in response_text:
                continue
            sentences = _split_sentences(response_text)
            is_negated = False
            for sent in sentences:
                if shortcut in sent:
                    context = sent.replace(shortcut, "")
                    if any(cue in context for cue in _DQ_NEGATION_CUES):
                        is_negated = True
                        break
            if not is_negated:
                disqualified = True
                break
    atomic_units_map = question_meta.get("atomic_units_map", {})
    has_any_atomic = any(
        atomic_units_map.get(idx, atomic_units_map.get(str(idx), []))
        for idx in miss_ids
    ) if miss_ids else False
    cascade_model = (
        _get_cascade_model()
        if _HAS_CASCADE and miss_ids and has_any_atomic and not disqualified
        else None
    )
    if cascade_model is not None:
        rescued_ids: List[int] = []
        remaining_miss: List[int] = []
        for idx in miss_ids:
            prop_text = core_props[idx]
            atomic_units = atomic_units_map.get(idx, atomic_units_map.get(str(idx), []))
            if not atomic_units:
                # atomic_units なし → c5 が必ず fail するため Tier 2 をスキップ
                remaining_miss.append(idx)
                continue
            t2 = tier2_candidate(prop_text, response_text, cascade_model)
            t3 = tier3_filter(
                tier2_result=t2,
                tier1_hit=False,
                f4_flag=f4,
                atomic_units=atomic_units,
                synonym_dict=_SYNONYM_MAP,
                response=response_text,
            )
            if t3["verdict"] == "Z_RESCUED":
                rescued_ids.append(idx)
                hit_sources[idx] = "cascade_rescued"
            else:
                remaining_miss.append(idx)
        if rescued_ids:
            hit_ids = sorted(hit_ids + rescued_ids)
            miss_ids = remaining_miss
            hits = len(hit_ids)

    return Evidence(
        question_id=question_id,
        f1_anchor=f1,
        f2_unknown=f2,
        f3_operator=f3,
        f4_premise=f4,
        f2_detail=f2_detail,
        f4_detail=f4_detail,
        f3_operator_family=f3_family,
        f4_trap_type=trap_type if f4 > 0 else "",
        propositions_hit=hits,
        propositions_total=len(core_props),
        hit_ids=hit_ids,
        miss_ids=miss_ids,
        hit_sources=hit_sources,
    )
