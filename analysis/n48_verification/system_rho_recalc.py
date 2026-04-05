"""system rho recalc -- 190/310 baseline (fr=0.30)"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

BASE = Path(__file__).resolve().parent
MERGED_V2 = BASE / "ha48_merged_v2.csv"
NEW_BASELINE = BASE.parent.parent / "data" / "eval" / "audit_102_main_baseline_safe_relaxed.csv"
OUT_V3 = BASE / "ha48_merged_v3.csv"
OUT_SUMMARY = BASE / "system_rho_results.md"
OUT_FP_FN = BASE / "fp_fn_analysis_v2.csv"

W_F1, W_F2, W_F3, W_F4 = 5, 25, 5, 5
W_SUM = 40
WEIGHT_S, WEIGHT_C = 2, 1

HA20_IDS = {
    "q024", "q075", "q009", "q080", "q037", "q100", "q015", "q012",
    "q049", "q033", "q071", "q061", "q083", "q025", "q095", "q032",
    "q044", "q019", "q069", "q063",
}


def calc_s(f1, f2, f3, f4):
    return 1.0 - (W_F1 * f1 + W_F2 * f2 + W_F3 * f3 + W_F4 * f4) / W_SUM


def calc_delta_e_a(s, c):
    return (WEIGHT_S * (1 - s) ** 2 + WEIGHT_C * (1 - c) ** 2) / (WEIGHT_S + WEIGHT_C)


def main():
    # Load new baseline
    baseline = {}
    with open(NEW_BASELINE) as f:
        for row in csv.DictReader(f):
            baseline[row["id"]] = row
    total_bl = sum(int(baseline[q]["hits"]) for q in baseline)
    total_props_bl = sum(int(baseline[q]["total"]) for q in baseline)
    print("Baseline: {}/{}".format(total_bl, total_props_bl))

    # Load v2 and update system_hit_rate
    rows = []
    changes = []
    with open(MERGED_V2) as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        for row in reader:
            qid = row["id"]
            if qid in baseline:
                bl = baseline[qid]
                hits = int(bl["hits"])
                total = int(bl["total"])
                new_sys_hr = round(hits / total, 4) if total > 0 else 0.0
                old_sys_hr = float(row["system_hit_rate"])

                if abs(new_sys_hr - old_sys_hr) > 0.0001:
                    changes.append((qid, old_sys_hr, new_sys_hr))

                row["system_hit_rate"] = str(new_sys_hr)

                # f1-f4 / S_score は v2 の値を維持（前セッション確定値）
                s_score = float(row["S_score"])

                # delta_e_a_sys のみ再計算（system_hit_rate 変更分）
                de_sys = calc_delta_e_a(s_score, new_sys_hr)
                row["delta_e_a_sys"] = str(round(de_sys, 4))

                # delta_e_a_ref は不変（human_hit_rate, S ともに変更なし）
                # 念のため再計算して検証
                human_hr = float(row["human_hit_rate"])
                de_ref = calc_delta_e_a(s_score, human_hr)
                row["delta_e_a_ref"] = str(round(de_ref, 4))

            rows.append(row)

    # Write v3
    with open(OUT_V3, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("\nsystem_hit_rate changes: {}".format(len(changes)))
    for qid, old, new in changes:
        sg = "HA20" if qid in HA20_IDS else "HA28"
        print("  {} ({}): {:.4f} -> {:.4f}".format(qid, sg, old, new))

    # Compute rho
    O_all, de_sys_all, de_ref_all = [], [], []
    O_ha20, de_sys_ha20, de_ref_ha20 = [], [], []
    O_ha28, de_sys_ha28, de_ref_ha28 = [], [], []

    for row in rows:
        qid = row["id"]
        o = float(row["O"])
        de_sys = float(row["delta_e_a_sys"])
        de_ref = float(row["delta_e_a_ref"])

        O_all.append(o)
        de_sys_all.append(de_sys)
        de_ref_all.append(de_ref)

        if qid in HA20_IDS:
            O_ha20.append(o)
            de_sys_ha20.append(de_sys)
            de_ref_ha20.append(de_ref)
        else:
            O_ha28.append(o)
            de_sys_ha28.append(de_sys)
            de_ref_ha28.append(de_ref)

    def compute_rho(o_list, de_list, label):
        o_arr = np.array(o_list)
        pred = 5 - 4 * np.array(de_list)
        rho, p = spearmanr(pred, o_arr)
        print("  {}: rho={:.4f}, p={:.4f} (n={})".format(label, rho, p, len(o_arr)))
        return rho, p

    print("\n=== delta_e_a system rho ===")
    rho_sys_all, p_sys_all = compute_rho(O_all, de_sys_all, "ALL")
    rho_sys_ha20, p_sys_ha20 = compute_rho(O_ha20, de_sys_ha20, "HA20")
    rho_sys_ha28, p_sys_ha28 = compute_rho(O_ha28, de_sys_ha28, "HA28")

    print("\n=== delta_e_a reference rho ===")
    rho_ref_all, p_ref_all = compute_rho(O_all, de_ref_all, "ALL")
    rho_ref_ha20, p_ref_ha20 = compute_rho(O_ha20, de_ref_ha20, "HA20")
    rho_ref_ha28, p_ref_ha28 = compute_rho(O_ha28, de_ref_ha28, "HA28")

    # FP/FN analysis
    fp_fn_rows = []
    tp_all, fp_all, fn_all, tn_all = 0, 0, 0, 0
    tp_ha20, fp_ha20, fn_ha20, tn_ha20 = 0, 0, 0, 0
    tp_ha28, fp_ha28, fn_ha28, tn_ha28 = 0, 0, 0, 0

    for row in rows:
        qid = row["id"]
        sg = "HA20" if qid in HA20_IDS else "HA28"
        human_hr = float(row["human_hit_rate"])

        if qid not in baseline:
            continue
        bl = baseline[qid]
        n_props = int(bl["total"])
        sys_hits = int(bl["hits"])

        ph = row.get("propositions_hit", "")
        if "/" in ph:
            human_hits = int(ph.split("/")[0])
        else:
            human_hits = round(human_hr * n_props)

        tp = min(sys_hits, human_hits)
        fp = sys_hits - tp
        fn = human_hits - tp
        tn = n_props - sys_hits - fn

        fp_fn_rows.append({
            "qid": qid, "subgroup": sg, "n_props": n_props,
            "sys_hits": sys_hits, "human_hits": human_hits,
            "tp_max": tp, "fp_min": fp, "fn_min": fn, "tn_max": tn,
        })

        tp_all += tp
        fp_all += fp
        fn_all += fn
        tn_all += tn
        if sg == "HA20":
            tp_ha20 += tp
            fp_ha20 += fp
            fn_ha20 += fn
            tn_ha20 += tn
        else:
            tp_ha28 += tp
            fp_ha28 += fp
            fn_ha28 += fn
            tn_ha28 += tn

    def safe_div(a, b):
        return a / b if b > 0 else 1.0

    prec_all = safe_div(tp_all, tp_all + fp_all)
    rec_all = safe_div(tp_all, tp_all + fn_all)
    prec_ha20 = safe_div(tp_ha20, tp_ha20 + fp_ha20)
    rec_ha20 = safe_div(tp_ha20, tp_ha20 + fn_ha20)
    prec_ha28 = safe_div(tp_ha28, tp_ha28 + fp_ha28)
    rec_ha28 = safe_div(tp_ha28, tp_ha28 + fn_ha28)

    # Write FP/FN CSV
    fp_fn_fields = [
        "qid", "subgroup", "n_props", "sys_hits", "human_hits",
        "tp_max", "fp_min", "fn_min", "tn_max",
    ]
    with open(OUT_FP_FN, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fp_fn_fields)
        writer.writeheader()
        writer.writerows(fp_fn_rows)

    print("\n=== FP/FN (best case) ===")
    print("  ALL: TP={} FP={} FN={} TN={} prec={:.4f} rec={:.4f}".format(
        tp_all, fp_all, fn_all, tn_all, prec_all, rec_all))
    print("  HA20: TP={} FP={} FN={} TN={} prec={:.4f} rec={:.4f}".format(
        tp_ha20, fp_ha20, fn_ha20, tn_ha20, prec_ha20, rec_ha20))
    print("  HA28: TP={} FP={} FN={} TN={} prec={:.4f} rec={:.4f}".format(
        tp_ha28, fp_ha28, fn_ha28, tn_ha28, prec_ha28, rec_ha28))

    # New FP check
    old_fp = {}
    with open(MERGED_V2) as f:
        for r in csv.DictReader(f):
            qid = r["id"]
            if qid in baseline:
                old_fp[qid] = float(r["system_hit_rate"]) > float(r["human_hit_rate"]) + 0.001

    new_fp_items = []
    for r in fp_fn_rows:
        qid = r["qid"]
        if r["sys_hits"] > r["human_hits"] and not old_fp.get(qid, False):
            new_fp_items.append(qid)

    print("\n  New FP: {}".format(new_fp_items if new_fp_items else "none"))

    # Write summary MD
    gap = rho_ref_all - rho_sys_all
    md = []
    md.append("# system rho recalc -- 190/310 baseline\n")
    md.append("## Baseline\n")
    md.append("- File: `audit_102_main_baseline_safe_relaxed.csv`")
    md.append("- Hits: {}/{} ({:.4f})".format(total_bl, total_props_bl, total_bl / total_props_bl))
    md.append("- Change: fr=0.30 (full_recall 0.35 -> 0.30)\n")

    md.append("## system_hit_rate changes\n")
    md.append("| qid | subgroup | old | new | delta |")
    md.append("|-----|----------|-----|-----|-------|")
    for qid, old, new in changes:
        sg = "HA20" if qid in HA20_IDS else "HA28"
        md.append("| {} | {} | {:.4f} | {:.4f} | {:+.4f} |".format(qid, sg, old, new, new - old))
    md.append("")

    md.append("## delta_e_a system rho\n")
    md.append("| metric | prev (189/310) | now (190/310) | delta |")
    md.append("|--------|----------------|---------------|-------|")
    md.append("| system rho | 0.484 | **{:.4f}** | {:+.4f} |".format(rho_sys_all, rho_sys_all - 0.484))
    md.append("| system p | - | {:.4f} | - |".format(p_sys_all))
    md.append("| reference rho | 0.857 | **{:.4f}** | {:+.4f} |".format(rho_ref_all, rho_ref_all - 0.857))
    md.append("| reference p | - | {:.4f} | - |".format(p_ref_all))
    md.append("| gap (ref - sys) | 0.373 | {:.4f} | {:+.4f} |".format(gap, gap - 0.373))
    md.append("")

    md.append("## Subgroup system rho\n")
    md.append("| group | n | sys rho | p | ref rho | p |")
    md.append("|-------|---|---------|---|---------|---|")
    md.append("| HA20 | {} | {:.4f} | {:.4f} | {:.4f} | {:.4f} |".format(
        len(O_ha20), rho_sys_ha20, p_sys_ha20, rho_ref_ha20, p_ref_ha20))
    md.append("| HA28 | {} | {:.4f} | {:.4f} | {:.4f} | {:.4f} |".format(
        len(O_ha28), rho_sys_ha28, p_sys_ha28, rho_ref_ha28, p_ref_ha28))
    md.append("| **ALL** | **{}** | **{:.4f}** | **{:.4f}** | **{:.4f}** | **{:.4f}** |".format(
        len(O_all), rho_sys_all, p_sys_all, rho_ref_all, p_ref_all))
    md.append("")

    md.append("## FP/FN (best case)\n")
    md.append("| metric | prev (189/310) | now (190/310) | delta |")
    md.append("|--------|----------------|---------------|-------|")
    md.append("| FP | 26 | {} | {:+d} |".format(fp_all, fp_all - 26))
    md.append("| FN | 8 | {} | {:+d} |".format(fn_all, fn_all - 8))
    md.append("| Precision | 0.732 | {:.4f} | {:+.4f} |".format(prec_all, prec_all - 0.732))
    md.append("| Recall | 0.899 | {:.4f} | {:+.4f} |".format(rec_all, rec_all - 0.899))
    md.append("")

    md.append("### Subgroup FP/FN\n")
    md.append("| group | TP | FP | FN | TN | Precision | Recall |")
    md.append("|-------|----|----|----|----|-----------| -------|")
    md.append("| HA20 | {} | {} | {} | {} | {:.4f} | {:.4f} |".format(
        tp_ha20, fp_ha20, fn_ha20, tn_ha20, prec_ha20, rec_ha20))
    md.append("| HA28 | {} | {} | {} | {} | {:.4f} | {:.4f} |".format(
        tp_ha28, fp_ha28, fn_ha28, tn_ha28, prec_ha28, rec_ha28))
    md.append("| **ALL** | **{}** | **{}** | **{}** | **{}** | **{:.4f}** | **{:.4f}** |".format(
        tp_all, fp_all, fn_all, tn_all, prec_all, rec_all))
    md.append("")

    if new_fp_items:
        md.append("### New FP: {}\n".format(", ".join(new_fp_items)))
    else:
        md.append("### New FP: none\n")

    sys_pass = rho_sys_all >= 0.50
    fp_pass = (fp_all - 26) <= 1
    ref_pass = abs(rho_ref_all - 0.857) < 0.01

    md.append("## Verdict\n")
    md.append("| criterion | threshold | result | verdict |")
    md.append("|-----------|-----------|--------|---------|")
    md.append("| system rho | >= 0.50 | {:.4f} | {} |".format(
        rho_sys_all, "**PASS**" if sys_pass else "FAIL"))
    md.append("| FP increase | <= 1 | {:+d} | {} |".format(
        fp_all - 26, "**PASS**" if fp_pass else "FAIL"))
    md.append("| reference rho | unchanged (~0.857) | {:.4f} | {} |".format(
        rho_ref_all, "**PASS**" if ref_pass else "FAIL"))
    md.append("")

    if sys_pass:
        md.append("**Conclusion: system rho >= 0.50 achieved.**")
    elif rho_sys_all > 0.484:
        md.append("**Conclusion: system rho < 0.50 but improved. Continue C improvement.**")
    else:
        md.append("**Conclusion: system rho < 0.50 and worsened. Revert fr=0.30.**")

    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("\nOutput: {}".format(OUT_V3))
    print("Output: {}".format(OUT_SUMMARY))
    print("Output: {}".format(OUT_FP_FN))


if __name__ == "__main__":
    main()
