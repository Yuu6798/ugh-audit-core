#!/usr/bin/env python3
"""Phase C v1 再採点 — TF-IDF char-ngram backend + 3パターンΔE + grv regex fallback

sentence-transformers モデルがダウンロードできない環境のため、
sklearn TfidfVectorizer (char_wb n-gram) でコサイン類似度を計算する。
grv はスコアラーの regex fallback (ストップワード除去済み) を使用する。
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ------------------------------------------------------------------ #
# grv 計算 (ugh_scorer.py の _grv_with_regex と同等)
# ------------------------------------------------------------------ #
GRV_STOPWORDS = {
    # 日本語機能語
    "は", "が", "を", "に", "で", "の", "と", "も", "か",
    "場合", "回答", "以下",
    "こと", "もの", "ため", "よう", "ほう", "として",
    # 機能語的末尾（regex が 3文字以上ひらがなとして取得する場合がある）
    "があります", "ています", "ません", "でしょう", "かもしれません",
    "いことは", "します", "である",
    # 英語機能語
    "this", "that", "the", "and", "for", "are", "not", "with",
}


def compute_grv(text: str) -> dict:
    """正規表現ベース grv (ストップワード除去済み)"""
    words = re.findall(r'[一-龯]{2,}|[ぁ-ん]{3,}|[ァ-ヴ\u30FC]{2,}|[a-zA-Z]{3,}', text)
    words = [w for w in words if w not in GRV_STOPWORDS and len(w) >= 2]
    if not words:
        return {}
    counts = Counter(words)
    total = sum(counts.values())
    return {w: round(c / total, 3) for w, c in counts.most_common(10)}


# ------------------------------------------------------------------ #
# TF-IDF ベース類似度
# ------------------------------------------------------------------ #
def compute_similarity_batch(pairs: list[tuple[str, str]]) -> list[float]:
    """TF-IDF char n-gram でテキスト対のコサイン類似度を一括計算"""
    all_texts = []
    for a, b in pairs:
        all_texts.extend([a, b])

    tfidf = TfidfVectorizer(
        analyzer='char_wb',
        ngram_range=(2, 4),
        max_features=10000,
    )
    matrix = tfidf.fit_transform(all_texts)

    results = []
    for i in range(0, len(all_texts), 2):
        sim = cosine_similarity(matrix[i:i + 1], matrix[i + 1:i + 2])[0][0]
        results.append(float(max(0.0, min(1.0, sim))))
    return results


def extract_head_sentences(text: str, n: int = 3) -> str:
    """テキストの先頭n文を抽出（ugh_scorer.py と同等ロジック）"""
    pattern = (
        r'(?<=[。？！?!])'
        r'|(?<=[^\d]\.)(?=\s+[A-Z\u3041-\u9fff]|\s+\d|\s+["\u201c]|\s*$)'
    )
    sentences = [s for s in re.split(pattern, text) if s.strip()]
    head = "".join(sentences[:n]).rstrip()
    if not head:
        head = text
    if len(head) > 200:
        head_sentences = [s for s in re.split(pattern, head) if s.strip()]
        truncated = ""
        for s in head_sentences:
            candidate = truncated + s
            if len(candidate) > 200:
                break
            truncated = candidate
        head = truncated.rstrip() if truncated else head[:200].rstrip()
    return head


POR_THRESHOLD = 0.82

# ------------------------------------------------------------------ #
# データ読み込み
# ------------------------------------------------------------------ #
RAW_FILE = Path("data/phase_c_v0/phase_c_raw.jsonl")
V0_CSV = Path("data/phase_c_v0/phase_c_results_v0.csv")


def load_raw_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_references_from_csv(path: Path) -> dict:
    refs = {}
    with open(path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for r in reader:
            qid = r['id']
            if qid not in refs:
                refs[qid] = {
                    'reference': r.get('reference', ''),
                    'reference_core': r.get('reference_core', ''),
                    'category': r.get('category', ''),
                    'role': r.get('role', ''),
                    'difficulty': r.get('difficulty', ''),
                    'trap_type': r.get('trap_type', ''),
                }
    return refs


# ------------------------------------------------------------------ #
# メイン処理
# ------------------------------------------------------------------ #
def main() -> None:
    print("Loading data...")
    raw_records = load_raw_jsonl(RAW_FILE)
    refs = load_references_from_csv(V0_CSV)
    print(f"Loaded {len(raw_records)} raw records, {len(refs)} references")

    # --- ペア構築 ---
    print("Building similarity pairs...")
    por_pairs = []        # (question, response)
    de_core_pairs = []    # (reference_core, response)
    de_full_pairs = []    # (reference, response)
    de_summary_pairs = [] # (reference_core, response_head)

    meta = []  # 付随メタ情報

    for rec in raw_records:
        qid = rec['id']
        question = rec['question']
        response = rec['response']
        temp = rec.get('temperature', '')

        ref_info = refs.get(qid, {})
        reference = ref_info.get('reference', '')
        reference_core = ref_info.get('reference_core', '')
        if not reference_core:
            reference_core = reference
        if not reference:
            reference = reference_core

        response_head = extract_head_sentences(response)

        por_pairs.append((question, response))
        de_core_pairs.append((reference_core, response))
        de_full_pairs.append((reference, response))
        de_summary_pairs.append((reference_core, response_head))

        meta.append({
            'id': qid,
            'category': ref_info.get('category', ''),
            'role': ref_info.get('role', ''),
            'difficulty': ref_info.get('difficulty', ''),
            'temperature': temp,
            'trap_type': ref_info.get('trap_type', ''),
            'question': question,
            'response': response,
            'reference': reference,
            'reference_core': reference_core,
        })

    # --- 一括類似度計算 ---
    print("Computing PoR similarities...")
    por_sims = compute_similarity_batch(por_pairs)

    print("Computing ΔE core...")
    de_core_sims = compute_similarity_batch(de_core_pairs)

    print("Computing ΔE full...")
    de_full_sims = compute_similarity_batch(de_full_pairs)

    print("Computing ΔE summary...")
    de_summary_sims = compute_similarity_batch(de_summary_pairs)

    # --- 結果構築 ---
    print("Building results...")
    results = []
    jsonl_records = []
    for i, m in enumerate(meta):
        por = round(por_sims[i], 4)
        por_fired = por >= POR_THRESHOLD
        delta_e_core = round(1.0 - de_core_sims[i], 4)
        delta_e_full = round(1.0 - de_full_sims[i], 4)
        delta_e_summary = round(1.0 - de_summary_sims[i], 4)

        grv = compute_grv(m['response'])
        grv_top = max(grv, key=grv.get) if grv else ""

        # CSV row (truncated question)
        results.append({
            'id': m['id'],
            'category': m['category'],
            'role': m['role'],
            'difficulty': m['difficulty'],
            'temperature': m['temperature'],
            'por': por,
            'por_fired': por_fired,
            'delta_e': delta_e_full,
            'delta_e_core': delta_e_core,
            'delta_e_full': delta_e_full,
            'delta_e_summary': delta_e_summary,
            'grv_top': grv_top,
            'backend': 'tfidf-char-ngram',
        })

        # JSONL record (full data)
        jsonl_records.append({
            'id': m['id'],
            'category': m['category'],
            'role': m['role'],
            'difficulty': m['difficulty'],
            'temperature': m['temperature'],
            'question': m['question'],
            'response': m['response'],
            'reference': m['reference'],
            'reference_core': m['reference_core'],
            'trap_type': m['trap_type'],
            'requires_manual_review': False,
            'model': 'gpt-4o',
            'usage': {},
            'por': por,
            'por_fired': por_fired,
            'delta_e_full': delta_e_full,
            'delta_e_core': delta_e_core,
            'delta_e_summary': delta_e_summary,
            'grv': grv,
            'backend': 'tfidf-char-ngram',
        })

    # --- 出力 ---
    OUT_CSV = Path("data/phase_c_v1/phase_c_v1_results.csv")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    # JSONL 出力
    OUT_JSONL = Path("data/phase_c_v1/phase_c_scored_v1.jsonl")
    with open(OUT_JSONL, 'w', encoding='utf-8') as f:
        for rec in jsonl_records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    print(f"\nDone: {len(results)} records -> {OUT_CSV}")
    print(f"Done: {len(jsonl_records)} records -> {OUT_JSONL}")

    # --- サマリー ---
    pors = [r['por'] for r in results]
    fired = sum(1 for r in results if r['por_fired'])
    de_core = [r['delta_e_core'] for r in results]
    de_full = [r['delta_e_full'] for r in results]
    de_summ = [r['delta_e_summary'] for r in results]

    print("\n=== v1 Summary ===")
    print(f"PoR mean: {sum(pors) / len(pors):.4f}")
    print(f"PoR fired: {fired}/{len(results)}")
    print(f"ΔE core mean:    {sum(de_core) / len(de_core):.4f}")
    print(f"ΔE full mean:    {sum(de_full) / len(de_full):.4f}")
    print(f"ΔE summary mean: {sum(de_summ) / len(de_summ):.4f}")
    print(f"ΔE full <= 0.10: {sum(1 for d in de_full if d <= 0.10)}/{len(results)}")

    # grv 不正トークンチェック
    bad_tokens = {'があります', 'します', 'いことは', 'クナイゼ', 'プンソ', 'コンピュ', 'である'}
    bad_count = sum(1 for r in results if r['grv_top'] in bad_tokens)
    print(f"Bad grv tokens: {bad_count}")


if __name__ == '__main__':
    main()
