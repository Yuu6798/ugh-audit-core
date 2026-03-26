"""synonym_ceiling_analysis.py — 58 miss命題の synonym 天井確認リサーチ

全 gap=1 (40件) + gap=2 miss[1,2] の 4b/4b' (18件) = 58 miss命題を分析し、
synonym辞書追加で回収可能な上限を確定する。
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import (
    _extract_content_bigrams,
    _expand_with_synonyms,
    _SYNONYM_MAP,
    detect_operator,
    OPERATOR_CATALOG,
)

# --- データ読み込み ---
def load_jsonl(path):
    data = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                data[r["id"]] = r
    return data

META = load_jsonl("data/question_sets/ugh-audit-100q-v3-1.json.txtl.txt")
RESP = {k: v.get("response", "") for k, v in load_jsonl("data/phase_c_scored_v1_t0_only.jsonl").items()}

# baseline CSV から gap=1, gap=2 miss[1,2] を抽出
BASELINE = []
with open("data/eval/audit_102_main_baseline_round4.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        BASELINE.append(r)

# 全命題のbigramキャッシュ（偽ヒット分析用）
ALL_PROPS = []  # (qid, idx, prop_text, prop_bigrams)
for qid in sorted(META.keys()):
    for i, p in enumerate(META[qid].get("core_propositions", [])):
        ALL_PROPS.append((qid, i, p, _extract_content_bigrams(p)))

ALL_RESP_BI = {}  # qid -> resp_bigrams
for qid in RESP:
    ALL_RESP_BI[qid] = _extract_content_bigrams(RESP[qid])

# --- 対象抽出 ---
targets = []  # (qid, miss_idx, group)

# gap=1: 40問
for row in BASELINE:
    hits = int(row["hits"])
    total = int(row["total"])
    if total - hits == 1:
        miss_ids = eval(row["miss_ids"])
        for mi in miss_ids:
            targets.append((row["id"], mi, "gap1"))

# gap=2 miss[1,2] の 4b/4b' (確定4件は除く: q040[2], q037[1], q039[2], q084[2])
# ただし全gap=2 miss[1,2]から確定4件と構造的不能(X判定済み)を除いた残り
gap2_already_done = {
    # 4a 確定: q040[2], q037[1], q039[2], q084[2]
    ("q040", 2), ("q037", 1), ("q039", 2), ("q084", 2),
    # 構造的不能(X): q002[1], q002[2], q004[1], q018[1], q018[2], q040[1],
    # q054[1], q067[2], q069[1]
    ("q002", 1), ("q002", 2), ("q004", 1), ("q018", 1), ("q018", 2),
    ("q040", 1), ("q054", 1), ("q067", 2), ("q069", 1),
    # q039[1]: 脱落(メモリ不在), q052[2]: 脱落(回避不在), q015[1]: 脱落(帰属不在)
    # q003[1]: 脱落(保守不在), q069[2]: 脱落(marker不在)
    ("q039", 1), ("q052", 2), ("q015", 1), ("q003", 1), ("q069", 2),
}
for row in BASELINE:
    hits = int(row["hits"])
    total = int(row["total"])
    if total - hits == 2 and row["miss_ids"] == "[1, 2]":
        for mi in [1, 2]:
            if (row["id"], mi) not in gap2_already_done:
                targets.append((row["id"], mi, "gap2"))

print(f"分析対象: {len(targets)} miss命題")
print(f"  gap=1: {sum(1 for t in targets if t[2]=='gap1')}")
print(f"  gap=2: {sum(1 for t in targets if t[2]=='gap2')}")
print()

# --- NG3 判定用: 候補語の出現頻度 ---
def count_props_with(bigram):
    """命題中に該当bigramを含む命題数"""
    return sum(1 for _, _, _, pbi in ALL_PROPS if bigram in pbi)

def count_resps_with(bigram):
    """回答中に該当bigramを含む回答数"""
    return sum(1 for rbi in ALL_RESP_BI.values() if bigram in rbi)

# --- 回答テキスト中の synonym 候補検索 ---
def find_synonym_candidates(miss_bigram, response_text, resp_bigrams):
    """miss_bigramに対して、回答テキスト中の意味的等価語を探す"""
    candidates = []

    # 1. 回答bigram中で同じ漢字を含むもの
    chars = set(c for c in miss_bigram if '\u4e00' <= c <= '\u9fff')
    if chars:
        for rb in resp_bigrams:
            rb_chars = set(c for c in rb if '\u4e00' <= c <= '\u9fff')
            if chars & rb_chars and rb != miss_bigram:
                candidates.append(rb)

    return candidates

# --- 意味的等価判定のための手動マッピング ---
# 頻出パターンの synonym 候補（精読に基づく）
MANUAL_SEARCH = {
    # kanji compounds -> search terms in response
    "継承": ["引き継", "受け継", "反映", "伝播", "再現"],
    "模倣": ["模倣", "真似", "シミュレート", "模擬", "ように見え"],
    "必然": ["必ず", "保証", "確実", "担保", "正しさ"],
    "等価": ["同等", "等しい", "同じ", "相当", "等価"],
    "核心": ["核心", "本質", "中心", "根本", "要点", "肝"],
    "自己": ["自己", "自律", "自身", "自分", "自ら"],
    "決定": ["決定", "判断", "選択", "意思", "コントロール"],
    "段階": ["段階", "段階的", "レベル", "程度", "フェーズ"],
    "排除": ["排除", "除去", "取り除", "削減", "減少", "軽減", "緩和"],
    "貢献": ["貢献", "寄与", "役立", "有益", "メリット", "助け", "利点"],
    "連鎖": ["連鎖", "チェーン", "つながり", "一連", "連続"],
    "断絶": ["断絶", "切断", "不明確", "曖昧", "途切"],
    "細分": ["細分", "分割", "分解", "サブワード", "分け"],
    "一体": ["一体", "まとまり", "統一", "完全", "全体"],
    "迷子": ["迷子", "lost", "忘れ", "見失", "注意", "中間"],
    "活用": ["活用", "利用", "使用", "用い", "使い"],
    "周縁": ["周縁", "マイノリティ", "少数", "弱者", "マージナル"],
    "固定": ["固定", "再生産", "強化", "永続", "持続", "拡大"],
    "粒度": ["粒度", "レベル", "細かさ", "単位", "スケール", "解像度"],
    "共振": ["共振", "共鳴", "類似", "一致", "マッチ", "関連"],
    "比較": ["比較", "参照", "基準", "ベンチマーク", "対照"],
    "品質": ["品質", "質", "精度", "信頼性", "正確"],
    "前提": ["前提", "前提条件", "基盤", "基礎", "土台", "仮定"],
    "検証": ["検証", "評価", "テスト", "確認", "チェック", "監査"],
    "外部": ["外部", "第三者", "独立", "外から"],
    "不可": ["不可欠", "必須", "必要", "欠かせ"],
    "有益": ["有益", "有用", "効果", "メリット", "利点", "恩恵"],
    "応用": ["応用", "活用", "適用", "利用", "実用"],
    "規制": ["規制", "規則", "法律", "ルール", "制限", "管理", "法的"],
    "論点": ["論点", "議論", "問題", "課題", "焦点", "争点"],
    "根本": ["根本", "本質", "基本", "根源", "fundamental"],
    "中心": ["中心", "中心的", "主義", "ベース", "基盤"],
    "区別": ["区別", "違い", "差異", "異なる", "distinction"],
    "文脈": ["文脈", "コンテキスト", "context", "状況", "場面"],
    "応答": ["応答", "回答", "返答", "出力", "生成", "レスポンス"],
    "因果": ["因果", "原因", "結果", "causal"],
    "役割": ["役割", "機能", "働き", "function", "ロール"],
    "記述": ["記述", "説明", "叙述", "描写", "表現"],
    "置換": ["置換", "代替", "置き換え", "代わり", "入れ替え"],
    "空転": ["空転", "無意味", "意味がない", "循環", "トートロジー"],
    "全面": ["全面", "完全", "一律", "全て", "一概"],
    "禁止": ["禁止", "ban", "制限", "forbidden", "停止"],
    "情報": ["情報", "データ", "知識", "information"],
    "申立": ["申立", "申し立て", "抗議", "異議", "訴え"],
    "経路": ["経路", "パス", "手段", "方法", "チャネル"],
    "争点": ["争点", "論点", "焦点", "問題", "課題", "議論"],
    "タスク": ["タスク", "課題", "作業", "task"],
    "ドメイン": ["ドメイン", "領域", "分野", "domain"],
    "モデル": ["モデル", "model"],
    "依存": ["依存", "左右", "影響", "条件"],
    "ヒューマン": ["ヒューマン", "人間", "human"],
    "レーティング": ["レーティング", "評価", "rating", "スコア", "scoring"],
    "相関": ["相関", "関連", "関係", "correlation"],
    "劣化": ["劣化", "低下", "悪化", "損失", "落ちる"],
    "保守": ["保守", "控えめ", "慎重", "無難", "安全"],
    "無難": ["無難", "安全", "標準的", "一般的", "平凡"],
    "稀な": ["稀な", "稀", "まれ", "珍しい", "少数", "希少"],
    "事象": ["事象", "イベント", "事例", "ケース", "event"],
    "正確": ["正確", "精度", "accuracy", "正しく"],
    "用途": ["用途", "使途", "目的", "ユースケース", "使い方"],
}

# --- メイン分析 ---
results = []

for qid, miss_idx, group in targets:
    meta = META.get(qid, {})
    props = meta.get("core_propositions", [])
    if miss_idx >= len(props):
        continue
    prop = props[miss_idx]
    resp = RESP.get(qid, "")

    prop_bi = _extract_content_bigrams(prop)
    resp_bi = ALL_RESP_BI.get(qid, set())
    prop_exp = _expand_with_synonyms(prop_bi)

    direct_ovl = prop_bi & resp_bi
    syn_ovl = prop_exp & resp_bi
    miss_bi = prop_bi - resp_bi

    op = detect_operator(prop)
    n_prop = len(prop_bi) if prop_bi else 1
    n_exp = len(prop_exp) if prop_exp else 1
    dr = len(direct_ovl) / n_prop
    fr = len(syn_ovl) / n_exp
    current_ovl = len(syn_ovl)

    # 通常パスの閾値
    need_ovl = min(3, n_prop)
    if op:
        need_ovl_op = 2  # operator path

    # Step 1: 各miss bigramについて回答テキスト中の候補を探索
    found_pairs = []  # (prop_key, resp_value, confidence)

    for mb in sorted(miss_bi):
        # Manual search dictionary
        search_terms = MANUAL_SEARCH.get(mb, [])
        # Also try the bigram itself
        search_terms = [mb] + search_terms

        for term in search_terms:
            if term.lower() in resp.lower() and term != mb:
                # Found! Now extract the actual bigram form
                term_bi = _extract_content_bigrams(term)
                # Check which bigram form is in resp_bi
                matching_resp_bi = term_bi & resp_bi
                if matching_resp_bi:
                    for mrb in matching_resp_bi:
                        found_pairs.append((mb, mrb))
                        break
                    break
                elif term in resp:
                    # Term exists but not as a bigram - might be too short
                    found_pairs.append((mb, term))
                    break

    # Step 2: NG checks on found pairs
    ok_pairs = []
    ng_pairs = []

    for pk, rv in found_pairs:
        # NG3 check
        rv_bi = rv if rv in resp_bi else None
        if rv_bi:
            n_props_with_key = count_props_with(pk)
            n_resps_with_val = count_resps_with(rv_bi)
            if n_resps_with_val >= 10:
                ng_pairs.append((pk, rv, f"NG3:resp={n_resps_with_val}"))
                continue
            if n_props_with_key >= 3:
                # Check: is it already in the synonym map?
                already_syn = pk in _SYNONYM_MAP
                ng_pairs.append((pk, rv, f"NG3:prop={n_props_with_key}"))
                continue
        ok_pairs.append((pk, rv))

    # Step 3: ovl reachability
    new_ovl = current_ovl + len(ok_pairs)

    # Determine status
    if not found_pairs and not ok_pairs:
        status = "absent"
        step1 = "absent"
        step2 = "-"
        step3 = "-"
    elif not ok_pairs and ng_pairs:
        status = "NG"
        step1 = "present"
        step2 = ng_pairs[0][2]
        step3 = "-"
    elif ok_pairs:
        step1 = "present"
        step2 = "OK"
        if op and new_ovl >= 2:
            step3 = f"reach(op,ovl={new_ovl})"
            status = f"recover_{len(ok_pairs)}"
        elif new_ovl >= need_ovl:
            step3 = f"reach(ovl={new_ovl})"
            status = f"recover_{len(ok_pairs)}"
        else:
            deficit = need_ovl - new_ovl
            step3 = f"short_{deficit}"
            status = f"short_{deficit}"
    else:
        status = "absent"
        step1 = "absent"
        step2 = "-"
        step3 = "-"

    results.append({
        "qid": qid,
        "idx": miss_idx,
        "group": group,
        "prop": prop,
        "dr": dr,
        "current_ovl": current_ovl,
        "op": op.family if op else None,
        "step1": step1,
        "step2": step2,
        "step3": step3,
        "status": status,
        "ok_pairs": ok_pairs,
        "ng_pairs": ng_pairs,
        "miss_bi": sorted(miss_bi),
        "direct_hit": sorted(direct_ovl),
    })

# --- 出力 ---
print("=" * 100)
print("SYNONYM CEILING ANALYSIS — 全58件")
print("=" * 100)

for group_name, group_label in [("gap2", "gap=2 (4b/4b')"), ("gap1", "gap=1")]:
    group_results = [r for r in results if r["group"] == group_name]
    print(f"\n{'='*80}")
    print(f"  {group_label}: {len(group_results)}件")
    print(f"{'='*80}")

    for r in group_results:
        op_str = f" [OP:{r['op']}]" if r['op'] else ""
        pairs_str = ", ".join(f"{pk}->{rv}" for pk, rv in r["ok_pairs"]) if r["ok_pairs"] else "-"
        ng_str = ", ".join(f"{pk}->{rv}({reason})" for pk, rv, reason in r["ng_pairs"]) if r["ng_pairs"] else ""

        print(f"\n{r['qid']}[{r['idx']}] | ovl={r['current_ovl']} dr={r['dr']:.2f}{op_str}")
        print(f"  prop: {r['prop']}")
        print(f"  hit: {r['direct_hit']} | miss: {r['miss_bi']}")
        print(f"  Step1={r['step1']} Step2={r['step2']} Step3={r['step3']}")
        print(f"  status={r['status']} | pairs: {pairs_str}")
        if ng_str:
            print(f"  NG: {ng_str}")

# --- 最終集計 ---
print("\n" + "=" * 80)
print("最終集計")
print("=" * 80)

status_counts = Counter()
for r in results:
    if r["status"].startswith("recover_"):
        n = int(r["status"].split("_")[1])
        if n == 1:
            status_counts["回収可能(1語)"] += 1
        elif n == 2:
            status_counts["回収可能(2語)"] += 1
        else:
            status_counts[f"回収可能({n}語)"] += 1
    elif r["status"].startswith("short_"):
        status_counts["ovl不足"] += 1
    elif r["status"] == "NG":
        status_counts["品質NG"] += 1
    elif r["status"] == "absent":
        status_counts["概念不在"] += 1

print(f"\n| 分類 | 件数 |")
print(f"|------|------|")
for label in ["回収可能(1語)", "回収可能(2語)", "回収可能(3語)", "ovl不足", "品質NG", "概念不在"]:
    print(f"| {label} | {status_counts.get(label, 0)} |")
print(f"| 合計 | {len(results)} |")

total_recoverable = sum(v for k, v in status_counts.items() if k.startswith("回収可能"))
print(f"\nsynonym天井 = 4a確定(4) + 本分析回収可能({total_recoverable}) = {4 + total_recoverable}")
print(f"現在ヒット: 181/310")
print(f"synonym全投入後: {181 + 4 + total_recoverable}/310 = {(181+4+total_recoverable)/310*100:.1f}%")
print(f"65%到達に必要: {int(310*0.65)} - {181+4+total_recoverable} = {max(0, int(310*0.65) - 181 - 4 - total_recoverable)}件 (構造改善で)")

# ペア一覧
print("\n" + "=" * 80)
print("回収可能ペア一覧")
print("=" * 80)
for r in results:
    if r["ok_pairs"] and r["status"].startswith("recover_"):
        for pk, rv in r["ok_pairs"]:
            n_p = count_props_with(pk)
            n_r = count_resps_with(rv) if rv in resp_bi else "?"
            print(f"  {r['qid']}[{r['idx']}]: {pk} -> {rv} (props={n_p}, resps={n_r})")
