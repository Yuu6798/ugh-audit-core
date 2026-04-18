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
import os
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
# blind 行の question/response/core_propositions を再構築するための元ソース
QMETA_PATH = (
    ROOT / "data" / "question_sets" / "q_metadata_structural_reviewed_102q.jsonl"
)
RESPONSES_PATH = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"
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


def _progress_path() -> Path:
    """Return progress json path (override via UGH_ANNOTATION_PROGRESS_PATH)."""
    override = os.environ.get("UGH_ANNOTATION_PROGRESS_PATH")
    if override:
        return Path(override)
    return PROGRESS_PATH


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
        "blind_check", "hits_total", "delta_e",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def _load_jsonl_map(path: Path, key: str = "id") -> Dict[str, dict]:
    """jsonl から key 列の値をキーにした dict を返す (blind enrichment 用)."""
    result: Dict[str, dict] = {}
    if not path.exists():
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            k = d.get(key)
            if k is not None:
                result[k] = d
    return result


def _load_ha48_for_blind() -> List[dict]:
    """HA48 から blind 混入候補を抽出し question/response/core_propositions で
    enrich した行を返す.

    placeholder のままだと rater は core_propositions を見て判定できず、
    blind annotation が guesswork になって |Δ|/bias check が機能しない。
    qmeta + responses から実コンテンツを結合できた行のみ blind 候補に採用。
    enrichment 不能 (元データ欠落) の行は除外する。
    """
    rows: List[dict] = []
    if not HA48_PATH.exists():
        return rows
    qmeta = _load_jsonl_map(QMETA_PATH)
    responses = _load_jsonl_map(RESPONSES_PATH)
    with open(HA48_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("O"):
                continue
            qid = row["id"]
            meta = qmeta.get(qid)
            resp_entry = responses.get(qid)
            if not meta or not resp_entry:
                # 再構築不能 → blind 対象外
                continue
            response_text = resp_entry.get("response", "")
            if not response_text:
                continue
            core_props = meta.get("original_core_propositions") or []
            enriched = dict(row)
            enriched["question"] = meta.get("question", "")
            enriched["response"] = response_text
            enriched["core_propositions"] = json.dumps(
                core_props, ensure_ascii=False
            )
            rows.append(enriched)
    return rows


def _save_progress(state: dict) -> None:
    progress_path = _progress_path()
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _load_progress() -> Optional[dict]:
    progress_path = _progress_path()
    if not progress_path.exists():
        return None
    with open(progress_path, encoding="utf-8") as f:
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
    seed: int,
    exclude_orig_ids: Optional[set] = None,
) -> tuple:
    """items に blind 混入を織り込む.

    blind 行の id は `blind_{orig_id}` 形式で採番。stub の `acc40_NNN`
    名前空間と分離されているため、後続 batch の stub id と衝突しない。

    exclude_orig_ids: 既に別 batch で使われた HA48 id 集合。ここから
    抽選対象を外すことで、同一 HA48 行が複数 batch に現れて `blind_{id}`
    が重複し completed_ids 照合で skip される事故を防ぐ。

    戻り値: (blind 混入後の items, 今回選ばれた orig_id 集合)
    """
    if n_blind <= 0 or not ha48_rows:
        return items, set()
    rng = random.Random(seed)
    pool = [r for r in ha48_rows if r["id"] not in (exclude_orig_ids or set())]
    if not pool:
        return items, set()
    chosen = rng.sample(pool, min(n_blind, len(pool)))
    blind_items = []
    chosen_ids: set = set()
    for c in chosen:
        orig_id = c["id"]
        chosen_ids.add(orig_id)
        # c は _load_ha48_for_blind で qmeta/responses から enrich 済み。
        # enrichment 不能な HA48 id は load 時点で除外済みなので
        # ここでは空文字を気にせず採用する。
        blind_items.append({
            "id": f"blind_{orig_id}",
            "source": "blind",
            "question_id": orig_id,
            "question": c.get("question", ""),
            "response": c.get("response", ""),
            "core_propositions": c.get("core_propositions", "[]"),
            "hits_total": "",
            "blind_check": orig_id,
        })
    combined = items + blind_items
    rng.shuffle(combined)
    return combined, chosen_ids


