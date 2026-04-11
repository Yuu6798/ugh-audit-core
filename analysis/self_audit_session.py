"""analysis/self_audit_session.py — セッション出力の Self-Audit Principle 準拠度測定

CLAUDE.md の Self-Audit Principle が実際に Claude の出力に効いているかを
定量的に検証するための proxy audit スクリプト。

## 位置づけ

正式な L_sem 計算 (audit.py) は「質問に対する命題カバレッジ」を測る設計で、
本スクリプトが測りたい「出力の filler / 評価語密度」とは target が異なる。
そのため本スクリプトは `semantic_loss.py` の指標体系 (L_Q / L_F / L_A) と
スピリット的に対応する proxy metric を独自に計算する。

### 計算する proxy metrics

- **L_Q_proxy** (評価語密度): 肯定的な評価語 / 感想語の出現頻度。
  L_Q (演算子制約の未処理) とは正確には一致しないが、Self-Audit Principle が
  「評価語で命題極性を偽装するな」と禁じている対象を直接カウントする
- **L_F_proxy** (filler 密度): 情報量ゼロの定型句・banner ヘッダ・自動挙動再宣言の出現頻度。
  L_F (用語捏造) とは対象が違うが、原則の「情報量ゼロの段落を挟むな」に対応
- **redundancy_proxy**: 直前の assistant turn との n-gram 重複率。
  既存命題の言い換えだけの段落を検出
- **decoration_ratio**: banner / heading / emoji の密度

### 使い方

```
python analysis/self_audit_session.py \\
    --transcript path/to/session.json \\
    --principle-turn 42 \\
    --output out.csv
```

入力フォーマット:
```json
[
  {"turn": 1, "role": "user", "content": "..."},
  {"turn": 2, "role": "assistant", "content": "..."},
  ...
]
```

### 何を証明しないか

- これは formal L_sem ではない (proxy)
- 「意味的誠実性」そのものを測るのではなく、Self-Audit Principle の
  compliance だけを測る
- Goodhart 注意: スコアを下げるだけなら、本来必要な qualifier まで
  機械的に削れば達成できてしまう。質的 review も併用すること
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ============================================================
# 検出パターン (CLAUDE.md Self-Audit Principle から直接導出)
# ============================================================

# 評価語: 原則「書かないもの」の「事実の感情的な再記述」「評価語だけで
# 情報量ゼロの段落」に対応。Claude 出力で観測された実例をベースにする。
_EVALUATIVE_WORDS: Tuple[str, ...] = (
    "素晴らしい",
    "的確な",
    "的確に",
    "非常に勉強になり",
    "勉強になり",
    "価値のある",
    "価値ある",
    "興味深い",
    "面白い",  # context-dependent だが自己監査目的では検出寄りに倒す
    "見事",
    "さすが",
    "有意義",
    "意義深い",
    "優れた",
    "秀逸",
    "完璧",
    "絶妙",
    "素敵",
    "感心",
    "称賛",
)

# Banner / 装飾ヘッダ: 原則「書かないもの」の「体裁だけの banner セクション」に対応。
# '所感' '観察' '累計' '総括' '振り返り' '雑感' など。
_BANNER_HEADERS: Tuple[str, ...] = (
    "所感",
    "観察",
    "累計",
    "総括",
    "振り返り",
    "雑感",
    "補足",  # context-dependent
    "まとめ",  # context-dependent
)

# Filler / 自動挙動再宣言: 原則「書かないもの」の「自動挙動の再宣言」に対応。
_AUTOMATION_RESTATEMENTS: Tuple[str, ...] = (
    "次の CI 結果を待ちます",
    "CI の結果を待ちます",
    "引き続き監視します",
    "引き続き watch します",
    "引き続き購読中です",
    "レビュー待ちします",
    "次のレビューを待ちます",
)

# Meta phrases that often decorate without adding content
_META_FILLER: Tuple[str, ...] = (
    "改めて",
    "一連の",
    "総じて",
    "結論から言うと",  # context-dependent
    "念のため",  # context-dependent
    "余談ですが",
    "蛇足ですが",
)

# 累計 / 達成感報告: "7 件すべて対応" のようなパターン
_ACHIEVEMENT_REPORT_RE = re.compile(
    r"(累計|通算|合計|これで|すべて)\s*\d+\s*件",
)

# Checkmark 羅列 (✅✅✅... 累計報告の兆候)
# 連続した 3 マーク以上だけを検出する。マーク間には whitespace (半角 SP /
# tab / 全角 SP) のみ許容し、改行やテキストが挟まるケースは別パターンとして
# 扱う (Codex review PR #61 r3067358384: 旧 `(?:✅.*){3,}` + DOTALL は、散発
# した ✅ を含む普通の multi-item report を誤検出していた)。
_CHECKMARK_BULLET_RE = re.compile(r"✅(?:[ \t\u3000]*✅){2,}")

# Banner section (## 見出し形式)
_HEADING_RE = re.compile(r"^#{1,4}\s+\S", re.MULTILINE)


# ============================================================
# Metric computation
# ============================================================


@dataclass
class TurnMetrics:
    """1 turn 分の proxy metrics"""

    turn: int
    role: str
    char_count: int
    sentence_count: int
    L_Q_proxy: float = 0.0   # 評価語密度 (per sentence)
    L_F_proxy: float = 0.0   # filler 密度 (per sentence)
    decoration_ratio: float = 0.0   # banner/heading 密度 (per 100 chars)
    redundancy_proxy: float = 0.0   # 直前 assistant turn との bigram 重複
    evaluative_hits: List[str] = field(default_factory=list)
    filler_hits: List[str] = field(default_factory=list)
    banner_hits: List[str] = field(default_factory=list)


def _count_sentences(text: str) -> int:
    """句点 + 改行で粗く文カウント"""
    if not text.strip():
        return 0
    sentences = re.split(r"[。．\n]+", text)
    return max(1, sum(1 for s in sentences if s.strip()))


def _count_pattern_hits(text: str, patterns: Tuple[str, ...]) -> Tuple[int, List[str]]:
    """複数 substring パターンの **非重複** 出現回数と hit list を返す。

    同じ位置を複数の pattern が cover する場合 (例: 「非常に勉強になり」と
    「勉強になり」が「非常に勉強になりました」に両方 match する場合)、
    **長い pattern を優先して 1 つの expression を 1 回だけカウントする**。
    単純な text.count() の和では double-count が発生し、before/after 比較を
    歪める (Codex review PR #61 r3067340176)。

    実装: 長い順に sort した pattern を alternation regex にまとめ、
    re.finditer で左端優先 + 非重複マッチを列挙する。
    """
    if not patterns or not text:
        return 0, []
    # 長い pattern を優先 (alternation は left-to-right を見るので、
    # 長い方を先に置くことで「非常に勉強になり」が「勉強になり」より先に試される)
    sorted_patterns = sorted(patterns, key=len, reverse=True)
    regex = re.compile("|".join(re.escape(p) for p in sorted_patterns))

    per_pattern: Dict[str, int] = {}
    for match in regex.finditer(text):
        key = match.group(0)
        per_pattern[key] = per_pattern.get(key, 0) + 1

    total = sum(per_pattern.values())
    hits = [f"{p}×{c}" for p, c in per_pattern.items()]
    return total, hits


def _char_bigrams(text: str) -> set:
    """文字 bigram (日本語向け)"""
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _bigram_overlap(a: str, b: str) -> float:
    """Jaccard 係数"""
    bg_a = _char_bigrams(a)
    bg_b = _char_bigrams(b)
    if not bg_a or not bg_b:
        return 0.0
    union = bg_a | bg_b
    return len(bg_a & bg_b) / len(union) if union else 0.0


def compute_turn_metrics(
    turn: int,
    role: str,
    content: str,
    previous_assistant: Optional[str] = None,
) -> TurnMetrics:
    """1 turn 分の proxy metrics を計算する"""
    char_count = len(content)
    sentence_count = _count_sentences(content)

    if sentence_count == 0:
        return TurnMetrics(turn=turn, role=role, char_count=0, sentence_count=0)

    # L_Q proxy: 評価語密度 / sentence
    eval_count, eval_hits = _count_pattern_hits(content, _EVALUATIVE_WORDS)
    L_Q_proxy = eval_count / sentence_count

    # L_F proxy: filler + automation restatement + achievement report
    filler_count, filler_hits = _count_pattern_hits(content, _META_FILLER)
    automation_count, automation_hits = _count_pattern_hits(
        content, _AUTOMATION_RESTATEMENTS
    )
    achievement_hits = len(_ACHIEVEMENT_REPORT_RE.findall(content))
    checkmark_hits = len(_CHECKMARK_BULLET_RE.findall(content))
    total_filler = filler_count + automation_count + achievement_hits + checkmark_hits
    L_F_proxy = total_filler / sentence_count

    all_filler_hits = list(filler_hits) + list(automation_hits)
    if achievement_hits:
        all_filler_hits.append(f"achievement_report×{achievement_hits}")
    if checkmark_hits:
        all_filler_hits.append(f"checkmark_streak×{checkmark_hits}")

    # decoration_ratio: banner / heading 密度 (per 100 chars)
    banner_count, banner_hits = _count_pattern_hits(content, _BANNER_HEADERS)
    heading_count = len(_HEADING_RE.findall(content))
    decoration_total = banner_count + heading_count
    decoration_ratio = (decoration_total / max(1, char_count)) * 100.0

    # redundancy_proxy: 直前 assistant turn との Jaccard 重複
    redundancy = 0.0
    if previous_assistant:
        redundancy = _bigram_overlap(content, previous_assistant)

    return TurnMetrics(
        turn=turn,
        role=role,
        char_count=char_count,
        sentence_count=sentence_count,
        L_Q_proxy=round(L_Q_proxy, 4),
        L_F_proxy=round(L_F_proxy, 4),
        decoration_ratio=round(decoration_ratio, 4),
        redundancy_proxy=round(redundancy, 4),
        evaluative_hits=eval_hits,
        filler_hits=all_filler_hits,
        banner_hits=banner_hits,
    )


def compute_session_metrics(
    transcript: List[Dict],
) -> List[TurnMetrics]:
    """全 turn の metrics を計算。assistant turn のみ metric 対象"""
    results: List[TurnMetrics] = []
    last_assistant: Optional[str] = None
    for entry in transcript:
        role = entry.get("role", "")
        turn = entry.get("turn", 0)
        content = entry.get("content", "")
        if role == "assistant":
            m = compute_turn_metrics(turn, role, content, previous_assistant=last_assistant)
            results.append(m)
            last_assistant = content
        # user turn は metric 計算対象外（ただし conversation flow 管理のため無視しない）
    return results


# ============================================================
# Before / After comparison
# ============================================================


@dataclass
class PhaseSummary:
    phase: str
    n_turns: int
    mean_L_Q_proxy: float
    mean_L_F_proxy: float
    mean_decoration_ratio: float
    mean_redundancy: float
    median_L_Q_proxy: float
    median_L_F_proxy: float


def _phase_stats(name: str, metrics: List[TurnMetrics]) -> PhaseSummary:
    if not metrics:
        return PhaseSummary(name, 0, 0, 0, 0, 0, 0, 0)
    return PhaseSummary(
        phase=name,
        n_turns=len(metrics),
        mean_L_Q_proxy=round(statistics.mean(m.L_Q_proxy for m in metrics), 4),
        mean_L_F_proxy=round(statistics.mean(m.L_F_proxy for m in metrics), 4),
        mean_decoration_ratio=round(
            statistics.mean(m.decoration_ratio for m in metrics), 4
        ),
        mean_redundancy=round(statistics.mean(m.redundancy_proxy for m in metrics), 4),
        median_L_Q_proxy=round(statistics.median(m.L_Q_proxy for m in metrics), 4),
        median_L_F_proxy=round(statistics.median(m.L_F_proxy for m in metrics), 4),
    )


def split_by_principle_turn(
    metrics: List[TurnMetrics],
    principle_turn: int,
) -> Tuple[PhaseSummary, PhaseSummary]:
    """principle_turn を境に before / after の統計を計算"""
    before = [m for m in metrics if m.turn < principle_turn]
    after = [m for m in metrics if m.turn >= principle_turn]
    return _phase_stats("before", before), _phase_stats("after", after)


# ============================================================
# IO
# ============================================================


def load_transcript(path: Path) -> List[Dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"transcript must be a list, got {type(data)}")
    return data


def write_csv(metrics: List[TurnMetrics], path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "turn", "role", "char_count", "sentence_count",
            "L_Q_proxy", "L_F_proxy", "decoration_ratio", "redundancy_proxy",
            "evaluative_hits", "filler_hits", "banner_hits",
        ])
        for m in metrics:
            writer.writerow([
                m.turn, m.role, m.char_count, m.sentence_count,
                m.L_Q_proxy, m.L_F_proxy, m.decoration_ratio, m.redundancy_proxy,
                "; ".join(m.evaluative_hits),
                "; ".join(m.filler_hits),
                "; ".join(m.banner_hits),
            ])


def print_summary(
    metrics: List[TurnMetrics],
    principle_turn: Optional[int],
) -> None:
    print(f"\n=== Session audit summary ({len(metrics)} assistant turns) ===")
    print(f"total chars:     {sum(m.char_count for m in metrics):,}")
    print(f"total sentences: {sum(m.sentence_count for m in metrics):,}")

    if principle_turn is None:
        overall = _phase_stats("overall", metrics)
        print("\noverall means:")
        print(f"  L_Q_proxy:        {overall.mean_L_Q_proxy}")
        print(f"  L_F_proxy:        {overall.mean_L_F_proxy}")
        print(f"  decoration_ratio: {overall.mean_decoration_ratio}")
        print(f"  redundancy_proxy: {overall.mean_redundancy}")
        return

    before, after = split_by_principle_turn(metrics, principle_turn)
    print(f"\nsplit at turn {principle_turn}:")
    print(f"  before: {before.n_turns} turns")
    print(f"  after:  {after.n_turns} turns")

    def _delta(b: float, a: float) -> str:
        if b == 0:
            return f"{a:+.4f}"
        delta = a - b
        pct = (delta / b) * 100.0
        arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "→")
        return f"{delta:+.4f} ({pct:+.1f}% {arrow})"

    print("\nmetric           before    after     change")
    print(f"{'-' * 60}")
    for label, b_val, a_val in [
        ("L_Q_proxy (mean) ", before.mean_L_Q_proxy, after.mean_L_Q_proxy),
        ("L_F_proxy (mean) ", before.mean_L_F_proxy, after.mean_L_F_proxy),
        ("decoration_ratio ", before.mean_decoration_ratio, after.mean_decoration_ratio),
        ("redundancy_proxy ", before.mean_redundancy, after.mean_redundancy),
        ("L_Q_proxy (median)", before.median_L_Q_proxy, after.median_L_Q_proxy),
        ("L_F_proxy (median)", before.median_L_F_proxy, after.median_L_F_proxy),
    ]:
        print(f"{label} {b_val:8.4f}  {a_val:8.4f}  {_delta(b_val, a_val)}")

    print("\nInterpretation:")
    if after.mean_L_Q_proxy < before.mean_L_Q_proxy:
        print("  L_Q_proxy ↓: 評価語が減った → 原則 compliance の可能性あり")
    if after.mean_L_F_proxy < before.mean_L_F_proxy:
        print("  L_F_proxy ↓: filler が減った → 原則 compliance の可能性あり")
    if after.mean_decoration_ratio < before.mean_decoration_ratio:
        print("  decoration ↓: banner 装飾が減った")

    print("\n注意: proxy metric のため, 正式な L_sem 改善の証明ではない。")
    print("質的 review も併用すること。また confirmation bias に注意。")


# ============================================================
# Main
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Claude セッション出力の Self-Audit Principle 準拠度測定",
    )
    parser.add_argument(
        "--transcript",
        required=True,
        type=Path,
        help="セッション transcript JSON (turn list)",
    )
    parser.add_argument(
        "--principle-turn",
        type=int,
        default=None,
        help="Self-Audit Principle が導入された turn 番号 (境界)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="per-turn metrics CSV 出力先",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="各 turn の hit 詳細を表示",
    )
    args = parser.parse_args()

    if not args.transcript.exists():
        print(f"Error: transcript not found: {args.transcript}", file=sys.stderr)
        return 1

    transcript = load_transcript(args.transcript)
    metrics = compute_session_metrics(transcript)

    if not metrics:
        print("Warning: no assistant turns found in transcript", file=sys.stderr)
        return 1

    print_summary(metrics, args.principle_turn)

    if args.verbose:
        print("\n=== per-turn detail ===")
        for m in metrics:
            marker = ""
            if args.principle_turn is not None:
                marker = " [after]" if m.turn >= args.principle_turn else " [before]"
            print(
                f"  turn {m.turn}{marker}: "
                f"L_Q={m.L_Q_proxy:.3f} L_F={m.L_F_proxy:.3f} "
                f"dec={m.decoration_ratio:.3f} red={m.redundancy_proxy:.3f} "
                f"chars={m.char_count}"
            )
            if m.evaluative_hits:
                print(f"    evaluative: {', '.join(m.evaluative_hits)}")
            if m.filler_hits:
                print(f"    filler:     {', '.join(m.filler_hits)}")
            if m.banner_hits:
                print(f"    banner:     {', '.join(m.banner_hits)}")

    if args.output:
        write_csv(metrics, args.output)
        print(f"\nper-turn metrics → {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
