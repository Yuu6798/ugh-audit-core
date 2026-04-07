"""experiments/meta_cache.py
question_meta のファイルキャッシュ

同一質問に対して毎回 LLM を呼ばないためのキャッシュ層。
キャッシュヒット時は LLM 呼び出しゼロ → 決定性の部分的回復。

キャッシュキー: 質問テキストの SHA256 ハッシュ
保存先: ~/.ugh_audit/meta_cache/ (JSON ファイル)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path.home() / ".ugh_audit" / "meta_cache"


def _cache_dir() -> Path:
    """キャッシュディレクトリを返す（環境変数で上書き可能）"""
    path = os.environ.get("UGH_META_CACHE_DIR")
    if path:
        return Path(path)
    return _DEFAULT_CACHE_DIR


def _prompt_fingerprint() -> str:
    """プロンプトテンプレートのハッシュを返す（改訂時にキャッシュ無効化）"""
    try:
        from .prompts.meta_generation_v1 import SYSTEM_PROMPT
        return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]
    except Exception:
        return "unknown"


def _cache_key(question: str, model: str = "") -> str:
    """質問テキスト + モデル名 + プロンプト版から SHA256 キーを生成"""
    raw = f"{question.strip()}\0{model}\0{_prompt_fingerprint()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get_cached_meta(question: str, model: str = "") -> Optional[dict]:
    """キャッシュから question_meta を取得

    Returns:
        キャッシュヒット時は dict、ミス時は None
    """
    key = _cache_key(question, model)
    cache_file = _cache_dir() / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, encoding="utf-8") as f:
            meta = json.load(f)
        logger.info("meta キャッシュヒット: %s", key[:12])
        return meta
    except (json.JSONDecodeError, OSError):
        logger.warning("キャッシュ読み取り失敗: %s", key[:12])
        return None


def save_cached_meta(question: str, meta: dict, model: str = "") -> None:
    """question_meta をキャッシュに保存"""
    cache_d = _cache_dir()
    cache_d.mkdir(parents=True, exist_ok=True)
    key = _cache_key(question, model)
    cache_file = cache_d / f"{key}.json"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.info("meta キャッシュ保存: %s", key[:12])
    except OSError:
        logger.warning("キャッシュ書き込み失敗: %s", key[:12])


def clear_cache() -> int:
    """キャッシュを全削除。削除した件数を返す。"""
    cache_d = _cache_dir()
    if not cache_d.exists():
        return 0
    count = 0
    for f in cache_d.glob("*.json"):
        f.unlink()
        count += 1
    return count