def _call_incremental_cal(acc40_path: Path) -> None:
    """batch 境界で run_incremental_calibration を呼ぶ.

    --no-run-full は付けない: 内部で accept subset の n をチェックし、
    目標未達ならスキップ、到達していれば full grid を回して
    STOP 推奨を出す設計になっている。
    """
    if not INCREMENTAL_CAL.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(INCREMENTAL_CAL), "--acc40", str(acc40_path)],
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

    # 注: stub_rows は resume 時も decompose せずそのまま batch 化する。
    # 既アノテート分は内部ループで skip する (cursor_in_batch 位置を
    # 保つため、事前フィルタは行わない — これをすると batch 内 index が
    # ズレて未アノテート行を飛ばす resume バグの原因になる)。
    ha48_for_blind = _load_ha48_for_blind()

    # batch_sizes と blind_counts の長さ不整合補正 (zip truncate 回避)
    if len(blind_counts) < len(batch_sizes):
        pad = len(batch_sizes) - len(blind_counts)
        outfile.write(
            f"[warn] blind_counts {len(blind_counts)} 件が batch_sizes "
            f"{len(batch_sizes)} 件より短い。後続 {pad} batch を blind=0 で埋める。\n"
        )
        blind_counts = list(blind_counts) + [0] * pad
    elif len(blind_counts) > len(batch_sizes):
        pad = len(blind_counts) - len(batch_sizes)
        outfile.write(
            f"[warn] blind_counts {len(blind_counts)} 件が batch_sizes "
            f"{len(batch_sizes)} 件より長い。余剰 {pad} 件を truncate する。\n"
        )
        blind_counts = list(blind_counts)[: len(batch_sizes)]

    # batch に分割 (blind は batch 間で orig_id の重複抽選を禁止)
    batches: List[List[dict]] = []
    used_blind_orig_ids: set = set()
    idx = 0
    for bs, blind_n in zip(batch_sizes, blind_counts):
        chunk = stub_rows[idx: idx + bs]
        idx += bs
        if not chunk:
            continue
        chunk, chosen_ids = _inject_blind(
            chunk, ha48_for_blind, blind_n,
            seed=seed + len(batches),
            exclude_orig_ids=used_blind_orig_ids,
        )
        used_blind_orig_ids.update(chosen_ids)
        batches.append(chunk)

    # 設定された batch_sizes の総和を超える stub 行は末尾 batch として追加する
    # (silently drop しない)。末尾 batch の blind は直近 blind_count を継承。
    if idx < len(stub_rows):
        tail = stub_rows[idx:]
        tail_blind_n = blind_counts[-1] if blind_counts else 0
        outfile.write(
            f"[warn] batch_sizes の総和 ({idx}) より stub 行が {len(tail)} 件多い。"
            f"末尾 batch として追加 (blind={tail_blind_n})。\n"
        )
        chunk, chosen_ids = _inject_blind(
            tail, ha48_for_blind, tail_blind_n,
            seed=seed + len(batches),
            exclude_orig_ids=used_blind_orig_ids,
        )
        used_blind_orig_ids.update(chosen_ids)
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
                # 前件に戻る: 直前の annotated 分を completed/all_outputs から
                # 取り下げて再 annotate できるようにする。
                # そうしないと内部ループの「completed_ids 照合で skip」が
                # 発火して「戻ったつもりが次へ進む」挙動になる。
                if i > 0:
                    prev_id = batch[i - 1]["id"]
                    if prev_id in completed_ids:
                        completed_ids.discard(prev_id)
                        all_outputs = [
                            r for r in all_outputs if r.get("id") != prev_id
                        ]
                        if not dry_run:
                            _write_output(all_outputs, ACC40_OUT)
                    i -= 1
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


def _normalize_state(rater: str, resume: bool) -> dict:
    state: dict = {
        "rater": rater,
        "batch_index": 0,
        "cursor_in_batch": 0,
        "completed_ids": [],
    }
    if resume:
        loaded = _load_progress()
        if loaded:
            state.update(loaded)
    return state


def _build_batches_for_step_mode(
    stub_rows: List[dict],
    batch_sizes: List[int],
    blind_counts: List[int],
    seed: int,
) -> List[List[dict]]:
    """Build deterministic batches identical to run_session."""
    ha48_for_blind = _load_ha48_for_blind()
    b_counts = list(blind_counts)
    if len(b_counts) < len(batch_sizes):
        b_counts = b_counts + [0] * (len(batch_sizes) - len(b_counts))
    elif len(b_counts) > len(batch_sizes):
        b_counts = b_counts[: len(batch_sizes)]

    batches: List[List[dict]] = []
    used_blind_orig_ids: set = set()
    idx = 0
    for bs, blind_n in zip(batch_sizes, b_counts):
        chunk = stub_rows[idx: idx + bs]
        idx += bs
        if not chunk:
            continue
        chunk, chosen_ids = _inject_blind(
            chunk,
            ha48_for_blind,
            blind_n,
            seed=seed + len(batches),
            exclude_orig_ids=used_blind_orig_ids,
        )
        used_blind_orig_ids.update(chosen_ids)
        batches.append(chunk)

    if idx < len(stub_rows):
        tail = stub_rows[idx:]
        tail_blind_n = b_counts[-1] if b_counts else 0
        chunk, chosen_ids = _inject_blind(
            tail,
            ha48_for_blind,
            tail_blind_n,
            seed=seed + len(batches),
            exclude_orig_ids=used_blind_orig_ids,
        )
        used_blind_orig_ids.update(chosen_ids)
        batches.append(chunk)
    return batches


