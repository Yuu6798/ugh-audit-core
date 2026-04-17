"""analysis/annotation_sampler.py — accept40 アノテート候補抽出

docs/annotation_protocol.md §3.1 の priority A（v5 ベースラインの未アノテート
accept/borderline）から候補を抽出し、アノテート用 CSV スタブを出力する。

priority B（experiments/orchestrator.py による新規生成）はこのスクリプトでは
扱わず、別途 orchestrator を叩いて得られた jsonl を `--orchestrator-jsonl`
で追加する運用とする（LLM 呼び出しをオフライン smoke test から隔離するため）。

使い方:
    python analysis/annotation_sampler.py --batch-size 15
    python analysis/annotation_sampler.py --batch-size 10 --offset 15
    python analysis/annotation_sampler.py --orchestrator-jsonl path/to/gen.jsonl

出力:
    data/human_annotation_accept40/annotation_accept40_stub.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
V5_PATH = ROOT / "data" / "eval" / "audit_102_main_baseline_v5.csv"
QMETA_PATH = (
    ROOT / "data" / "question_sets" / "q_metadata_structural_reviewed_102q.jsonl"
)
RESPONSES_PATH = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"
OUT_CSV = ROOT / "data" / "human_annotation_accept40" / "annotation_accept40_stub.csv"

DELTA_E_ACCEPT = 0.10
DELTA_E_BORDERLINE_MAX = 0.15


def _compute_delta_e(s: float, c: float) -> float:
    raw = 2.0 * (1.0 - s) + 1.0 * (1.0 - c)
    return max(0.0, min(1.0, raw / 3.0))


def _load_ha48_ids() -> set:
    ids: set = set()
    with open(HA48_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ids.add(row["id"])
    return ids


def _load_v5() -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    with open(V5_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["id"]] = row
    return result


def _load_qmeta() -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    with open(QMETA_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            result[d["id"]] = d
    return result


def _load_responses() -> Dict[str, str]:
    result: Dict[str, str] = {}
    with open(RESPONSES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            result[d["id"]] = d.get("response", "")
    return result


def _core_propositions(qmeta: dict) -> List[str]:
    """question meta から core_propositions を抽出 (複数パスを試す).

    reviewed_102q.jsonl は `original_core_propositions` がリスト本体。
    他スキーマ互換のため top-level `core_propositions`,
    structural_meta.core_propositions も許容する。
    """
    for key in ("original_core_propositions", "core_propositions"):
        cp = qmeta.get(key)
        if isinstance(cp, list) and cp:
            return cp
    s = qmeta.get("structural_meta") or {}
    cp = s.get("core_propositions")
    if isinstance(cp, list) and cp:
        return cp
    return []


def collect_priority_a(
    ha48_ids: set,
    v5: Dict[str, dict],
    qmeta: Dict[str, dict],
    responses: Dict[str, str],
) -> List[dict]:
    """priority A: v5 で未アノテートの accept/borderline 候補."""
    out: List[dict] = []
    for qid, row in v5.items():
        if qid in ha48_ids:
            continue
        try:
            s = float(row["S"])
            c = float(row["C"])
        except (ValueError, KeyError):
            continue
        de = _compute_delta_e(s, c)
        if de > DELTA_E_BORDERLINE_MAX:
            continue
        source = "v5_unannotated" if de <= DELTA_E_ACCEPT else "v5_borderline"
        meta = qmeta.get(qid, {})
        resp = responses.get(qid, "")
        if not resp:
            # 回答本文が取れないと annotate 不能
            continue
        propositions = _core_propositions(meta)
        if not propositions:
            # core_propositions がないと O 判定の基準が立たない
            continue
        out.append(
            {
                "question_id": qid,
                "source": source,
                "question": meta.get("question", ""),
                "response": resp,
                "core_propositions": json.dumps(propositions, ensure_ascii=False),
                "hits_total": f"{row.get('hits', '')}/{row.get('total', '')}",
                "delta_e": f"{de:.4f}",
            }
        )
    # accept 優先、次に borderline
    out.sort(key=lambda r: (r["source"] != "v5_unannotated", r["question_id"]))
    return out


def collect_orchestrator(path: Path, ha48_ids: set) -> List[dict]:
    """priority B: 既存 orchestrator 出力 jsonl を読み込む.

    期待する jsonl schema (1 行 1 回答):
        {"question_id", "source" (e.g. "orchestrator_claude"), "question",
         "response", "core_propositions", "hits_total" (optional),
         "delta_e" (optional, ΔE ≤ 0.15 でフィルタ済みを想定)}
    """
    if not path.exists():
        return []
    out: List[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            qid = d.get("question_id")
            if not qid or qid in ha48_ids:
                continue
            propositions = d.get("core_propositions")
            if isinstance(propositions, list):
                propositions_str = json.dumps(propositions, ensure_ascii=False)
            else:
                propositions_str = str(propositions or "")
            if not propositions_str or propositions_str == "[]":
                continue
            out.append(
                {
                    "question_id": qid,
                    "source": d.get("source", "orchestrator"),
                    "question": d.get("question", ""),
                    "response": d.get("response", ""),
                    "core_propositions": propositions_str,
                    "hits_total": d.get("hits_total", ""),
                    "delta_e": d.get("delta_e", ""),
                }
            )
    return out


def _stratified_shuffle(candidates: List[dict], seed: int) -> List[dict]:
    """source 別に shuffle した上で round-robin で混ぜる.

    A (v5_unannotated) → B (orchestrator_*) → C (v5_borderline) の
    順で 1 件ずつ interleave する。単一 source への偏りを避けつつ
    優先度の高いものを前倒しする。
    """
    rng = random.Random(seed)
    buckets: Dict[str, List[dict]] = {}
    for c in candidates:
        buckets.setdefault(c["source"], []).append(c)
    for b in buckets.values():
        rng.shuffle(b)

    def bucket_priority(name: str) -> int:
        if name == "v5_unannotated":
            return 0
        if name.startswith("orchestrator"):
            return 1
        if name == "v5_borderline":
            return 2
        return 3

    ordered_sources = sorted(buckets.keys(), key=bucket_priority)
    result: List[dict] = []
    while any(buckets[s] for s in ordered_sources):
        for s in ordered_sources:
            if buckets[s]:
                result.append(buckets[s].pop(0))
    return result


def assign_acc40_ids(rows: List[dict], start: int = 1) -> List[dict]:
    """acc40_NNN 形式の id を連番で振る."""
    for i, r in enumerate(rows, start=start):
        r["id"] = f"acc40_{i:03d}"
    return rows


def write_csv(rows: List[dict], path: Path) -> None:
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
            writer.writerow(
                {
                    "id": r["id"],
                    "source": r["source"],
                    "question_id": r["question_id"],
                    "question": r["question"],
                    "response": r["response"],
                    "core_propositions": r["core_propositions"],
                    "O": "",
                    "rater": "",
                    "annotated_at": "",
                    "comment": "",
                    "blind_check": "",
                    "hits_total": r.get("hits_total", ""),
                }
            )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--orchestrator-jsonl", type=Path,
        help="priority B の既生成回答 (省略時は priority A のみ)",
    )
    parser.add_argument(
        "--append", action="store_true",
        help="既存 stub CSV に追記 (既存 acc40 id は維持)",
    )
    args = parser.parse_args(argv)

    ha48_ids = _load_ha48_ids()
    v5 = _load_v5()
    qmeta = _load_qmeta()
    responses = _load_responses()

    candidates = collect_priority_a(ha48_ids, v5, qmeta, responses)
    if args.orchestrator_jsonl:
        candidates.extend(collect_orchestrator(args.orchestrator_jsonl, ha48_ids))

    ordered = _stratified_shuffle(candidates, args.seed)
    window = ordered[args.offset : args.offset + args.batch_size]

    start_id = 1
    if args.append and OUT_CSV.exists():
        with open(OUT_CSV, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
        start_id = len(existing) + 1
        assigned = assign_acc40_ids(window, start=start_id)
        combined = existing + [
            {**r, "O": r.get("O", ""), "rater": "", "annotated_at": "",
             "comment": "", "blind_check": ""}
            for r in assigned
        ]
        write_csv(assigned, OUT_CSV)  # 書き込みは新規分のみ上書き (append 実装は簡略化)
        # append モードでは既存行を保持して再書き出し
        with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
            fieldnames = [
                "id", "source", "question_id", "question", "response",
                "core_propositions", "O", "rater", "annotated_at", "comment",
                "blind_check", "hits_total",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in combined:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
    else:
        assigned = assign_acc40_ids(window, start=start_id)
        write_csv(assigned, OUT_CSV)

    print(f"抽出: {len(window)} 件 (候補プール {len(ordered)} 件中)")
    print(f"出力: {OUT_CSV}")
    source_counts: Dict[str, int] = {}
    for r in window:
        source_counts[r["source"]] = source_counts.get(r["source"], 0) + 1
    for src, n in sorted(source_counts.items()):
        print(f"  {src}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
