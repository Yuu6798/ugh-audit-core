"""tests/test_ha48_regression.py — HA48 audit 出力の回帰テスト

`analysis/ha48_regression_check.csv` を "期待値スナップショット" として、
HA48 48 件全件に対し `detect → calculate` を走らせ、S / C / ΔE /
quality_score / verdict / f1-f4 / hits / total が一致することを検証する。

目的: 論文で報告する HA48 ρ=-0.5195 (ΔE vs O, system C) を支える pipeline
出力が将来の変更で壊れないことを CI で保証する。**論文の数字が CI で
守られる** reproducibility guard。

運用:
- SBert (sentence-transformers) が必要。cascade rescued の C が再現できる
  ため、未導入環境では自動 skip
- .github/workflows/ci-weekly.yml で週次実行される (ci.yml の fast-path
  では skipif で skip される)
- 期待値 CSV は `analysis/ha48_regression_check.csv`。pipeline に意図的な
  変更を加える場合は、CSV 更新コミットを同 PR で必ず添える
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# SBert がないと cascade rescued 分の C が再現できず、保存値と乖離するため skip
pytest.importorskip("sentence_transformers")

CSV_PATH = ROOT / "analysis" / "ha48_regression_check.csv"
RESPONSES_PATH = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"
Q_META_PATH = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.jsonl"

# 浮動小数の比較許容誤差。CSV は 4 桁丸めで保存されているため
# round-trip の誤差を吸収する。
_ABS_TOL = 1e-3

# 論文 "HA48" の canonical cardinality。accidental truncation を silent に
# 見逃さないよう exact equality で assert する。データセット拡張時は本定数と
# CSV 両方を同一コミットで更新する運用。
EXPECTED_HA48_N = 48


def _load_csv() -> dict:
    result: dict = {}
    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["id"]] = row
    return result


def _load_jsonl(path: Path) -> dict:
    result: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            result[rec["id"]] = rec
    return result


# --- fixtures ---


@pytest.fixture(scope="module")
def expected_rows() -> dict:
    return _load_csv()


@pytest.fixture(scope="module")
def responses() -> dict:
    return _load_jsonl(RESPONSES_PATH)


@pytest.fixture(scope="module")
def q_meta() -> dict:
    return _load_jsonl(Q_META_PATH)


# --- sentinel: parametrize が no-op 化していないことを独立保証する ---


def test_ha48_snapshot_csv_is_loadable_and_has_expected_size() -> None:
    """HA48 snapshot CSV が読めて **canonical 48 件と exact 一致** する。

    Codex review P2 対応の二重防御 (round 2):
    - parametrize が future refactor で silent no-op 化しないよう、非
      parametrize な test で CSV 読み込みを独立に assert する
    - **exact equality** (== EXPECTED_HA48_N) で accidental truncation を
      silent に見逃さない。前版 `>= 40` は最大 8 行 truncation を pass
      させる guard 抜けがあった (Codex review P2 round 2)

    運用: HA48 拡張 (accept40 合流 → HA63 など) を行う場合は、`EXPECTED_HA48_N`
    定数と CSV 両方を同一コミットで更新する。
    """
    ids = _csv_ids()
    # Exact cardinality: 論文の "HA48" claim を row-for-row に lock する
    assert len(ids) == EXPECTED_HA48_N, (
        f"HA48 snapshot row count mismatch: expected exactly {EXPECTED_HA48_N} "
        f"(paper-facing HA48 claim), got {len(ids)}. Accidental truncation or "
        f"undeclared dataset expansion breaks reproducibility guard."
    )
    # duplicate id がないこと
    assert len(set(ids)) == len(ids), "duplicate ids in HA48 snapshot CSV"


# --- parametrize で 48 件個別に test 化（drift 発生時に qid が pinpoint される）---


def _csv_ids() -> list:
    """CSV から id リストを module import 時に取得する。parametrize 用。

    Codex review P2 (PR #104): 読み込み失敗時に空 list を返すと
    `pytest.mark.parametrize` が 0 件になって本 test が no-op 化する。
    OSError / 空 CSV は collection エラーとして **hard-fail** させる。
    """
    # OSError を握りつぶさない。CSV が存在しない・読めない場合は
    # 本 regression guard が無効化されるため、pytest collection が
    # エラーで止まって CI が赤になるのが正しい挙動。
    with open(CSV_PATH, encoding="utf-8") as f:
        ids = [row["id"] for row in csv.DictReader(f)]
    if not ids:
        # 空 CSV でも同じく no-op 化するので hard-fail させる
        raise RuntimeError(
            f"{CSV_PATH} is empty — HA48 regression guard would be a no-op. "
            "Regenerate snapshot via analysis pipeline."
        )
    return ids


@pytest.mark.parametrize("qid", _csv_ids())
def test_ha48_row_matches_snapshot(
    qid: str,
    expected_rows: dict,
    responses: dict,
    q_meta: dict,
) -> None:
    """HA48 1 件の audit 出力が CSV スナップショットと一致"""
    from detector import detect
    from ugh_calculator import calculate

    assert qid in expected_rows, f"CSV に {qid} がない"
    assert qid in responses, f"response JSONL に {qid} がない (data drift)"
    assert qid in q_meta, f"question_sets JSONL に {qid} がない (data drift)"

    expected = expected_rows[qid]
    evidence = detect(qid, responses[qid]["response"], q_meta[qid])
    state = calculate(evidence)

    # 整数・文字列フィールド: 完全一致
    assert evidence.propositions_hit == int(expected["hits"]), (
        f"{qid}: hits drift (expected {expected['hits']}, got {evidence.propositions_hit})"
    )
    assert evidence.propositions_total == int(expected["total"]), (
        f"{qid}: total drift (expected {expected['total']}, got {evidence.propositions_total})"
    )

    # 浮動小数フィールド: 4 桁丸め round-trip を許容
    assert state.S == pytest.approx(float(expected["S"]), abs=_ABS_TOL), (
        f"{qid}: S drift (expected {expected['S']}, got {state.S:.4f})"
    )
    assert state.C == pytest.approx(float(expected["C"]), abs=_ABS_TOL), (
        f"{qid}: C drift (expected {expected['C']}, got {state.C:.4f})"
    )
    assert state.delta_e == pytest.approx(float(expected["delta_e"]), abs=_ABS_TOL), (
        f"{qid}: ΔE drift (expected {expected['delta_e']}, got {state.delta_e:.4f})"
    )
    assert state.quality_score == pytest.approx(
        float(expected["quality_score"]), abs=_ABS_TOL,
    ), (
        f"{qid}: quality_score drift "
        f"(expected {expected['quality_score']}, got {state.quality_score:.4f})"
    )

    # f1-f4 (structural gate) も snapshot と一致
    assert evidence.f1_anchor == pytest.approx(float(expected["f1"]), abs=_ABS_TOL), (
        f"{qid}: f1 drift"
    )
    assert evidence.f2_unknown == pytest.approx(float(expected["f2"]), abs=_ABS_TOL), (
        f"{qid}: f2 drift"
    )
    assert evidence.f3_operator == pytest.approx(float(expected["f3"]), abs=_ABS_TOL), (
        f"{qid}: f3 drift"
    )
    f4_expected = float(expected["f4"])
    f4_actual = evidence.f4_premise if evidence.f4_premise is not None else 0.0
    assert f4_actual == pytest.approx(f4_expected, abs=_ABS_TOL), (
        f"{qid}: f4 drift (expected {f4_expected}, got {f4_actual})"
    )


# --- 集約サマリテスト (論文で報告する数字の直接検証) ---


def test_ha48_overall_hit_rate_matches_snapshot(
    expected_rows: dict,
    responses: dict,
    q_meta: dict,
) -> None:
    """全 48 件の hit 総数が CSV スナップショットと一致 (論文 baseline 合計)"""
    from detector import detect

    # 集約前に cardinality が canonical 48 件であることを再確認する。
    # これで sentinel 漏れた場合でも本 test が paper-facing "HA48" の前提
    # 破綻を surface できる。
    assert len(expected_rows) == EXPECTED_HA48_N, (
        f"aggregate test の前提破綻: expected {EXPECTED_HA48_N} rows, "
        f"got {len(expected_rows)}"
    )

    expected_total_hits = sum(int(r["hits"]) for r in expected_rows.values())
    expected_total_props = sum(int(r["total"]) for r in expected_rows.values())

    actual_total_hits = 0
    actual_total_props = 0
    for qid, expected in expected_rows.items():
        evidence = detect(qid, responses[qid]["response"], q_meta[qid])
        actual_total_hits += evidence.propositions_hit
        actual_total_props += evidence.propositions_total

    assert actual_total_hits == expected_total_hits, (
        f"HA48 total hits drift: expected {expected_total_hits}, "
        f"got {actual_total_hits}"
    )
    assert actual_total_props == expected_total_props, (
        f"HA48 total propositions drift: expected {expected_total_props}, "
        f"got {actual_total_props}"
    )