def _seek_next_pending(
    batches: List[List[dict]],
    completed_ids: set,
    batch_index: int,
    cursor_in_batch: int,
) -> tuple[int, int]:
    """Return the next unresolved (batch_index, cursor) or (len(batches), 0)."""
    b_i = max(0, batch_index)
    i = max(0, cursor_in_batch)
    while b_i < len(batches):
        batch = batches[b_i]
        while i < len(batch):
            if batch[i]["id"] in completed_ids:
                i += 1
                continue
            return b_i, i
        b_i += 1
        i = 0
    return len(batches), 0


def _compose_comment(comment_key: str, comment_detail: str) -> str:
    base = COMMENT_TEMPLATES.get(comment_key, COMMENT_TEMPLATES["5"])
    detail = (comment_detail or "").strip()
    if detail:
        return f"{base}: {detail}"
    return base


def _derive_o_from_inputs(
    *,
    override_o: Optional[int],
    q1: Optional[str],
    q2: Optional[str],
) -> int:
    if override_o is not None:
        return int(override_o)

    if not q1:
        raise ValueError("q1 is required when --o is not provided")
    q1_n = q1.strip().upper()
    if q1_n not in {"A", "B", "C"}:
        raise ValueError("q1 must be one of A/B/C")

    if q1_n == "C":
        return DECISION_TREE[("C", "N")]

    if not q2:
        raise ValueError("q2 is required when q1 is A or B")
    q2_n = q2.strip().upper()
    if q2_n not in {"Y", "N"}:
        raise ValueError("q2 must be one of Y/N")
    return DECISION_TREE[(q1_n, q2_n)]


