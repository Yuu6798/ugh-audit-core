"""analysis/annotation_ui.py — HA48 accept40 拡充用の対話 CLI

docs/annotation_protocol.md §3 の user 負担軽減策を実装した対話ツール:

- decision tree (Q1/Q2) → O 自動 mapping
- O への anchoring を避けるため ΔE / S / C / f1-f4 / verdict / モデル名を非表示
- comment は template 選択式
- keyboard shortcut 中心、90 秒超で reminder
- batch 区切りで強制休憩 + run_incremental_calibration.py 呼び出し
- progress 保存 + --resume 対応
- ブラインド混入（HA48 から N 件）
- --stability-check サブコマンド（任意）

使い方:
    python analysis/annotation_ui.py                   # 新規セッション
    python analysis/annotation_ui.py --resume          # 中断再開
    python analysis/annotation_ui.py --stability-check # 任意: 過去の再評価
    python analysis/annotation_ui.py --dry-run --batch-size 3 < stdin.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, TextIO

ROOT = Path(__file__).resolve().parent.parent
ACC40_STUB = (
    ROOT / "data" / "human_annotation_accept40" / "annotation_accept40_stub.csv"
)
ACC40_OUT = ROOT / "data" / "human_annotation_accept40" / "annotation_accept40.csv"
HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
PROGRESS_PATH = Path.home() / ".ugh_audit" / "annotation_progress.json"
INCREMENTAL_CAL = ROOT / "analysis" / "run_incremental_calibration.py"

# Decision tree → O mapping (int 1-5 Likert)
DECISION_TREE: Dict[tuple, int] = {
    ("A", "N"): 5,  # 核心全部 + 誤情報なし → 完全
    ("A", "Y"): 4,  # 核心全部 + 誤情報あり → 概ね良好
    ("B", "N"): 3,  # 核心部分的 + 誤情報なし → 境界
    ("B", "Y"): 2,  # 核心部分的 + 誤情報あり → 不十分
    ("C", "N"): 1,  # 核心なし → 失敗
    ("C", "Y"): 1,
}

COMMENT_TEMPLATES = {
    "1": "命題カバー不足",
    "2": "方向違い / 主題逸脱",
    "3": "誤情報含む",
    "4": "冗長だが核心あり",
    "5": "完璧",
    "6": "カスタム",
}

BATCH_SIZES = [15, 10, 10]  # batch_1, batch_2, batch_3
BLIND_COUNTS = [3, 2, 2]    # それぞれの batch に混入する HA48 件数
SLOW_THINK_SECONDS = 90


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_stub(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_existing_output(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_output(rows: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id", "source", "question_id", "question", "response",
        "core_propositions", "O", "rater", "annotated_at", "comment",
        "blind_check", "hits_total",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def _load_ha48_for_blind() -> List[dict]:
    """HA48 から blind 混入候補を抽出 (O が 1-5 で埋まっている行)."""
    rows: List[dict] = []
    if not HA48_PATH.exists():
        return rows
    with open(HA48_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("O"):
                continue
            rows.append(row)
    return rows


def _save_progress(state: dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _load_progress() -> Optional[dict]:
    if not PROGRESS_PATH.exists():
        return None
    with open(PROGRESS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _format_item(item: dict) -> str:
    """annotate 対象を user に見せる整形済み文字列.

    ΔE / S / C / verdict / モデル名 / f1-f4 は一切表示しない。
    hits_total は命題カウンタとしてのみ表示 (O への anchoring を避ける軽度情報)。
    """
    try:
        core_props = json.loads(item.get("core_propositions") or "[]")
    except (json.JSONDecodeError, TypeError):
        core_props = []
    hits_total = item.get("hits_total", "")
    parts = [
        "─" * 60,
        f"[ID: {item.get('id', '?')}]",
        "",
        "【質問】",
        item.get("question", "(なし)"),
        "",
        "【核心命題 (core_propositions)】",
    ]
    if core_props:
        for i, p in enumerate(core_props):
            parts.append(f"  {i}: {p}")
    else:
        parts.append("  (なし)")
    if hits_total:
        parts.append(f"  [命題カウンタ参考: hits_total = {hits_total}]")
    parts.extend([
        "",
        "【回答】",
        item.get("response", "(なし)"),
        "─" * 60,
    ])
    return "\n".join(parts)


def _show_rubric() -> str:
    return (
        "【Decision Tree】\n"
        "  Q1: 核心命題は回答に含まれるか?\n"
        "    [a] 全て含まれる\n"
        "    [b] 部分的に含まれる\n"
        "    [c] 含まれない / 方向違い\n"
        "  Q2: 誤情報・方向違いで主題を狂わせているか?\n"
        "    [y] あり\n"
        "    [n] なし / 軽微\n"
        "\n"
        "【直接入力 (override)】\n"
        "  [o] 上記以外で O を直接指定したい → 続けて 1-5 を押す\n"
        "  [1-5] O を直接入力 (Likert 1-5 スケール)\n"
        "\n"
        "【その他】\n"
        "  [s] skip  [q] pause  [r] 前件に戻る  [?] ヘルプ"
    )


def _prompt(msg: str, *, infile: TextIO, outfile: TextIO) -> str:
    outfile.write(msg)
    outfile.flush()
    line = infile.readline()
    if not line:
        return ""
    return line.strip()


# ---------------------------------------------------------------------------
# Core annotation loop
# ---------------------------------------------------------------------------


def _annotate_one(
    item: dict,
    infile: TextIO,
    outfile: TextIO,
    rater: str,
) -> Optional[dict]:
    """1 件を annotate。None なら skip / pause を示す。

    戻り値: item に O/comment/... を埋めた dict (skip/pause は None)
    """
    outfile.write("\n" + _format_item(item) + "\n")
    outfile.write(_show_rubric() + "\n")
    start = time.monotonic()
    slow_warned = False

    o: Optional[int] = None
    q1: Optional[str] = None
    q2: Optional[str] = None

    while o is None:
        if not slow_warned and time.monotonic() - start > SLOW_THINK_SECONDS:
            outfile.write("\n[注意] 悩みが長い。skip [s] を推奨。\n")
            slow_warned = True
        prompt_label = "Q1 [a/b/c] or直接 [o/1-5] or [s/q/r/?]: "
        if q1 is not None and q2 is None:
            prompt_label = f"Q1={q1} → Q2 [y/n] or [o/1-5] or [s/q/r/?]: "
        key = _prompt(prompt_label, infile=infile, outfile=outfile).lower()
        if not key:
            outfile.write("(入力が空。もう一度)\n")
            continue
        if key == "?":
            outfile.write(_show_rubric() + "\n")
            continue
        if key == "s":
            return None
        if key == "q":
            return {"_control": "pause"}
        if key == "r":
            return {"_control": "back"}
        if key == "o":
            while True:
                sub = _prompt(
                    "override → O を 1-5 で: ", infile=infile, outfile=outfile
                ).strip()
                if sub in {"1", "2", "3", "4", "5"}:
                    o = int(sub)
                    break
                outfile.write("(1-5 を押してください)\n")
            break
        if key in {"1", "2", "3", "4", "5"}:
            o = int(key)
            break
        if q1 is None and key in {"a", "b", "c"}:
            q1 = key.upper()
            if q1 == "C":
                # C の場合は Q2 に関わらず O=1
                o = DECISION_TREE[("C", "N")]
                break
            continue
        if q1 is not None and q2 is None and key in {"y", "n"}:
            q2 = key.upper()
            o = DECISION_TREE[(q1, q2)]
            break
        outfile.write(f"(無効キー: {key!r})\n")

    # comment 選択
    outfile.write(
        "\n【コメント】\n  "
        + "  ".join(f"[{k}]{v}" for k, v in COMMENT_TEMPLATES.items())
        + "\n"
    )
    comment_key = _prompt("コメント選択 [1-6] (default=5): ",
                          infile=infile, outfile=outfile).strip() or "5"
    comment_label = COMMENT_TEMPLATES.get(comment_key, COMMENT_TEMPLATES["5"])
    detail = ""
    if comment_key == "1":
        detail = _prompt(
            "欠落命題番号 (カンマ区切り, 例 0,2): ",
            infile=infile, outfile=outfile,
        ).strip()
    elif comment_key == "6":
        detail = _prompt(
            "カスタムコメント: ", infile=infile, outfile=outfile,
        ).strip()
    comment_full = comment_label
    if detail:
        comment_full = f"{comment_label}: {detail}"

    out = dict(item)
    out["O"] = str(o)
    out["rater"] = rater
    out["annotated_at"] = datetime.now(timezone.utc).isoformat()
    out["comment"] = comment_full
    return out


# ---------------------------------------------------------------------------
# Session orchestration
# ---------------------------------------------------------------------------


def _inject_blind(
    items: List[dict],
    ha48_rows: List[dict],
    n_blind: int,
    existing_acc40_ids: set,
    seed: int,
) -> List[dict]:
    """items に blind 混入を織り込む.

    blind 行は acc40 id を新規で振り、source="blind", blind_check=元 id.
    user に見えない形式で response/question は持たないので、
    blind は「annotate_ui 起動前に stub にあらかじめ混ぜる」設計が本筋だが、
    本 UI では簡易に items の前後に挿入する。
    """
    if n_blind <= 0 or not ha48_rows:
        return items
    rng = random.Random(seed)
    # HA48 には question/response 本文がない → blind 混入は smoke 上は
    # question/response を空のまま渡す運用。実運用では sampler 側で HA48 対応
    # ペアを stub に含めるのが望ましいが、本 v1 では警告のみ出す。
    chosen = rng.sample(ha48_rows, min(n_blind, len(ha48_rows)))
    blind_items = []
    max_existing = 0
    for i in items + [{"id": x} for x in existing_acc40_ids]:
        m = i.get("id", "")
        if m.startswith("acc40_"):
            try:
                max_existing = max(max_existing, int(m.split("_")[1]))
            except (IndexError, ValueError):
                pass
    next_idx = max_existing + 1
    for c in chosen:
        blind_items.append({
            "id": f"acc40_{next_idx:03d}",
            "source": "blind",
            "question_id": c["id"],
            "question": "(ブラインド項目: 元の質問本文は annotation_48 を参照)",
            "response": "(ブラインド項目: 元の回答本文は annotation_48 を参照)",
            "core_propositions": "[]",
            "hits_total": "",
            "blind_check": c["id"],
        })
        next_idx += 1
    combined = items + blind_items
    rng.shuffle(combined)
    return combined


def _call_incremental_cal(acc40_path: Path) -> None:
    if not INCREMENTAL_CAL.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(INCREMENTAL_CAL), "--acc40", str(acc40_path),
             "--no-run-full"],
            check=False,
        )
    except Exception as exc:  # pragma: no cover - best-effort hook
        print(f"[incremental_cal 失敗: {exc}]", file=sys.stderr)


def run_session(
    rater: str,
    batch_sizes: List[int],
    blind_counts: List[int],
    infile: TextIO,
    outfile: TextIO,
    resume: bool,
    seed: int,
    dry_run: bool,
) -> int:
    # 進捗復元
    state: dict = {
        "rater": rater,
        "batch_index": 0,
        "cursor_in_batch": 0,
        "completed_ids": [],
    }
    if resume:
        loaded = _load_progress()
        if loaded:
            state = loaded
            outfile.write(f"[resume] 進捗復元: batch={state['batch_index']+1}, "
                          f"cursor={state['cursor_in_batch']}\n")

    stub_rows = _load_stub(ACC40_STUB)
    existing_out = _load_existing_output(ACC40_OUT)
    completed_ids = {r["id"] for r in existing_out if r.get("O")}
    all_outputs = list(existing_out)

    # stub から未完成分だけ残し、blind を混入
    pending = [r for r in stub_rows if r["id"] not in completed_ids]
    ha48_for_blind = _load_ha48_for_blind()

    # batch に分割
    batches: List[List[dict]] = []
    idx = 0
    for bs, blind_n in zip(batch_sizes, blind_counts):
        chunk = pending[idx: idx + bs]
        idx += bs
        if not chunk:
            continue
        existing_ids = completed_ids | {r["id"] for b in batches for r in b}
        chunk = _inject_blind(chunk, ha48_for_blind, blind_n, existing_ids, seed + len(batches))
        batches.append(chunk)

    if not batches:
        outfile.write("annotate 対象なし (stub が空か全完了)\n")
        return 0

    for b_i in range(state["batch_index"], len(batches)):
        batch = batches[b_i]
        outfile.write(f"\n=== Batch {b_i+1}/{len(batches)} (n={len(batch)}) ===\n")
        cursor = state["cursor_in_batch"] if b_i == state["batch_index"] else 0
        i = cursor
        while i < len(batch):
            item = batch[i]
            if item["id"] in completed_ids:
                i += 1
                continue
            result = _annotate_one(item, infile, outfile, rater)
            if result is None:
                outfile.write(f"(skip: {item['id']})\n")
                i += 1
                continue
            if result.get("_control") == "pause":
                state["batch_index"] = b_i
                state["cursor_in_batch"] = i
                _save_progress(state)
                if not dry_run:
                    _write_output(all_outputs, ACC40_OUT)
                outfile.write("\n[pause] 進捗を保存して終了。--resume で再開可能。\n")
                return 0
            if result.get("_control") == "back":
                i = max(0, i - 1)
                continue
            # 正常完了
            all_outputs.append(result)
            completed_ids.add(item["id"])
            if not dry_run:
                _write_output(all_outputs, ACC40_OUT)
            i += 1
        # batch 終了
        state["batch_index"] = b_i + 1
        state["cursor_in_batch"] = 0
        _save_progress(state)
        outfile.write(f"\n[batch {b_i+1} 終了] 休憩を推奨。\n")
        if not dry_run:
            _call_incremental_cal(ACC40_OUT)
            cont = _prompt("次の batch へ進む? [y/n]: ",
                           infile=infile, outfile=outfile).lower().strip()
            if cont != "y":
                outfile.write("セッション終了。\n")
                return 0

    outfile.write("\n全 batch 完了。\n")
    return 0


def run_stability_check(
    rater: str,
    infile: TextIO,
    outfile: TextIO,
    seed: int,
    n_sample: int,
) -> int:
    """過去の annotation 行から n_sample 件を再評価して |Δ| を表示."""
    existing = _load_existing_output(ACC40_OUT)
    annotated = [r for r in existing if r.get("O") and not r.get("blind_check")]
    if len(annotated) < n_sample:
        outfile.write(f"既 annotation {len(annotated)} 件 < 目標 {n_sample}。中止。\n")
        return 1
    rng = random.Random(seed)
    chosen = rng.sample(annotated, n_sample)
    deltas: List[int] = []
    for c in chosen:
        # 古い O を一旦伏せて再 annotate
        item = dict(c)
        item["O"] = ""
        item["comment"] = ""
        outfile.write(f"\n[stability-check: {c['id']} の再評価]\n")
        result = _annotate_one(item, infile, outfile, rater)
        if result is None or result.get("_control"):
            outfile.write("(skip)\n")
            continue
        old = int(c["O"])
        new = int(result["O"])
        deltas.append(new - old)
        outfile.write(f"  元 O={old} → 新 O={new}  (Δ={new - old:+d})\n")
    if deltas:
        mean_abs = sum(abs(d) for d in deltas) / len(deltas)
        bias = sum(deltas) / len(deltas)
        outfile.write(
            f"\n集計: n={len(deltas)}, |Δ|平均={mean_abs:.3f}, bias={bias:+.3f}\n"
        )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rater", default="user")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--batch-size", type=int, nargs="+", default=BATCH_SIZES,
        help="batch 毎の件数 (デフォルト 15 10 10)",
    )
    parser.add_argument(
        "--blind-count", type=int, nargs="+", default=BLIND_COUNTS,
        help="batch 毎の blind 混入件数",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="output CSV に書き出さない (smoke test 用)",
    )
    parser.add_argument(
        "--stability-check", action="store_true",
        help="過去 annotation の再評価 (任意実行)",
    )
    parser.add_argument(
        "--stability-n", type=int, default=5,
        help="stability-check サンプル数",
    )
    args = parser.parse_args(argv)

    if args.stability_check:
        return run_stability_check(
            args.rater, sys.stdin, sys.stdout, args.seed, args.stability_n
        )

    return run_session(
        args.rater, args.batch_size, args.blind_count,
        sys.stdin, sys.stdout, args.resume, args.seed, args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
