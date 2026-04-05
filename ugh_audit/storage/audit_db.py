"""
ugh_audit/storage/audit_db.py
監査結果の SQLite 永続化レイヤー（パイプライン A 対応）
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = Path.home() / ".ugh_audit" / "audit.db"


class AuditDB:
    """
    監査結果の SQLite 永続化

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
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL DEFAULT '',
                    question        TEXT NOT NULL DEFAULT '',
                    response        TEXT NOT NULL DEFAULT '',
                    reference       TEXT,
                    S               REAL NOT NULL DEFAULT 0.0,
                    C               REAL NOT NULL DEFAULT 0.0,
                    delta_e         REAL NOT NULL DEFAULT 0.0,
                    quality_score   REAL NOT NULL DEFAULT 5.0,
                    verdict         TEXT NOT NULL DEFAULT '',
                    f1              REAL NOT NULL DEFAULT 0.0,
                    f2              REAL NOT NULL DEFAULT 0.0,
                    f3              REAL NOT NULL DEFAULT 0.0,
                    f4              REAL NOT NULL DEFAULT 0.0,
                    hit_rate        TEXT NOT NULL DEFAULT '',
                    created_at      TEXT NOT NULL
                )
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

    def save(
        self,
        *,
        session_id: str = "",
        question: str = "",
        response: str = "",
        reference: Optional[str] = None,
        S: float = 0.0,
        C: float = 0.0,
        delta_e: float = 0.0,
        quality_score: float = 5.0,
        verdict: str = "",
        f1: float = 0.0,
        f2: float = 0.0,
        f3: float = 0.0,
        f4: float = 0.0,
        hit_rate: str = "",
    ) -> int:
        """監査結果を保存してrow idを返す"""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO audit_runs
                    (session_id, question, response, reference,
                     S, C, delta_e, quality_score, verdict,
                     f1, f2, f3, f4, hit_rate, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                question,
                response,
                reference,
                S,
                C,
                delta_e,
                quality_score,
                verdict,
                f1,
                f2,
                f3,
                f4,
                hit_rate,
                now,
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
                    AVG(delta_e)        AS avg_delta_e,
                    MIN(delta_e)        AS min_delta_e,
                    MAX(delta_e)        AS max_delta_e,
                    AVG(quality_score)  AS avg_quality_score
                FROM audit_runs
                WHERE session_id = ?
            """, (session_id,)).fetchone()
            return {
                "session_id": session_id,
                "total": row[0],
                "avg_delta_e": round(row[1] or 0, 4),
                "min_delta_e": round(row[2] or 0, 4),
                "max_delta_e": round(row[3] or 0, 4),
                "avg_quality_score": round(row[4] or 0, 4),
            }

    def drift_history(self, limit: int = 100) -> List[dict]:
        """ΔE時系列データ"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT created_at, S, C, delta_e, quality_score, verdict
                FROM audit_runs
                ORDER BY created_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
