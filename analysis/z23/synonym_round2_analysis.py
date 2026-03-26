#!/usr/bin/env python3
"""
Z_23 synonym map 第2ラウンド分析スクリプト
演算子なし平叙命題57件の言い換え表現を回答テキストから収集する。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# ── Z_23 IDs ──
Z23_IDS = [
    "q001", "q002", "q003", "q004", "q007", "q011", "q012",
    "q015", "q016", "q030", "q031", "q036", "q041", "q045",
    "q048", "q064", "q065", "q066", "q067", "q074", "q090",
    "q099", "qg01",
]

# ── 現行 synonym_dict (60エントリ) ──
EXISTING_SYNONYMS = {
    "llm": ["ai"],
    "条件": ["基準", "要件"],
    "話者": ["証言者", "発話者"],
    "正直": ["信頼", "誠実"],
    "仮説": ["理論", "学説"],
    "反証": ["覆す", "否定", "誤り"],
    "妥当": ["有効", "適切"],
    "拒否": ["否定", "退け"],
    "閾値": ["限界", "境界"],
    "検証": ["確認", "実証", "証明", "評価", "検討"],
    "低下": ["下がる", "減少"],
    "集合": ["集約", "総体"],
    "トークン": ["単語"],
    "配分": ["分配", "割当"],
    "事例": ["実例"],
    "帰属": ["所在", "帰責"],
    "十分": ["保証", "確実"],
    "複合": ["多角", "総合", "多面"],
    "偏在": ["集中", "偏り"],
    "核心": ["本質", "要点", "論点"],
    "普遍": ["一律", "固定", "一般"],
    "トレードオフ": ["バランス", "両立", "二律", "背反", "長短"],
    "構造": ["仕組み", "体系", "枠組"],
    "機能": ["役割", "動作", "作用", "働き"],
    "依存": ["左右", "影響"],
    "関係": ["関連", "結び"],
    "対応": ["対処", "適合"],
    "同一": ["一致", "等価"],
    "対立": ["矛盾", "衝突", "相反"],
    "排除": ["除外", "防止"],
    "保証": ["担保", "確保", "裏付"],
    "測定": ["計測"],
    "拡張": ["拡大", "展開"],
    "生成": ["作成", "出力", "産出"],
    "論理": ["推論", "ロジック"],
    "決定": ["判断", "選択"],
    "直接": ["明示"],
    "指標": ["尺度", "メトリクス"],
    "連鎖": ["連続", "波及"],
    "断絶": ["途切", "切断"],
    "存在": ["実在"],
    "現象": ["事象"],
    "grv": ["語彙", "偏り", "重力"],
    "δe": ["ズレ", "距離"],
    "por": ["共鳴", "共振"],
    "共振": ["共鳴", "対応"],
    "reference": ["参照", "基準", "正解"],
    "功利": ["帰結", "効用"],
    "紛争": ["戦争", "武力", "軍事"],
    "空洞": ["形骸", "不在", "欠如"],
    "類推": ["推論", "類比", "アナロジー"],
    "経験": ["実践", "実際", "実証"],
    "根拠": ["理由", "証拠", "裏付"],
    "前提": ["仮定", "想定"],
    "主体": ["当事", "行為"],
    "リスク": ["危険", "恐れ", "懸念"],
    "確立": ["構築", "整備", "定着"],
    "差別": ["不公", "不平", "格差"],
    "優劣": ["比較", "上下"],
    "未確": ["未定", "不明", "未知"],
}


def load_yaml_propositions(path: Path) -> list[dict]:
    """簡易YAMLパーサ（pyaml不要）"""
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f)
    return data


def load_yaml_propositions_manual(path: Path) -> list[dict]:
    """yaml モジュールがない場合の簡易パーサ"""
    results = []
    with open(path) as f:
        text = f.read()

    # Split by "- id:" entries
    entries = re.split(r'^- id:', text, flags=re.MULTILINE)
    for entry in entries[1:]:  # skip first empty
        lines = entry.strip().split('\n')
        qid = lines[0].strip()
        prop_idx = None
        original = ""
        operators = None

        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith('proposition_index:'):
                prop_idx = int(stripped.split(':')[1].strip())
            elif stripped.startswith('original:'):
                original = stripped.split(':', 1)[1].strip()
            elif stripped == 'operators: []':
                operators = []
            elif stripped == 'operators:':
                operators = ['has_operators']

        results.append({
            'id': qid,
            'proposition_index': prop_idx,
            'original': original,
            'has_operators': operators != [],
        })
    return results


def load_responses(path: Path) -> dict[str, str]:
    """t=0.0 の回答テキストを読み込む"""
    responses = {}
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d['id'] in Z23_IDS and d.get('temperature') == 0.0:
                responses[d['id']] = d['response']
    return responses


def is_existing_pair(key: str, val: str) -> bool:
    """既存辞書に含まれるペアか"""
    for k, vals in EXISTING_SYNONYMS.items():
        if k == key and val in vals:
            return True
        # 部分一致もチェック
        if key.startswith(k) or k.startswith(key):
            if val in vals:
                return True
    return False


def check_word_in_response(word: str, response: str) -> bool:
    """語が回答テキスト中に出現するか"""
    return word in response


def main():
    yaml_path = ROOT / "analysis" / "z23" / "part_c_normalized_draft.yaml"
    jsonl_path = ROOT / "data" / "phase_c_v1" / "phase_c_scored_v1.jsonl"

    # Load propositions
    try:
        props = load_yaml_propositions(yaml_path)
    except ImportError:
        props = load_yaml_propositions_manual(yaml_path)

    # Load responses
    responses = load_responses(jsonl_path)

    # Filter operator-free propositions
    op_free = [p for p in props if not p.get('has_operators', True)]
    print(f"Operator-free propositions: {len(op_free)}")
    print(f"Responses loaded: {len(responses)}")

    # Print for manual analysis
    for p in op_free:
        qid = p['id']
        idx = p['proposition_index']
        orig = p['original']
        resp = responses.get(qid, "NO RESPONSE")
        print(f"\n{'='*80}")
        print(f"ID: {qid} | idx: {idx}")
        print(f"命題: {orig}")
        print(f"回答: {resp[:500]}")


if __name__ == "__main__":
    main()
