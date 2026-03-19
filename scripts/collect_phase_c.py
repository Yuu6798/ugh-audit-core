#!/usr/bin/env python3
"""
collect_phase_c.py — Phase C GPT生回答収集スクリプト

Usage:
    export OPENAI_API_KEY="sk-..."
    python3 scripts/collect_phase_c.py \\
        --input path/to/questions.jsonl \\
        --output ~/.ugh_audit/phase_c_v0/phase_c_raw.jsonl \\
        --model gpt-4o \\
        --temperatures 0.0 0.7 1.0

Notes:
    - 出力ファイルは追記モード（既存レコードがあればスキップ）
    - 生回答は絶対に上書きしない。バージョンディレクトリを分けて保存すること
"""

import argparse
import json
import os
import time
from pathlib import Path


SYSTEM_PROMPT = """あなたはAI・哲学・認識論に関する深い知識を持つAIアシスタントです。
以下の質問に対して、誠実かつ深く回答してください。
表面的な安全文句や一般論を避け、問いの核心に踏み込んでください。"""


def load_questions(path: str) -> list[dict]:
    questions = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    return questions


def load_existing_ids(output_path: Path) -> set[tuple]:
    """既存レコードの (id, temperature) セットを返す（再実行時スキップ用）"""
    existing = set()
    if output_path.exists():
        with open(output_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    existing.add((r["id"], r["temperature"]))
    return existing


def collect(
    questions: list[dict],
    output_path: Path,
    model: str,
    temperatures: list[float],
    sleep_sec: float = 0.3,
) -> None:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    existing = load_existing_ids(output_path)

    total = len(questions) * len(temperatures)
    count = 0
    errors = 0
    skipped = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "a") as out:
        for q in questions:
            for temp in temperatures:
                count += 1
                key = (q["id"], temp)

                if key in existing:
                    skipped += 1
                    continue

                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": q["question"]},
                        ],
                        temperature=temp,
                        max_tokens=800,
                    )
                    record = {
                        "id": q["id"],
                        "category": q["category"],
                        "role": q["role"],
                        "difficulty": q["difficulty"],
                        "temperature": temp,
                        "question": q["question"],
                        "response": resp.choices[0].message.content,
                        "reference": q["reference"],
                        "reference_core": q.get("reference_core", ""),
                        "trap_type": q.get("trap_type", ""),
                        "requires_manual_review": q.get("requires_manual_review", False),
                        "model": model,
                        "usage": {
                            "prompt_tokens": resp.usage.prompt_tokens,
                            "completion_tokens": resp.usage.completion_tokens,
                            "total_tokens": resp.usage.total_tokens,
                        },
                    }
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()

                    if count % 10 == 0:
                        print(f"[{count}/{total}] {q['id']} temp={temp} ✓", flush=True)
                    else:
                        print(f"  {q['id']} temp={temp} ✓", flush=True)

                    time.sleep(sleep_sec)

                except Exception as e:
                    errors += 1
                    print(f"  ERROR {q['id']} temp={temp}: {e}", flush=True)
                    time.sleep(2)

    collected = count - skipped - errors
    print(f"\n完了: 収集={collected}件 / スキップ={skipped}件 / エラー={errors}件")
    print(f"出力: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase C GPT生回答収集")
    parser.add_argument("--input", required=True, help="質問JSONLファイルパス")
    parser.add_argument("--output", required=True, help="出力JSONLファイルパス")
    parser.add_argument("--model", default="gpt-4o", help="使用モデル")
    parser.add_argument(
        "--temperatures", nargs="+", type=float, default=[0.0, 0.7, 1.0],
        help="温度リスト（スペース区切り）"
    )
    parser.add_argument("--sleep", type=float, default=0.3, help="API呼び出し間隔(秒)")
    args = parser.parse_args()

    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("ERROR: OPENAI_API_KEY 環境変数が設定されていません")

    questions = load_questions(args.input)
    print(f"問題数: {len(questions)}")
    print(f"温度: {args.temperatures}")
    print(f"総呼び出し数（上限）: {len(questions) * len(args.temperatures)}")

    collect(
        questions=questions,
        output_path=Path(args.output),
        model=args.model,
        temperatures=args.temperatures,
        sleep_sec=args.sleep,
    )


if __name__ == "__main__":
    main()