def run_step_mode(
    *,
    rater: str,
    batch_sizes: List[int],
    blind_counts: List[int],
    resume: bool,
    seed: int,
    dry_run: bool,
    show_next: bool,
    annotate_current: bool,
    skip_current: bool,
    item_id: Optional[str],
    q1: Optional[str],
    q2: Optional[str],
    override_o: Optional[int],
    comment_key: str,
    comment_detail: str,
    outfile: TextIO,
) -> int:
    stub_rows = _load_stub(ACC40_STUB)
    if not stub_rows:
        outfile.write("stub CSV がありません。先に annotation_sampler.py を実行してください。\n")
        return 1

    batches = _build_batches_for_step_mode(
        stub_rows=stub_rows,
        batch_sizes=batch_sizes,
        blind_counts=blind_counts,
        seed=seed,
    )
    if not batches:
        outfile.write("batch が作れませんでした。\n")
        return 1

    all_outputs = _load_existing_output(ACC40_OUT)
    completed_ids = {r["id"] for r in all_outputs if r.get("O")}

    state = _normalize_state(rater=rater, resume=resume)
    b_i, i = _seek_next_pending(
        batches=batches,
        completed_ids=completed_ids,
        batch_index=state.get("batch_index", 0),
        cursor_in_batch=state.get("cursor_in_batch", 0),
    )
    state["batch_index"] = b_i
    state["cursor_in_batch"] = i

    if b_i >= len(batches):
        _save_progress(state)
        outfile.write("[done] すべての対象が処理済みです。\n")
        return 0

    current = batches[b_i][i]
    if item_id and item_id != current["id"]:
        outfile.write(
            f"[error] current item id mismatch: expected={current['id']} provided={item_id}\n"
        )
        return 2

    if show_next:
        _save_progress(state)
        batch_total = len(batches[b_i])
        remaining_in_batch = sum(
            1 for row in batches[b_i][i:] if row["id"] not in completed_ids
        )
        outfile.write(
            f"[next] batch={b_i+1}/{len(batches)} index={i+1}/{batch_total} "
            f"remaining_in_batch={remaining_in_batch}\n"
        )
        outfile.write(_format_item(current) + "\n")
        return 0

    if skip_current:
        old_b_i = b_i
        next_b_i, next_i = _seek_next_pending(
            batches=batches,
            completed_ids=completed_ids,
            batch_index=b_i,
            cursor_in_batch=i + 1,
        )
        state["batch_index"] = next_b_i
        state["cursor_in_batch"] = next_i
        _save_progress(state)
        outfile.write(f"[skip] {current['id']}\n")
        if next_b_i > old_b_i and not dry_run:
            _call_incremental_cal(ACC40_OUT)
        return 0

    if annotate_current:
        try:
            o_val = _derive_o_from_inputs(
                override_o=override_o,
                q1=q1,
                q2=q2,
            )
        except ValueError as exc:
            outfile.write(f"[error] {exc}\n")
            return 2

        row = dict(current)
        row["O"] = str(o_val)
        row["rater"] = rater
        row["annotated_at"] = datetime.now(timezone.utc).isoformat()
        row["comment"] = _compose_comment(comment_key=comment_key, comment_detail=comment_detail)

        # overwrite-safe upsert
        all_outputs = [r for r in all_outputs if r.get("id") != row["id"]]
        all_outputs.append(row)
        completed_ids.add(row["id"])
        if not dry_run:
            _write_output(all_outputs, ACC40_OUT)

        old_b_i = b_i
        next_b_i, next_i = _seek_next_pending(
            batches=batches,
            completed_ids=completed_ids,
            batch_index=b_i,
            cursor_in_batch=i + 1,
        )
        state["batch_index"] = next_b_i
        state["cursor_in_batch"] = next_i
        _save_progress(state)

        outfile.write(
            f"[saved] id={row['id']} O={row['O']} "
            f"batch={old_b_i+1}/{len(batches)} index={i+1}/{len(batches[old_b_i])}\n"
        )
        if next_b_i > old_b_i and not dry_run:
            _call_incremental_cal(ACC40_OUT)
            outfile.write(f"[batch {old_b_i+1} completed] incremental calibration triggered.\n")
        if next_b_i >= len(batches):
            outfile.write("[done] すべての対象が処理済みです。\n")
        else:
            nxt = batches[next_b_i][next_i]
            outfile.write(f"[next-id] {nxt['id']}\n")
        return 0

    outfile.write("[error] no step action selected.\n")
    return 2


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
    parser.add_argument(
        "--step-next", action="store_true",
        help="非対話ステップモード: 現在の対象1件を表示して終了",
    )
    parser.add_argument(
        "--step-annotate", action="store_true",
        help="非対話ステップモード: 現在の対象1件を記録して次へ進む",
    )
    parser.add_argument(
        "--step-skip", action="store_true",
        help="非対話ステップモード: 現在の対象1件を skip して次へ進む",
    )
    parser.add_argument(
        "--item-id",
        help="step 実行時の安全確認用 (現在対象IDと一致必須)",
    )
    parser.add_argument(
        "--q1", choices=["a", "b", "c", "A", "B", "C"],
        help="step-annotate 用 Q1",
    )
    parser.add_argument(
        "--q2", choices=["y", "n", "Y", "N"],
        help="step-annotate 用 Q2 (Q1=A/B の場合必須)",
    )
    parser.add_argument(
        "--o", type=int, choices=[1, 2, 3, 4, 5],
        help="step-annotate 用 override O (指定時は q1/q2 不要)",
    )
    parser.add_argument(
        "--comment-key", choices=["1", "2", "3", "4", "5", "6"], default="5",
        help="step-annotate 用コメントテンプレート番号",
    )
    parser.add_argument(
        "--comment-detail", default="",
        help="step-annotate 用コメント詳細",
    )
    args = parser.parse_args(argv)
    step_modes = [args.step_next, args.step_annotate, args.step_skip]
    if sum(1 for f in step_modes if f) > 1:
        parser.error("use only one of --step-next / --step-annotate / --step-skip")

    if args.stability_check:
        return run_stability_check(
            args.rater, sys.stdin, sys.stdout, args.seed, args.stability_n
        )

    if any(step_modes):
        return run_step_mode(
            rater=args.rater,
            batch_sizes=args.batch_size,
            blind_counts=args.blind_count,
            resume=args.resume,
            seed=args.seed,
            dry_run=args.dry_run,
            show_next=args.step_next,
            annotate_current=args.step_annotate,
            skip_current=args.step_skip,
            item_id=args.item_id,
            q1=args.q1,
            q2=args.q2,
            override_o=args.o,
            comment_key=args.comment_key,
            comment_detail=args.comment_detail,
            outfile=sys.stdout,
        )

    return run_session(
        args.rater, args.batch_size, args.blind_count,
        sys.stdin, sys.stdout, args.resume, args.seed, args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
