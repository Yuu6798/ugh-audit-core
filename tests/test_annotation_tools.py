"""HA-accept40 アノテーションツールの smoke test + 単体テスト."""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import annotation_blind_check as blind_mod  # noqa: E402
from analysis import annotation_sampler as sampler_mod  # noqa: E402
from analysis import annotation_ui as ui_mod  # noqa: E402
from analysis import merge_ha48_accept40 as merge_mod  # noqa: E402
from analysis import run_incremental_calibration as cal_mod  # noqa: E402


# ---------------------------------------------------------------------------
# annotation_sampler
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_jsonl(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@pytest.fixture
def fake_data(tmp_path, monkeypatch):
    """sampler / merge が参照する全パスを tmp_path に差し替える."""
    ha48 = tmp_path / "ha48.csv"
    v5 = tmp_path / "v5.csv"
    qmeta = tmp_path / "qmeta.jsonl"
    resp = tmp_path / "resp.jsonl"
    out_stub = tmp_path / "stub.csv"
    out_acc40 = tmp_path / "annotation_accept40.csv"

    _write_csv(
        ha48,
        [
            {"id": "q001", "category": "x", "S": "2", "C": "3", "O": "4",
             "propositions_hit": "2/3", "notes": ""},
        ],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        v5,
        [
            # q002: accept 相当 (ΔE=0), NOT in HA48
            {"id": "q002", "category": "x", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "3", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "1.0", "C": "1.0", "dE": "0", "decision": "accept"},
            # q003: borderline (ΔE ≈ 0.11), NOT in HA48
            {"id": "q003", "category": "y", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "2", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "0.95", "C": "0.70", "dE": "0", "decision": "rewrite"},
            # q001: in HA48 → skip
            {"id": "q001", "category": "x", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "3", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "1.0", "C": "1.0", "dE": "0", "decision": "accept"},
            # q004: regenerate 相当 → filter で弾かれる
            {"id": "q004", "category": "z", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "0", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "0.5", "C": "0.3", "dE": "0", "decision": "regenerate"},
        ],
        ["id", "category", "trap_type", "f1", "f2", "f3", "f4", "hits",
         "total", "hit_ids", "miss_ids", "hit_sources", "S", "C", "dE",
         "decision"],
    )
    _write_jsonl(
        qmeta,
        [
            {"id": "q001", "category": "x", "question": "Q1?",
             "original_core_propositions": ["p1", "p2", "p3"]},
            {"id": "q002", "category": "x", "question": "Q2?",
             "original_core_propositions": ["p1", "p2", "p3"]},
            {"id": "q003", "category": "y", "question": "Q3?",
             "original_core_propositions": ["p1"]},
            {"id": "q004", "category": "z", "question": "Q4?",
             "original_core_propositions": ["p1"]},
        ],
    )
    _write_jsonl(
        resp,
        [
            {"id": "q001", "response": "A1"},
            {"id": "q002", "response": "A2"},
            {"id": "q003", "response": "A3"},
            {"id": "q004", "response": "A4"},
        ],
    )

    monkeypatch.setattr(sampler_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(sampler_mod, "V5_PATH", v5)
    monkeypatch.setattr(sampler_mod, "QMETA_PATH", qmeta)
    monkeypatch.setattr(sampler_mod, "RESPONSES_PATH", resp)
    monkeypatch.setattr(sampler_mod, "OUT_CSV", out_stub)
    monkeypatch.setattr(merge_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(merge_mod, "V5_PATH", v5)
    monkeypatch.setattr(merge_mod, "ACC40_DEFAULT", out_acc40)
    monkeypatch.setattr(blind_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(blind_mod, "ACC40_DEFAULT", out_acc40)
    monkeypatch.setattr(cal_mod, "ACC40_DEFAULT", out_acc40)
    return {
        "ha48": ha48, "v5": v5, "qmeta": qmeta, "resp": resp,
        "stub": out_stub, "acc40": out_acc40,
    }


def test_sampler_extracts_accept_and_borderline(fake_data):
    assert sampler_mod.main(["--batch-size", "5"]) == 0
    with open(fake_data["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # q002 (accept) + q003 (borderline) = 2 件; q001 は HA48 にあるので除外
    assert len(rows) == 2
    sources = {r["source"] for r in rows}
    assert sources == {"v5_unannotated", "v5_borderline"}
    # id 形式は acc40_NNN
    assert all(r["id"].startswith("acc40_") for r in rows)


def test_sampler_batch_size_and_offset(fake_data):
    assert sampler_mod.main(["--batch-size", "1"]) == 0
    with open(fake_data["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # accept が borderline より先にくる (stratified_shuffle の優先度)
    assert len(rows) == 1
    assert rows[0]["source"] == "v5_unannotated"


def test_sampler_orchestrator_jsonl(fake_data, tmp_path):
    gen = tmp_path / "gen.jsonl"
    _write_jsonl(
        gen,
        [
            {"question_id": "q005", "source": "orchestrator_claude",
             "question": "Q5?", "response": "A5",
             "core_propositions": ["p"]},
        ],
    )
    assert sampler_mod.main(
        ["--batch-size", "5", "--orchestrator-jsonl", str(gen)]
    ) == 0
    with open(fake_data["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    sources = [r["source"] for r in rows]
    assert "orchestrator_claude" in sources


# ---------------------------------------------------------------------------
# merge_ha48_accept40
# ---------------------------------------------------------------------------


def test_merge_without_acc40(fake_data):
    out = fake_data["acc40"].parent / "merged.csv"
    assert merge_mod.main(
        ["--acc40", str(fake_data["acc40"]), "--out", str(out)]
    ) == 0
    with open(out, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["source"] == "ha48"


def test_merge_accept_only_filters(fake_data):
    # acc40 に 2 件書き込み (1 件 accept、1 件 borderline)
    _write_csv(
        fake_data["acc40"],
        [
            {"id": "acc40_001", "source": "v5_unannotated",
             "question_id": "q002", "question": "", "response": "",
             "core_propositions": "", "O": "5", "rater": "",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": ""},
            {"id": "acc40_002", "source": "v5_borderline",
             "question_id": "q003", "question": "", "response": "",
             "core_propositions": "", "O": "3", "rater": "",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": ""},
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total"],
    )
    out = fake_data["acc40"].parent / "merged.csv"
    assert merge_mod.main(
        ["--acc40", str(fake_data["acc40"]),
         "--accept-only", "--out", str(out)]
    ) == 0
    with open(out, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # HA48 の q001: v5 での ΔE = (2×(1-2/5)² + (1-3/5)²)/3 ≠ accept → 除外
    # acc40 の q002 (v5_unannotated): 含まれる
    # acc40 の q003 (v5_borderline): accept subset に含めない
    kept_qids = {r["question_id"] for r in rows}
    assert "q002" in kept_qids
    assert "q003" not in kept_qids


def test_merge_excludes_blind_check_rows(fake_data):
    _write_csv(
        fake_data["acc40"],
        [
            {"id": "acc40_001", "source": "blind",
             "question_id": "q001", "question": "", "response": "",
             "core_propositions": "", "O": "3", "rater": "",
             "annotated_at": "", "comment": "", "blind_check": "q001",
             "hits_total": ""},
            {"id": "acc40_002", "source": "v5_unannotated",
             "question_id": "q002", "question": "", "response": "",
             "core_propositions": "", "O": "5", "rater": "",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": ""},
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total"],
    )
    out = fake_data["acc40"].parent / "merged.csv"
    assert merge_mod.main(
        ["--acc40", str(fake_data["acc40"]), "--out", str(out)]
    ) == 0
    with open(out, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # blind_check 行は除外、HA48 の q001 + acc40 の q002 のみ
    sources = [r["source"] for r in rows]
    assert "blind" not in sources
    assert len(rows) == 2


def test_merge_parse_o_handles_float_and_int(fake_data):
    assert merge_mod._parse_o("4") == 4
    assert merge_mod._parse_o("4.0") == 4
    assert merge_mod._parse_o("") is None
    assert merge_mod._parse_o("abc") is None
    assert merge_mod._parse_o("0") is None  # 範囲外
    assert merge_mod._parse_o("6") is None


# ---------------------------------------------------------------------------
# annotation_blind_check
# ---------------------------------------------------------------------------


def test_blind_check_pass_when_within_tolerance(fake_data):
    _write_csv(
        fake_data["acc40"],
        [
            # 元 O=4, 新 O=4 (Δ=0) と 元 O=4, 新 O=5 (Δ=+1)
            {"id": "acc40_001", "source": "blind",
             "question_id": "q001", "question": "", "response": "",
             "core_propositions": "", "O": "4", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "q001",
             "hits_total": ""},
            {"id": "acc40_002", "source": "blind",
             "question_id": "q001", "question": "", "response": "",
             "core_propositions": "", "O": "5", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "q001",
             "hits_total": ""},
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total"],
    )
    ha48_o = blind_mod.load_ha48_o_map()
    pairs = blind_mod.collect_blind_pairs(fake_data["acc40"], ha48_o)
    summary = blind_mod.summarize(pairs)
    assert summary["n"] == 2
    assert summary["mean_abs_delta"] == pytest.approx(0.5)
    assert summary["pass"] is True


def test_blind_check_fail_when_bias_exceeds(fake_data):
    _write_csv(
        fake_data["acc40"],
        [
            # 元 O=4 → 新 O=1 (Δ=-3) 一貫した下げ方向
            {"id": "acc40_001", "source": "blind",
             "question_id": "q001", "question": "", "response": "",
             "core_propositions": "", "O": "1", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "q001",
             "hits_total": ""},
            {"id": "acc40_002", "source": "blind",
             "question_id": "q001", "question": "", "response": "",
             "core_propositions": "", "O": "1", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "q001",
             "hits_total": ""},
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total"],
    )
    ha48_o = blind_mod.load_ha48_o_map()
    pairs = blind_mod.collect_blind_pairs(fake_data["acc40"], ha48_o)
    summary = blind_mod.summarize(pairs)
    assert summary["pass"] is False  # |Δ|=3 > 1.0


# ---------------------------------------------------------------------------
# run_incremental_calibration
# ---------------------------------------------------------------------------


def test_incremental_cal_continues_when_below_target(fake_data):
    """accept subset n < 28 なら CONTINUE を返して full-grid を呼ばない."""
    # acc40 なし状態。HA48 1件のみ。
    assert cal_mod.main(["--acc40", str(fake_data["acc40"]),
                         "--no-run-full"]) == 0


def test_incremental_cal_parse_grid_fire_window(tmp_path, monkeypatch):
    """grid CSV から fire_rate ∈ [10%, 30%] の行を抽出できる."""
    grid = tmp_path / "grid.csv"
    _write_csv(
        grid,
        [
            {"tau_collapse_high": "0.20", "tau_anchor_low": "0.60",
             "fire_rate": "0.80", "rho_advisory_full": "0.3"},
            {"tau_collapse_high": "0.26", "tau_anchor_low": "0.76",
             "fire_rate": "0.15", "rho_advisory_full": "0.55"},
            {"tau_collapse_high": "0.40", "tau_anchor_low": "0.80",
             "fire_rate": "0.05", "rho_advisory_full": "0.4"},
        ],
        ["tau_collapse_high", "tau_anchor_low", "fire_rate",
         "rho_advisory_full"],
    )
    rows = cal_mod.parse_grid_for_fire_window(grid)
    assert len(rows) == 1
    assert rows[0]["tau_collapse_high"] == "0.26"


# ---------------------------------------------------------------------------
# annotation_ui (dry-run + mock stdin)
# ---------------------------------------------------------------------------


def test_ui_decision_tree_mapping_constants():
    """DECISION_TREE の mapping が rubric と一致."""
    assert ui_mod.DECISION_TREE[("A", "N")] == 5
    assert ui_mod.DECISION_TREE[("A", "Y")] == 4
    assert ui_mod.DECISION_TREE[("B", "N")] == 3
    assert ui_mod.DECISION_TREE[("B", "Y")] == 2
    assert ui_mod.DECISION_TREE[("C", "N")] == 1
    assert ui_mod.DECISION_TREE[("C", "Y")] == 1


def test_ui_annotate_one_decision_tree_flow():
    """a → n → comment=5 の入力で O=5 が記録される."""
    item = {"id": "acc40_001", "source": "v5_unannotated",
            "question_id": "q001", "question": "Q?", "response": "R",
            "core_propositions": "[]", "hits_total": "3/3"}
    infile = io.StringIO("a\nn\n5\n")
    outfile = io.StringIO()
    result = ui_mod._annotate_one(item, infile, outfile, "t")
    assert result is not None
    assert result["O"] == "5"
    assert result["rater"] == "t"
    assert result["annotated_at"]  # タイムスタンプが入っている


def test_ui_annotate_one_override():
    """o 押下 → 数字で直接 O を入力."""
    item = {"id": "x", "source": "", "question_id": "", "question": "",
            "response": "", "core_propositions": "[]"}
    infile = io.StringIO("o\n3\n5\n")
    outfile = io.StringIO()
    result = ui_mod._annotate_one(item, infile, outfile, "t")
    assert result is not None
    assert result["O"] == "3"


def test_ui_annotate_one_skip():
    item = {"id": "x", "source": "", "question_id": "", "question": "",
            "response": "", "core_propositions": "[]"}
    infile = io.StringIO("s\n")
    outfile = io.StringIO()
    result = ui_mod._annotate_one(item, infile, outfile, "t")
    assert result is None


def test_ui_annotate_one_pause():
    item = {"id": "x", "source": "", "question_id": "", "question": "",
            "response": "", "core_propositions": "[]"}
    infile = io.StringIO("q\n")
    outfile = io.StringIO()
    result = ui_mod._annotate_one(item, infile, outfile, "t")
    assert result is not None
    assert result.get("_control") == "pause"


def test_ui_c_path_short_circuits_to_o1():
    """Q1=c は Q2 を問わず O=1 になる."""
    item = {"id": "x", "source": "", "question_id": "", "question": "",
            "response": "", "core_propositions": "[]"}
    infile = io.StringIO("c\n5\n")  # c だけで確定、続く 5 は comment 選択
    outfile = io.StringIO()
    result = ui_mod._annotate_one(item, infile, outfile, "t")
    assert result is not None
    assert result["O"] == "1"


# ---------------------------------------------------------------------------
# Codex PR #85 回帰テスト
# ---------------------------------------------------------------------------


def test_blind_ids_do_not_collide_with_stub_ids():
    """P1-2 回帰: blind ID が `blind_*` 名前空間を使い stub `acc40_*` と衝突しない."""
    stub_chunk = [
        {"id": "acc40_001", "source": "v5_unannotated"},
        {"id": "acc40_002", "source": "v5_unannotated"},
        {"id": "acc40_016", "source": "v5_unannotated"},  # 後続 batch の id 相当
    ]
    ha48_rows = [
        {"id": "q001", "O": "3"},
        {"id": "q002", "O": "4"},
    ]
    result = ui_mod._inject_blind(stub_chunk, ha48_rows, n_blind=2, seed=1)
    stub_ids = {r["id"] for r in stub_chunk}
    blind_ids = {r["id"] for r in result if r.get("blind_check")}
    # blind_ids は stub_ids と disjoint
    assert blind_ids & stub_ids == set()
    # blind id 形式の検証
    assert all(bid.startswith("blind_") for bid in blind_ids)


def test_blind_ids_deterministic_across_calls():
    """同一 seed/pool なら blind id が安定 (resume 時の整合性)."""
    ha48_rows = [
        {"id": "q001", "O": "3"},
        {"id": "q002", "O": "4"},
        {"id": "q003", "O": "5"},
    ]
    r1 = ui_mod._inject_blind([], ha48_rows, n_blind=2, seed=42)
    r2 = ui_mod._inject_blind([], ha48_rows, n_blind=2, seed=42)
    ids1 = sorted(r["id"] for r in r1)
    ids2 = sorted(r["id"] for r in r2)
    assert ids1 == ids2


def test_resume_does_not_skip_pending_items(tmp_path, monkeypatch):
    """P1-1 回帰: resume 時に cursor_in_batch が保存位置と一致する.

    batch 内 index をそのまま使えば未アノテート行を飛ばさない。
    stub_rows を事前フィルタしないことで batch 構造が決定的に再現される。
    """
    stub = tmp_path / "stub.csv"
    out = tmp_path / "out.csv"
    rows = []
    for i in range(1, 8):  # 7 件の stub
        rows.append({
            "id": f"acc40_{i:03d}", "source": "v5_unannotated",
            "question_id": f"q{i:03d}", "question": "Q",
            "response": "R", "core_propositions": "[]",
            "O": "", "rater": "", "annotated_at": "",
            "comment": "", "blind_check": "", "hits_total": "",
        })
    _write_csv(
        stub, rows,
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total"],
    )
    monkeypatch.setattr(ui_mod, "ACC40_STUB", stub)
    monkeypatch.setattr(ui_mod, "ACC40_OUT", out)
    monkeypatch.setattr(ui_mod, "INCREMENTAL_CAL", Path("/nonexistent"))
    monkeypatch.setattr(ui_mod, "_load_ha48_for_blind", lambda: [])
    prog = tmp_path / "progress.json"
    monkeypatch.setattr(ui_mod, "PROGRESS_PATH", prog)

    # 1 件目 annotate → 2 件目で pause
    script = "a\nn\n5\nq\n"
    infile = io.StringIO(script)
    outfile = io.StringIO()
    rc = ui_mod.run_session(
        rater="t", batch_sizes=[5], blind_counts=[0],
        infile=infile, outfile=outfile, resume=False,
        seed=42, dry_run=False,
    )
    assert rc == 0
    # 出力に acc40_001 だけ annotate されている
    with open(out, encoding="utf-8") as f:
        done = [r for r in csv.DictReader(f) if r.get("O")]
    assert len(done) == 1
    assert done[0]["id"] == "acc40_001"

    # 保存された cursor=1 (2 件目でポーズ)
    with open(prog, encoding="utf-8") as f:
        saved_state = json.load(f)
    assert saved_state["batch_index"] == 0
    assert saved_state["cursor_in_batch"] == 1

    # resume: 2 件目 (acc40_002) から続き、すぐに pause
    script2 = "a\nn\n5\nq\n"
    infile = io.StringIO(script2)
    outfile = io.StringIO()
    rc = ui_mod.run_session(
        rater="t", batch_sizes=[5], blind_counts=[0],
        infile=infile, outfile=outfile, resume=True,
        seed=42, dry_run=False,
    )
    assert rc == 0
    # 2 件目 (acc40_002) が annotate された — skip されていないことを検証
    with open(out, encoding="utf-8") as f:
        done = [r for r in csv.DictReader(f) if r.get("O")]
    done_ids = {r["id"] for r in done}
    assert "acc40_001" in done_ids
    assert "acc40_002" in done_ids, (
        "resume 後に acc40_002 が skip されている (P1-1 回帰)"
    )
