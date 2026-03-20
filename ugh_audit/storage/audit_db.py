"""
ugh_audit/storage/audit_db.py
AuditResult の SQLite 永続化レイヤー
ugh-quantamental の永続化パターンを踏襲
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from ..scorer.models import AuditResult

DEFAULT_DB_PATH = Path.home() / ".ugh_audit" / "audit.db"


class AuditDB:
    """
    AuditResult の SQLite 永続化

    スキーマ設計:
        - audit_runs: 全スコアリング結果を蓄積
        - 同一 session_id でグループ化して会話単位の分析が可能
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    model_id    TEXT NOT NULL DEFAULT 'unknown',
                    question    TEXT NOT NULL,
                    response    TEXT NOT NULL,
                    reference   TEXT,
                    por         REAL NOT NULL,
                    por_fired   INTEGER NOT NULL,
                    delta_e     REAL NOT NULL,
                    delta_e_core    REAL NOT NULL DEFAULT 0.0,
                    delta_e_full    REAL NOT NULL DEFAULT 0.0,
                    delta_e_summary REAL NOT NULL DEFAULT 0.0,
                    grv_json    TEXT NOT NULL DEFAULT '{}',
                    meaning_drift TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )
            """)
            # 既存DBへのマイグレーション（カラム追加）
            for col in ("delta_e_core", "delta_e_full", "delta_e_summary"):
                try:
                    conn.execute(
                        f"ALTER TABLE audit_runs ADD COLUMN {col} REAL DEFAULT 0.0"
                    )
                except sqlite3.OperationalError:
                    continue  # カラムが既に存在する場合はスキップ
            # 既存行の delta_e_full をレガシー delta_e から backfill
            conn.execute("""
                UPDATE audit_runs
                SET delta_e_full = delta_e
                WHERE delta_e_full = 0.0 AND delta_e != 0.0
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session
                ON audit_runs(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at
                ON audit_runs(created_at)
            """)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def save(self, result: AuditResult) -> int:
        """AuditResultを保存してrow idを返す"""
        # delta_e_full が未設定（0.0）の場合はレガシー delta_e から補填
        delta_e_full = result.delta_e_full if result.delta_e_full != 0.0 else result.delta_e
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO audit_runs
                    (session_id, model_id, question, response, reference,
                     por, por_fired, delta_e,
                     delta_e_core, delta_e_full, delta_e_summary,
                     grv_json, meaning_drift, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.session_id,
                result.model_id,
                result.question,
                result.response,
                result.reference,
                result.por,
                int(result.por_fired),
                result.delta_e,
                result.delta_e_core,
                delta_e_full,
                result.delta_e_summary,
                json.dumps(result.grv, ensure_ascii=False),
                result.meaning_drift,
                result.created_at.isoformat(),
            ))
            return cursor.lastrowid

    def list_recent(self, limit: int = 20) -> List[dict]:
        """最近のスコアリング結果を返す"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM audit_runs
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def session_summary(self, session_id: str) -> dict:
        """セッション単位の集計サマリー"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)            AS total,
                    AVG(por)            AS avg_por,
                    SUM(por_fired)      AS fired_count,
                    AVG(delta_e)        AS avg_delta_e,
                    MIN(delta_e)        AS min_delta_e,
                    MAX(delta_e)        AS max_delta_e
                FROM audit_runs
                WHERE session_id = ?
            """, (session_id,)).fetchone()
            return {
                "session_id": session_id,
                "total": row[0],
                "avg_por": round(row[1] or 0, 4),
                "fired_count": row[2],
                "avg_delta_e": round(row[3] or 0, 4),
                "min_delta_e": round(row[4] or 0, 4),
                "max_delta_e": round(row[5] or 0, 4),
                "fire_rate": round((row[2] or 0) / max(row[0], 1), 3),
            }

    def drift_history(self, limit: int = 100) -> List[dict]:
        """ΔE時系列データ（Phase Map生成用）"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT created_at, por, delta_e, por_fired, meaning_drift, model_id
                FROM audit_runs
                ORDER BY created_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
