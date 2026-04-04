"""audit.py — エンドツーエンド統合 + CLI

detect → calculate → decide のパイプライン。
CLIとしても動作する。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Optional

from ugh_calculator import calculate
from detector import detect
from decider import decide


def audit(question_id: str, response_text: str, question_meta: dict) -> dict:
    """detect → calculate → decide のパイプライン

    全計算が決定的。同じ入力なら同じ出力。
    embedding/LLM呼び出しゼロ。
    """
    evidence = detect(question_id, response_text, question_meta)
    state = calculate(evidence)
    result = decide(state, evidence)

    return {
        "evidence": asdict(evidence),
        "state": asdict(state),
        "policy": result["policy"],
        "budget": result["budget"],
    }


def _load_question_meta(data_path: str, question_id: str) -> Optional[dict]:
    """JONLファイルから question_id に一致するメタデータを取得する"""
    with open(data_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("id") == question_id:
                return record
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="UGH Audit Engine — AI回答の意味論的監査",
    )
    parser.add_argument(
        "--question-id",
        required=True,
        help="問題ID（例: q001）",
    )
    parser.add_argument(
        "--response",
        default=None,
        help="AIの回答テキスト（省略時はstdinから読み取り）",
    )
    parser.add_argument(
        "--data",
        required=True,
        help="メタデータJSONLファイルのパス",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="JSON出力を整形する",
    )

    args = parser.parse_args()

    # メタデータの読み込み
    question_meta = _load_question_meta(args.data, args.question_id)
    if question_meta is None:
        print(
            f"Error: question_id '{args.question_id}' not found in {args.data}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 回答テキストの取得
    if args.response is not None:
        response_text = args.response
    else:
        response_text = sys.stdin.read().strip()

    if not response_text:
        print("Error: response text is empty", file=sys.stderr)
        sys.exit(1)

    # 監査実行
    result = audit(args.question_id, response_text, question_meta)

    # JSON出力
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))


if __name__ == "__main__":
    main()
