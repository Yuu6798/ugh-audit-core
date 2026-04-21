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
            # q003: borderline under canonical squared ΔE
            # (2*(1-0.70)² + (1-0.60)²) / 3 = (0.18 + 0.16) / 3 ≈ 0.113
            {"id": "q003", "category": "y", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "2", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "0.70", "C": "0.60", "dE": "0", "decision": "rewrite"},
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


@pytest.fixture
def fake_data_with_polarity(tmp_path, monkeypatch):
    """focus option 検証用の sampler 入力データ."""
    ha48 = tmp_path / "ha48.csv"
    v5 = tmp_path / "v5.csv"
    qmeta = tmp_path / "qmeta.jsonl"
    resp = tmp_path / "resp.jsonl"
    out_stub = tmp_path / "stub_focus.csv"

    _write_csv(
        ha48,
        [],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        v5,
        [
            # accept (non-polarity)
            {"id": "q010", "category": "x", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "3", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "1.0", "C": "1.0", "dE": "0", "decision": "accept"},
            # accept (polarity-bearing)
            {"id": "q011", "category": "x", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "3", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "1.0", "C": "1.0", "dE": "0", "decision": "accept"},
            # borderline (near ΔE=0.10)
            {"id": "q012", "category": "y", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "2", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "0.70", "C": "0.60", "dE": "0", "decision": "rewrite"},
            # borderline (far from ΔE=0.10, but still <=0.15)
            {"id": "q013", "category": "y", "trap_type": "", "f1": "0",
             "f2": "0", "f3": "0", "f4": "0", "hits": "2", "total": "3",
             "hit_ids": "", "miss_ids": "", "hit_sources": "",
             "S": "0.65", "C": "0.55", "dE": "0", "decision": "rewrite"},
        ],
        ["id", "category", "trap_type", "f1", "f2", "f3", "f4", "hits",
         "total", "hit_ids", "miss_ids", "hit_sources", "S", "C", "dE",
         "decision"],
    )
    _write_jsonl(
        qmeta,
        [
            {"id": "q010", "question": "Q10?",
             "original_core_propositions": ["通常命題", "別命題"]},
            {"id": "q011", "question": "Q11?",
             "original_core_propositions": ["Xはすべきではない", "通常命題"]},
            {"id": "q012", "question": "Q12?",
             "original_core_propositions": ["通常命題A"]},
            {"id": "q013", "question": "Q13?",
             "original_core_propositions": ["通常命題B"]},
        ],
    )
    _write_jsonl(
        resp,
        [
            {"id": "q010", "response": "A10"},
            {"id": "q011", "response": "A11"},
            {"id": "q012", "response": "A12"},
            {"id": "q013", "response": "A13"},
        ],
    )

    monkeypatch.setattr(sampler_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(sampler_mod, "V5_PATH", v5)
    monkeypatch.setattr(sampler_mod, "QMETA_PATH", qmeta)
    monkeypatch.setattr(sampler_mod, "RESPONSES_PATH", resp)
    monkeypatch.setattr(sampler_mod, "OUT_CSV", out_stub)
    return {
        "ha48": ha48,
        "v5": v5,
        "qmeta": qmeta,
        "resp": resp,
        "stub": out_stub,
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


def test_sampler_polarity_focus_prioritizes_polarity_bearing(
    fake_data_with_polarity,
):
    assert sampler_mod.main(
        ["--batch-size", "10", "--seed", "7", "--polarity-focus"]
    ) == 0
    with open(fake_data_with_polarity["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["question_id"] == "q011"


def test_sampler_borderline_focus_prioritizes_borderline(fake_data_with_polarity):
    assert sampler_mod.main(
        ["--batch-size", "10", "--seed", "7", "--borderline-focus"]
    ) == 0
    with open(fake_data_with_polarity["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    first_four = [r["question_id"] for r in rows[:4]]
    # q012 (ΔE≈0.113) は q013 (ΔE≈0.149) より閾値 0.10 に近いので先頭
    assert first_four[:2] == ["q012", "q013"]
    # borderline を accept より前倒し
    assert first_four[2:] == ["q010", "q011"]


def test_sampler_combined_focus_uses_defined_priority(fake_data_with_polarity, tmp_path):
    gen = tmp_path / "gen.jsonl"
    _write_jsonl(
        gen,
        [
            {"question_id": "q900", "source": "orchestrator_claude",
             "question": "Q900?", "response": "A900",
             "core_propositions": ["Yはすべきではない"]},
        ],
    )
    assert sampler_mod.main(
        [
            "--batch-size", "10",
            "--seed", "7",
            "--polarity-focus",
            "--borderline-focus",
            "--orchestrator-jsonl", str(gen),
        ]
    ) == 0
    with open(fake_data_with_polarity["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["question_id"] for r in rows] == [
        "q011",  # polarity ∧ accept
        "q012",  # borderline (near)
        "q013",  # borderline (far)
        "q010",  # accept
        "q900",  # orchestrator_* (last)
    ]


def test_sampler_focus_flags_default_preserves_existing_order(fake_data, tmp_path):
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
        ["--batch-size", "5", "--seed", "3", "--orchestrator-jsonl", str(gen)]
    ) == 0
    with open(fake_data["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["source"] for r in rows] == [
        "v5_unannotated",
        "orchestrator_claude",
        "v5_borderline",
    ]


def test_sampler_default_does_not_call_polarity_counter(fake_data, monkeypatch):
    def _boom(_propositions):
        raise AssertionError("polarity counter must not run without --polarity-focus")

    monkeypatch.setattr(sampler_mod, "_count_polarity_bearing", _boom)
    assert sampler_mod.main(["--batch-size", "5"]) == 0
    with open(fake_data["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2


def test_sampler_polarity_focus_degrades_when_semantic_loss_unavailable(
    fake_data_with_polarity, monkeypatch
):
    import builtins

    original_import = builtins.__import__

    def _import_with_missing_semantic_loss(name, globals_=None, locals_=None,
                                           fromlist=(), level=0):
        if name == "semantic_loss":
            raise ModuleNotFoundError("No module named 'semantic_loss'")
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import_with_missing_semantic_loss)
    monkeypatch.setattr(sampler_mod, "_POLARITY_CHECKER", None)
    monkeypatch.setattr(sampler_mod, "_POLARITY_IMPORT_WARNED", False)

    assert sampler_mod.main(
        ["--batch-size", "10", "--seed", "7", "--polarity-focus"]
    ) == 0
    with open(fake_data_with_polarity["stub"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4


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
    result, _chosen = ui_mod._inject_blind(stub_chunk, ha48_rows, n_blind=2, seed=1)
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
    r1, _ = ui_mod._inject_blind([], ha48_rows, n_blind=2, seed=42)
    r2, _ = ui_mod._inject_blind([], ha48_rows, n_blind=2, seed=42)
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


# ---------------------------------------------------------------------------
# annotation_ui step mode tests
# ---------------------------------------------------------------------------


@pytest.fixture
def step_mode_env(tmp_path, monkeypatch):
    """step モード用の最小環境 (stub/out/progress を tmp 配下に固定)."""
    stub = tmp_path / "stub.csv"
    out = tmp_path / "annotation_accept40.csv"
    progress = tmp_path / "annotation_progress.json"
    _write_csv(
        stub,
        [
            {
                "id": "acc40_001", "source": "v5_unannotated",
                "question_id": "q001", "question": "Q1?",
                "response": "R1", "core_propositions": "[\"p1\"]",
                "O": "", "rater": "", "annotated_at": "",
                "comment": "", "blind_check": "", "hits_total": "1/1",
                "delta_e": "0.05",
            },
            {
                "id": "acc40_002", "source": "v5_unannotated",
                "question_id": "q002", "question": "Q2?",
                "response": "R2", "core_propositions": "[\"p2\"]",
                "O": "", "rater": "", "annotated_at": "",
                "comment": "", "blind_check": "", "hits_total": "1/1",
                "delta_e": "0.05",
            },
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    monkeypatch.setattr(ui_mod, "ACC40_STUB", stub)
    monkeypatch.setattr(ui_mod, "ACC40_OUT", out)
    monkeypatch.setattr(ui_mod, "INCREMENTAL_CAL", tmp_path / "no_call.py")
    monkeypatch.setattr(ui_mod, "PROGRESS_PATH", progress)
    monkeypatch.setattr(ui_mod, "_load_ha48_for_blind", lambda: [])
    monkeypatch.delenv("UGH_ANNOTATION_PROGRESS_PATH", raising=False)
    return {"stub": stub, "out": out, "progress": progress}


def _seed_step_progress(progress_path: Path) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(
        json.dumps(
            {
                "rater": "user",
                "batch_index": 0,
                "cursor_in_batch": 0,
                "completed_ids": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_progress(progress_path: Path) -> dict:
    return json.loads(progress_path.read_text(encoding="utf-8"))


def test_step_next_shows_current_without_mutation(step_mode_env, capsys):
    _seed_step_progress(step_mode_env["progress"])
    before = _read_progress(step_mode_env["progress"])

    rc = ui_mod.main([
        "--resume",
        "--batch-size", "2",
        "--blind-count", "0",
        "--step-next",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "[next]" in out
    assert "[ID: acc40_001]" in out
    assert not step_mode_env["out"].exists()
    after = _read_progress(step_mode_env["progress"])
    assert after["cursor_in_batch"] == before["cursor_in_batch"]
    assert after["batch_index"] == before["batch_index"]


def test_step_annotate_advances_cursor_and_writes_csv(step_mode_env, capsys):
    _seed_step_progress(step_mode_env["progress"])

    rc = ui_mod.main([
        "--resume",
        "--batch-size", "2",
        "--blind-count", "0",
        "--step-annotate",
        "--item-id", "acc40_001",
        "--q1", "a",
        "--q2", "n",
        "--comment-key", "5",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "[saved] id=acc40_001" in out
    assert "[next-id] acc40_002" in out

    assert step_mode_env["out"].exists()
    with open(step_mode_env["out"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    row = next(r for r in rows if r["id"] == "acc40_001")
    assert row["O"] == "5"
    assert row["comment"] == "完璧"

    after = _read_progress(step_mode_env["progress"])
    assert after["cursor_in_batch"] == 1
    assert after["batch_index"] == 0


def test_step_annotate_dry_run_does_not_write_or_persist_cursor(
    step_mode_env, capsys
):
    _seed_step_progress(step_mode_env["progress"])
    before = _read_progress(step_mode_env["progress"])

    rc = ui_mod.main([
        "--resume",
        "--dry-run",
        "--batch-size", "2",
        "--blind-count", "0",
        "--step-annotate",
        "--item-id", "acc40_001",
        "--q1", "a",
        "--q2", "n",
        "--comment-key", "5",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "[saved] id=acc40_001" in out
    assert "[dry-run] progress not persisted." in out
    assert not step_mode_env["out"].exists()
    after = _read_progress(step_mode_env["progress"])
    assert after["cursor_in_batch"] == before["cursor_in_batch"]
    assert after["batch_index"] == before["batch_index"]


def test_step_skip_advances_without_writing(step_mode_env, capsys):
    _seed_step_progress(step_mode_env["progress"])

    rc = ui_mod.main([
        "--resume",
        "--batch-size", "2",
        "--blind-count", "0",
        "--step-skip",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "[skip] acc40_001" in out
    assert not step_mode_env["out"].exists()
    after = _read_progress(step_mode_env["progress"])
    assert after["cursor_in_batch"] == 1
    assert after["batch_index"] == 0


def test_step_item_id_mismatch_returns_error(step_mode_env, capsys):
    _seed_step_progress(step_mode_env["progress"])
    before = _read_progress(step_mode_env["progress"])

    rc = ui_mod.main([
        "--resume",
        "--batch-size", "2",
        "--blind-count", "0",
        "--step-annotate",
        "--item-id", "acc40_999",
        "--q1", "a",
        "--q2", "n",
        "--comment-key", "5",
    ])
    out = capsys.readouterr().out

    assert rc == 2
    assert "[error] current item id mismatch" in out
    assert not step_mode_env["out"].exists()
    after = _read_progress(step_mode_env["progress"])
    assert after == before


def test_step_override_o_skips_decision_tree(step_mode_env):
    _seed_step_progress(step_mode_env["progress"])
    rc = ui_mod.main([
        "--resume",
        "--batch-size", "2",
        "--blind-count", "0",
        "--step-annotate",
        "--item-id", "acc40_001",
        "--o", "3",
        "--comment-key", "5",
    ])
    assert rc == 0
    with open(step_mode_env["out"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    row = next(r for r in rows if r["id"] == "acc40_001")
    assert row["O"] == "3"
    assert row["comment"] == "完璧"


def test_step_custom_comment_with_detail(step_mode_env):
    _seed_step_progress(step_mode_env["progress"])
    rc = ui_mod.main([
        "--resume",
        "--batch-size", "2",
        "--blind-count", "0",
        "--step-annotate",
        "--item-id", "acc40_001",
        "--q1", "a",
        "--q2", "n",
        "--comment-key", "6",
        "--comment-detail", "カスタム理由",
    ])
    assert rc == 0
    with open(step_mode_env["out"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    row = next(r for r in rows if r["id"] == "acc40_001")
    assert row["comment"] == "カスタム: カスタム理由"


# ---------------------------------------------------------------------------
# Codex PR #85 追加レビュー回帰テスト
# ---------------------------------------------------------------------------


def test_sampler_append_excludes_existing_question_ids(fake_data):
    """P1 回帰: --append で既存 question_id を候補から除外する."""
    # 1 回目: 1 件サンプリング
    assert sampler_mod.main(["--batch-size", "1", "--seed", "1"]) == 0
    with open(fake_data["stub"], encoding="utf-8") as f:
        first = list(csv.DictReader(f))
    assert len(first) == 1
    first_qid = first[0]["question_id"]

    # 2 回目: --append で残り全部。first_qid は重複してはならない
    assert sampler_mod.main(
        ["--batch-size", "5", "--append", "--seed", "1"]
    ) == 0
    with open(fake_data["stub"], encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    qids = [r["question_id"] for r in all_rows]
    assert qids.count(first_qid) == 1, (
        f"--append で {first_qid} が重複している (P1 回帰): {qids}"
    )
    # 新規 id が assigned されている (acc40_002 以降)
    new_ids = [r["id"] for r in all_rows[1:]]
    assert all(nid != "acc40_001" for nid in new_ids)


def test_ui_back_navigation_revises_prior_annotation(tmp_path, monkeypatch):
    """P2 回帰: `r` キーで前件に戻って再 annotate できる."""
    stub = tmp_path / "stub.csv"
    out = tmp_path / "out.csv"
    rows = []
    for i in range(1, 4):
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
    monkeypatch.setattr(ui_mod, "PROGRESS_PATH", tmp_path / "progress.json")

    # 1件目: a,n,5 → O=5
    # 2件目: r で戻る (1件目を訂正するため)
    # 1件目を再評価: b,y,3 → O=2
    # 2件目: a,n,5 → O=5
    # 3件目: q で pause
    script = (
        "a\nn\n5\n"        # item1 O=5
        "r\n"               # back
        "b\ny\n3\n"         # item1 再 annotate → O=2
        "a\nn\n5\n"         # item2 O=5
        "q\n"               # pause
    )
    infile = io.StringIO(script)
    outfile = io.StringIO()
    rc = ui_mod.run_session(
        rater="t", batch_sizes=[3], blind_counts=[0],
        infile=infile, outfile=outfile, resume=False,
        seed=42, dry_run=False,
    )
    assert rc == 0
    with open(out, encoding="utf-8") as f:
        done = {r["id"]: r for r in csv.DictReader(f) if r.get("O")}
    # item1 は再 annotate で O=2 に上書きされているべき
    assert done["acc40_001"]["O"] == "2", (
        f"`r` で戻っても item1 が再評価されていない (P2 回帰): {done['acc40_001']}"
    )
    # item2 も annotate 済
    assert "acc40_002" in done


def test_ui_incremental_cal_does_not_pass_no_run_full(monkeypatch):
    """P2 回帰: batch 境界で --no-run-full を渡さない (STOP 判定を可能に)."""
    captured_args: List[List[str]] = []

    def fake_run(args, **kwargs):
        captured_args.append(list(args))
        class R:
            returncode = 0
        return R()

    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(ui_mod, "INCREMENTAL_CAL", Path(__file__))  # exists

    ui_mod._call_incremental_cal(Path("/tmp/fake.csv"))

    assert captured_args, "subprocess.run が呼ばれていない"
    args = captured_args[0]
    assert "--no-run-full" not in args, (
        f"--no-run-full が渡っている (P2 回帰): {args}"
    )
    assert "--acc40" in args
    acc40_idx = args.index("--acc40")
    assert Path(args[acc40_idx + 1]) == Path("/tmp/fake.csv")


# ---------------------------------------------------------------------------
# Codex PR #85 第 3 ラウンド回帰テスト
# ---------------------------------------------------------------------------


def test_run_incremental_cal_passes_ha48_path_override(tmp_path, monkeypatch):
    """P1 回帰: full-run 時に calibrate に --ha48-path を渡す."""
    import analysis.run_incremental_calibration as rc_mod

    acc40 = tmp_path / "acc40.csv"
    merged = tmp_path / "merged.csv"
    ha48_src = tmp_path / "ha48.csv"
    _write_csv(
        ha48_src,
        [{"id": "q001", "category": "x", "S": "4", "C": "4", "O": "5",
          "propositions_hit": "3/3", "notes": ""}],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        acc40,
        [{"id": "acc40_001", "source": "v5_unannotated",
          "question_id": "q020", "question": "q", "response": "r",
          "core_propositions": "[]", "O": "5", "rater": "t",
          "annotated_at": "", "comment": "", "blind_check": "",
          "hits_total": "", "delta_e": "0.05"}],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    monkeypatch.setattr(rc_mod, "HA48_SRC_PATH", ha48_src)
    monkeypatch.setattr(rc_mod, "ACCEPT_SUBSET_TARGET", 1)
    monkeypatch.setattr(rc_mod, "MERGED_FOR_CAL", merged)
    monkeypatch.setattr(rc_mod, "ACC40_DEFAULT", acc40)
    monkeypatch.setattr(rc_mod, "CAL_SCRIPT", tmp_path / "fake_cal.py")
    # CAL_SCRIPT が exists 扱いになるよう空ファイル作成
    (tmp_path / "fake_cal.py").write_text("")

    captured: List[List[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _Result()

    monkeypatch.setattr(rc_mod.subprocess, "run", fake_run)
    # filter_accept_subset が v5 を読めるよう merge_mod 側 fixture を流用
    monkeypatch.setattr(merge_mod, "HA48_PATH", ha48_src)
    # v5 に q020 を含める
    v5 = tmp_path / "v5.csv"
    _write_csv(
        v5,
        [{"id": "q020", "category": "x", "trap_type": "", "f1": "0",
          "f2": "0", "f3": "0", "f4": "0", "hits": "3", "total": "3",
          "hit_ids": "", "miss_ids": "", "hit_sources": "",
          "S": "1.0", "C": "1.0", "dE": "0", "decision": "accept"}],
        ["id", "category", "trap_type", "f1", "f2", "f3", "f4", "hits",
         "total", "hit_ids", "miss_ids", "hit_sources", "S", "C", "dE",
         "decision"],
    )
    monkeypatch.setattr(merge_mod, "V5_PATH", v5)

    rc = rc_mod.main(["--acc40", str(acc40)])
    assert rc == 0
    assert captured, "subprocess.run が呼ばれていない"
    cmd = captured[0]
    assert "--ha48-path" in cmd, f"--ha48-path が渡されていない: {cmd}"
    idx = cmd.index("--ha48-path")
    assert Path(cmd[idx + 1]) == merged, (
        f"ha48-path が MERGED_FOR_CAL でない: {cmd[idx + 1]}"
    )


def test_build_merged_for_calibration_remaps_question_id(tmp_path, monkeypatch):
    """acc40 priority A 行で id が question_id に置き換わる."""
    import analysis.run_incremental_calibration as rc_mod

    acc40 = tmp_path / "acc40.csv"
    merged = tmp_path / "merged.csv"
    ha48_src = tmp_path / "ha48.csv"
    _write_csv(
        ha48_src,
        [{"id": "q001", "category": "x", "S": "4", "C": "4", "O": "5",
          "propositions_hit": "3/3", "notes": ""}],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        acc40,
        [
            {"id": "acc40_001", "source": "v5_unannotated",
             "question_id": "q020", "question": "q", "response": "r",
             "core_propositions": "[]", "O": "5", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": "", "delta_e": "0.05"},
            {"id": "acc40_002", "source": "orchestrator_claude",
             "question_id": "q077", "question": "q", "response": "r",
             "core_propositions": "[]", "O": "4", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": "", "delta_e": "0.08"},
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    monkeypatch.setattr(rc_mod, "HA48_SRC_PATH", ha48_src)

    stats = rc_mod.build_merged_for_calibration(acc40, merged)
    assert stats["ha48"] == 1
    assert stats["acc40_accept"] == 1  # v5_unannotated のみ
    assert stats["orchestrator_excluded"] == 1

    with open(merged, encoding="utf-8") as f:
        out_rows = list(csv.DictReader(f))
    ids = {r["id"] for r in out_rows}
    assert "q001" in ids  # HA48
    assert "q020" in ids  # acc40 priority A が question_id にリマップされている
    assert "acc40_001" not in ids  # acc40_NNN 形式はリマップ後に残らない
    assert "q077" not in ids  # orchestrator は除外


def test_ui_pads_blind_counts_shorter(tmp_path, monkeypatch):
    """P2 回帰: blind_counts が短くても後続 batch が drop されない."""
    stub = tmp_path / "stub.csv"
    out = tmp_path / "out.csv"
    rows = []
    for i in range(1, 6):
        rows.append({
            "id": f"acc40_{i:03d}", "source": "v5_unannotated",
            "question_id": f"q{i:03d}", "question": "Q",
            "response": "R", "core_propositions": "[]",
            "O": "", "rater": "", "annotated_at": "",
            "comment": "", "blind_check": "", "hits_total": "",
            "delta_e": "0.05",
        })
    _write_csv(
        stub, rows,
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    monkeypatch.setattr(ui_mod, "ACC40_STUB", stub)
    monkeypatch.setattr(ui_mod, "ACC40_OUT", out)
    monkeypatch.setattr(ui_mod, "INCREMENTAL_CAL", Path("/nonexistent"))
    monkeypatch.setattr(ui_mod, "_load_ha48_for_blind", lambda: [])
    monkeypatch.setattr(ui_mod, "PROGRESS_PATH", tmp_path / "progress.json")

    # 2 batches (3+2) と blind_counts=[0] (短い, pad される想定)
    # 3 件目で pause して 2 batch 目が存在することを間接的に確認
    script = (
        "a\nn\n5\n"  # item1
        "a\nn\n5\n"  # item2
        "a\nn\n5\n"  # item3
        "n\n"        # batch 1 終了後 "次の batch へ" 問いに n → 終了
    )
    infile = io.StringIO(script)
    outfile = io.StringIO()
    rc = ui_mod.run_session(
        rater="t", batch_sizes=[3, 2], blind_counts=[0],
        infile=infile, outfile=outfile, resume=False,
        seed=42, dry_run=False,
    )
    assert rc == 0
    out_text = outfile.getvalue()
    # "Batch 1/2" が出力されていれば total 2 batches 構築済 → padding 成功
    assert "Batch 1/2" in out_text, (
        f"batch 2 が生成されていない (P2 回帰):\n{out_text[-500:]}"
    )
    assert "[warn]" in out_text  # padding 警告が出ている


def test_merge_orchestrator_uses_own_delta_e(tmp_path, monkeypatch):
    """P2 回帰: orchestrator 行は自身の delta_e で accept 判定."""
    ha48 = tmp_path / "ha48.csv"
    v5 = tmp_path / "v5.csv"
    acc40 = tmp_path / "acc40.csv"
    # v5 は q050 を accept にしない (ΔE > 0.10 相当)
    _write_csv(
        ha48,
        [{"id": "q001", "category": "x", "S": "4", "C": "4", "O": "5",
          "propositions_hit": "3/3", "notes": ""}],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        v5,
        [{"id": "q050", "category": "x", "trap_type": "", "f1": "0",
          "f2": "0", "f3": "0", "f4": "0", "hits": "1", "total": "3",
          "hit_ids": "", "miss_ids": "", "hit_sources": "",
          "S": "0.5", "C": "0.3", "dE": "0", "decision": "regenerate"}],
        ["id", "category", "trap_type", "f1", "f2", "f3", "f4", "hits",
         "total", "hit_ids", "miss_ids", "hit_sources", "S", "C", "dE",
         "decision"],
    )
    _write_csv(
        acc40,
        [
            # orchestrator 行: 自分の delta_e 0.05 (accept 相当) → 含める
            {"id": "acc40_001", "source": "orchestrator_claude",
             "question_id": "q050", "question": "q", "response": "r",
             "core_propositions": "[]", "O": "5", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": "", "delta_e": "0.05"},
            # orchestrator 行: 自分の delta_e 0.20 (accept 外) → 除外
            {"id": "acc40_002", "source": "orchestrator_gpt4o",
             "question_id": "q050", "question": "q", "response": "r",
             "core_propositions": "[]", "O": "4", "rater": "t",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": "", "delta_e": "0.20"},
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    monkeypatch.setattr(merge_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(merge_mod, "V5_PATH", v5)

    combined = merge_mod.load_ha48() + merge_mod.load_acc40(acc40)
    subset = merge_mod.filter_accept_subset(combined)
    ids = {r["id"] for r in subset}
    assert "acc40_001" in ids, (
        "delta_e ≤ 0.10 の orchestrator 行が accept subset に含まれない (P2 回帰)"
    )
    assert "acc40_002" not in ids, (
        "delta_e > 0.10 の orchestrator 行が accept subset に含まれている (P2 回帰)"
    )


# ---------------------------------------------------------------------------
# Codex PR #85 第 4 ラウンド回帰テスト (canonical ΔE 揃え)
# ---------------------------------------------------------------------------


def test_sampler_uses_canonical_squared_delta_e(tmp_path, monkeypatch):
    """P1 回帰: sampler は canonical (squared) ΔE で accept/borderline を決める.

    S=0.75, C=0.75 は:
      - 線形式: ΔE = (2*0.25 + 0.25)/3 = 0.25 → pool から除外
      - squared: ΔE = (2*0.0625 + 0.0625)/3 = 0.0625 → accept
    """
    ha48 = tmp_path / "ha48.csv"
    v5 = tmp_path / "v5.csv"
    qmeta = tmp_path / "qmeta.jsonl"
    resp = tmp_path / "resp.jsonl"
    stub = tmp_path / "stub.csv"
    _write_csv(
        ha48, [],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        v5,
        [{"id": "qX", "category": "x", "trap_type": "",
          "f1": "0", "f2": "0", "f3": "0", "f4": "0",
          "hits": "2", "total": "3", "hit_ids": "", "miss_ids": "",
          "hit_sources": "", "S": "0.75", "C": "0.75", "dE": "0",
          "decision": "rewrite"}],
        ["id", "category", "trap_type", "f1", "f2", "f3", "f4", "hits",
         "total", "hit_ids", "miss_ids", "hit_sources", "S", "C", "dE",
         "decision"],
    )
    _write_jsonl(
        qmeta,
        [{"id": "qX", "category": "x", "question": "Q",
          "original_core_propositions": ["p1", "p2"]}],
    )
    _write_jsonl(resp, [{"id": "qX", "response": "A"}])

    monkeypatch.setattr(sampler_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(sampler_mod, "V5_PATH", v5)
    monkeypatch.setattr(sampler_mod, "QMETA_PATH", qmeta)
    monkeypatch.setattr(sampler_mod, "RESPONSES_PATH", resp)
    monkeypatch.setattr(sampler_mod, "OUT_CSV", stub)

    assert sampler_mod.main(["--batch-size", "5"]) == 0
    with open(stub, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1, (
        f"S=C=0.75 が squared ΔE で accept 候補として含まれない (P1 回帰): {rows}"
    )
    assert rows[0]["source"] == "v5_unannotated"
    # ΔE は squared で ≈ 0.0625 になっているはず (線形なら 0.25)
    de = float(rows[0]["delta_e"])
    assert de < 0.10, (
        f"delta_e が canonical squared 値ではない: {de}"
    )


def test_merge_accept_subset_uses_canonical_squared(tmp_path, monkeypatch):
    """P1 回帰: merge の _v5_accept_ids が canonical squared ΔE を使う."""
    ha48 = tmp_path / "ha48.csv"
    v5 = tmp_path / "v5.csv"
    _write_csv(
        ha48,
        [{"id": "qX", "category": "x", "S": "3", "C": "4", "O": "4",
          "propositions_hit": "2/3", "notes": ""}],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        v5,
        [{"id": "qX", "category": "x", "trap_type": "",
          "f1": "0", "f2": "0", "f3": "0", "f4": "0",
          "hits": "2", "total": "3", "hit_ids": "", "miss_ids": "",
          "hit_sources": "", "S": "0.75", "C": "0.75", "dE": "0",
          "decision": "rewrite"}],
        ["id", "category", "trap_type", "f1", "f2", "f3", "f4", "hits",
         "total", "hit_ids", "miss_ids", "hit_sources", "S", "C", "dE",
         "decision"],
    )
    monkeypatch.setattr(merge_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(merge_mod, "V5_PATH", v5)

    combined = merge_mod.load_ha48()
    subset = merge_mod.filter_accept_subset(combined)
    ids = {r["id"] for r in subset}
    assert "qX" in ids, (
        "S=C=0.75 の HA48 行が accept subset に含まれない (P1 回帰、"
        "線形 ΔE では 0.25 で落とされる)"
    )


# ---------------------------------------------------------------------------
# Codex PR #85 第 5 ラウンド回帰テスト
# ---------------------------------------------------------------------------


def test_current_accept_subset_excludes_orchestrator(tmp_path, monkeypatch):
    """P1 回帰: orchestrator 行は gate 計算から除外される.

    build_merged_for_calibration が orchestrator を外すのに合わせて、
    current_accept_subset_size もカウント対象から外す。両者が乖離すると
    premature STOP が出る。
    """
    import analysis.run_incremental_calibration as rc_mod

    ha48 = tmp_path / "ha48.csv"
    v5 = tmp_path / "v5.csv"
    acc40 = tmp_path / "acc40.csv"
    _write_csv(
        ha48,
        [{"id": "qA", "category": "x", "S": "5", "C": "5", "O": "5",
          "propositions_hit": "3/3", "notes": ""}],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_csv(
        v5,
        [{"id": "qA", "category": "x", "trap_type": "",
          "f1": "0", "f2": "0", "f3": "0", "f4": "0",
          "hits": "3", "total": "3", "hit_ids": "", "miss_ids": "",
          "hit_sources": "", "S": "1.0", "C": "1.0", "dE": "0",
          "decision": "accept"}],
        ["id", "category", "trap_type", "f1", "f2", "f3", "f4", "hits",
         "total", "hit_ids", "miss_ids", "hit_sources", "S", "C", "dE",
         "decision"],
    )
    _write_csv(
        acc40,
        [
            # orchestrator 行 (自分の delta_e で accept 判定されうる)
            {"id": "acc40_001", "source": "orchestrator_claude",
             "question_id": "qZ", "question": "", "response": "",
             "core_propositions": "", "O": "5", "rater": "",
             "annotated_at": "", "comment": "", "blind_check": "",
             "hits_total": "", "delta_e": "0.05"},
        ],
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    monkeypatch.setattr(merge_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(merge_mod, "V5_PATH", v5)

    n = rc_mod.current_accept_subset_size(acc40)
    # HA48 qA (accept) のみ含まれる。orchestrator 行は除外されるので +0
    assert n == 1, (
        f"orchestrator 行が gate に加算されている (P1 回帰): n={n}"
    )


def test_blind_injection_populates_real_content(tmp_path, monkeypatch):
    """P2 回帰: blind 行は placeholder ではなく実コンテンツを持つ."""
    ha48 = tmp_path / "ha48.csv"
    qmeta = tmp_path / "qmeta.jsonl"
    resp = tmp_path / "resp.jsonl"
    _write_csv(
        ha48,
        [{"id": "q001", "category": "x", "S": "3", "C": "3", "O": "4",
          "propositions_hit": "2/3", "notes": ""}],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_jsonl(
        qmeta,
        [{"id": "q001", "category": "x", "question": "実際の質問文?",
          "original_core_propositions": ["命題1", "命題2"]}],
    )
    _write_jsonl(resp, [{"id": "q001", "response": "実際の回答文"}])

    monkeypatch.setattr(ui_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(ui_mod, "QMETA_PATH", qmeta)
    monkeypatch.setattr(ui_mod, "RESPONSES_PATH", resp)

    blind_pool = ui_mod._load_ha48_for_blind()
    assert len(blind_pool) == 1
    enriched = blind_pool[0]
    assert enriched["question"] == "実際の質問文?"
    assert enriched["response"] == "実際の回答文"
    # core_propositions は JSON 文字列で渡される
    assert json.loads(enriched["core_propositions"]) == ["命題1", "命題2"]

    # _inject_blind が enrichment 結果を blind 行に引き継ぐ
    injected, _chosen = ui_mod._inject_blind([], blind_pool, n_blind=1, seed=1)
    assert len(injected) == 1
    blind_row = injected[0]
    assert blind_row["id"] == "blind_q001"
    assert "実際の質問文" in blind_row["question"]
    assert "実際の回答文" in blind_row["response"]
    assert "placeholder" not in blind_row["question"].lower()
    assert "参照" not in blind_row["question"]  # 旧 placeholder 文言


def test_blind_injection_drops_rows_without_enrichment(tmp_path, monkeypatch):
    """元 response / qmeta がない HA48 id は blind 候補から除外."""
    ha48 = tmp_path / "ha48.csv"
    qmeta = tmp_path / "qmeta.jsonl"
    resp = tmp_path / "resp.jsonl"
    _write_csv(
        ha48,
        [
            {"id": "q001", "category": "x", "S": "3", "C": "3", "O": "4",
             "propositions_hit": "2/3", "notes": ""},
            # q999 は qmeta / resp に存在しない
            {"id": "q999", "category": "x", "S": "3", "C": "3", "O": "4",
             "propositions_hit": "2/3", "notes": ""},
        ],
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_jsonl(
        qmeta,
        [{"id": "q001", "category": "x", "question": "Q",
          "original_core_propositions": ["p"]}],
    )
    _write_jsonl(resp, [{"id": "q001", "response": "A"}])

    monkeypatch.setattr(ui_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(ui_mod, "QMETA_PATH", qmeta)
    monkeypatch.setattr(ui_mod, "RESPONSES_PATH", resp)

    blind_pool = ui_mod._load_ha48_for_blind()
    ids = {r["id"] for r in blind_pool}
    assert "q001" in ids
    assert "q999" not in ids, (
        "enrichment 不能な HA48 id が blind 候補に残っている"
    )


# ---------------------------------------------------------------------------
# Codex PR #85 第 6 ラウンド回帰テスト
# ---------------------------------------------------------------------------


def test_ui_appends_tail_batch_when_stub_exceeds_batch_sizes(tmp_path, monkeypatch):
    """P2 回帰: stub が sum(batch_sizes) を超えても末尾行が drop されない."""
    stub = tmp_path / "stub.csv"
    out = tmp_path / "out.csv"
    # 5 件の stub、 batch_sizes=[2] (sum=2) だと残り 3 件が drop される問題を検証
    rows = []
    for i in range(1, 6):
        rows.append({
            "id": f"acc40_{i:03d}", "source": "v5_unannotated",
            "question_id": f"q{i:03d}", "question": "Q",
            "response": "R", "core_propositions": "[]",
            "O": "", "rater": "", "annotated_at": "",
            "comment": "", "blind_check": "", "hits_total": "",
            "delta_e": "0.05",
        })
    _write_csv(
        stub, rows,
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    monkeypatch.setattr(ui_mod, "ACC40_STUB", stub)
    monkeypatch.setattr(ui_mod, "ACC40_OUT", out)
    monkeypatch.setattr(ui_mod, "INCREMENTAL_CAL", Path("/nonexistent"))
    monkeypatch.setattr(ui_mod, "_load_ha48_for_blind", lambda: [])
    monkeypatch.setattr(ui_mod, "PROGRESS_PATH", tmp_path / "progress.json")

    # batch_sizes=[2] (sum=2) だが stub は 5 件。3 件の tail が作られる想定。
    # 1件目 annotate → pause
    script = "a\nn\n5\nq\n"
    infile = io.StringIO(script)
    outfile = io.StringIO()
    rc = ui_mod.run_session(
        rater="t", batch_sizes=[2], blind_counts=[0],
        infile=infile, outfile=outfile, resume=False,
        seed=42, dry_run=False,
    )
    assert rc == 0
    out_text = outfile.getvalue()
    # tail batch が作られている: 2 batch (1 + tail)
    assert "Batch 1/2" in out_text, (
        f"tail batch が作られていない (P2 回帰):\n{out_text[-500:]}"
    )
    assert "[warn]" in out_text and "末尾 batch" in out_text


def test_blind_does_not_repeat_orig_id_across_batches(tmp_path, monkeypatch):
    """P2 回帰: 複数 batch で同じ HA48 行を抽選しない (id 衝突 → skip 防止).

    batch 数 * blind_count > HA48 pool size でなければ重複は起きない、という
    保証を run_session レベルで確かめる。pool 3 件から 2 batch × 2 blind
    (=4 件) を要求する設定で、全 HA48 pool が消費されるまで重複しないか。
    """
    stub = tmp_path / "stub.csv"
    out = tmp_path / "out.csv"
    ha48 = tmp_path / "ha48.csv"
    qmeta = tmp_path / "qmeta.jsonl"
    resp = tmp_path / "resp.jsonl"
    rows = []
    for i in range(1, 5):
        rows.append({
            "id": f"acc40_{i:03d}", "source": "v5_unannotated",
            "question_id": f"q{i:03d}", "question": "Q",
            "response": "R", "core_propositions": "[]",
            "O": "", "rater": "", "annotated_at": "",
            "comment": "", "blind_check": "", "hits_total": "",
            "delta_e": "0.05",
        })
    _write_csv(
        stub, rows,
        ["id", "source", "question_id", "question", "response",
         "core_propositions", "O", "rater", "annotated_at", "comment",
         "blind_check", "hits_total", "delta_e"],
    )
    # HA48 は 3 件のみ
    ha48_rows = [
        {"id": f"hq{i:03d}", "category": "x", "S": "3", "C": "3",
         "O": "4", "propositions_hit": "2/3", "notes": ""}
        for i in range(1, 4)
    ]
    _write_csv(
        ha48, ha48_rows,
        ["id", "category", "S", "C", "O", "propositions_hit", "notes"],
    )
    _write_jsonl(
        qmeta,
        [{"id": r["id"], "question": "HA question",
          "original_core_propositions": ["p"]} for r in ha48_rows],
    )
    _write_jsonl(
        resp,
        [{"id": r["id"], "response": "HA response"} for r in ha48_rows],
    )

    monkeypatch.setattr(ui_mod, "ACC40_STUB", stub)
    monkeypatch.setattr(ui_mod, "ACC40_OUT", out)
    monkeypatch.setattr(ui_mod, "HA48_PATH", ha48)
    monkeypatch.setattr(ui_mod, "QMETA_PATH", qmeta)
    monkeypatch.setattr(ui_mod, "RESPONSES_PATH", resp)
    monkeypatch.setattr(ui_mod, "INCREMENTAL_CAL", Path("/nonexistent"))
    monkeypatch.setattr(ui_mod, "PROGRESS_PATH", tmp_path / "progress.json")

    # batch_sizes=[2, 2], blind_counts=[2, 2] → 合計 blind 要求 4 件だが
    # HA48 pool は 3 件。orig_id 重複禁止なので 2 batch 目は最大 1 件しか
    # blind を得られない (重複回避で pool 枯渇)。
    # pause ですぐ抜ける。batches が構築された時点で検証できる。
    infile = io.StringIO("q\n")
    outfile = io.StringIO()
    rc = ui_mod.run_session(
        rater="t", batch_sizes=[2, 2], blind_counts=[2, 2],
        infile=infile, outfile=outfile, resume=False,
        seed=42, dry_run=False,
    )
    assert rc == 0
    # blind id が重複していないことを確認するため直接 _inject_blind を叩く
    pool = ui_mod._load_ha48_for_blind()
    _, chosen1 = ui_mod._inject_blind([], pool, n_blind=2, seed=1)
    _, chosen2 = ui_mod._inject_blind(
        [], pool, n_blind=2, seed=2, exclude_orig_ids=chosen1
    )
    assert chosen1 & chosen2 == set(), (
        "blind 抽選で batch 間の orig_id 重複が発生 (P2 回帰)"
    )
    # pool 枯渇で 2 batch 目は max 1 件 (pool=3 - 2件消費 = 1 残り)
    assert len(chosen2) <= 1
