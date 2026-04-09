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
            # v0.2 → v0.3 マイグレーション: 既存テーブルに新カラムを追加
            _new_columns = [
                ("S", "REAL DEFAULT 0.0"),
                ("C", "REAL DEFAULT 0.0"),
                ("quality_score", "REAL DEFAULT 5.0"),
                ("verdict", "TEXT DEFAULT ''"),
                ("f1", "REAL DEFAULT 0.0"),
                ("f2", "REAL DEFAULT 0.0"),
                ("f3", "REAL DEFAULT 0.0"),
                ("f4", "REAL DEFAULT 0.0"),
                ("hit_rate", "TEXT DEFAULT ''"),
                ("metadata_source", "TEXT DEFAULT 'inline'"),
                ("generated_meta", "TEXT DEFAULT ''"),
                ("hit_sources", "TEXT DEFAULT ''"),
                ("retry_of", "INTEGER DEFAULT NULL"),
            ]
            for col_name, col_type in _new_columns:
                try:
                    conn.execute(
                        f"ALTER TABLE audit_runs ADD COLUMN {col_name} {col_type}"
                    )
                except sqlite3.OperationalError:
                    pass  # カラムが既に存在する場合はスキップ
            # レガシー行の quality_score / verdict を delta_e から backfill
            conn.execute("""
                UPDATE audit_runs
                SET quality_score = MAX(1.0, MIN(5.0, 5.0 - 4.0 * delta_e))
                WHERE quality_score = 5.0 AND delta_e != 0.0
            """)
            conn.execute("""
                UPDATE audit_runs
                SET verdict = CASE
                    WHEN delta_e <= 0.10 THEN 'accept'
                    WHEN delta_e <= 0.25 THEN 'rewrite'
                    ELSE 'regenerate'
                END
                WHERE verdict = ''
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

    def _table_columns(self) -> set:
        """audit_runs テーブルの既存カラム名を返す"""
        with self._conn() as conn:
            cursor = conn.execute("PRAGMA table_info(audit_runs)")
            return {row[1] for row in cursor.fetchall()}

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
        metadata_source: str = "inline",
        generated_meta: str = "",
        hit_sources: str = "",
        retry_of: Optional[int] = None,
    ) -> int:
        """監査結果を保存してrow idを返す"""
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "session_id": session_id,
            "question": question,
            "response": response,
            "reference": reference,
            "S": S,
            "C": C,
            "delta_e": delta_e,
            "quality_score": quality_score,
            "verdict": verdict,
            "f1": f1,
            "f2": f2,
            "f3": f3,
            "f4": f4,
            "hit_rate": hit_rate,
            "metadata_source": metadata_source,
            "generated_meta": generated_meta,
            "hit_sources": hit_sources,
            "retry_of": retry_of,
            "created_at": now,
        }
        # レガシー DB にも NOT NULL カラムのデフォルト値を提供
        existing = self._table_columns()
        if "por" in existing:
            row.setdefault("por", 0.0)
        if "por_fired" in existing:
            row.setdefault("por_fired", 0)
        if "meaning_drift" in existing:
            row.setdefault("meaning_drift", verdict or "")
        if "model_id" in existing:
            row.setdefault("model_id", "pipeline-a")
        if "grv_json" in existing:
            row.setdefault("grv_json", "{}")
        # 実際に存在するカラムのみ INSERT
        cols = [c for c in row if c in existing]
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        values = [row[c] for c in cols]
        with self._conn() as conn:
            cursor = conn.execute(
                f"INSERT INTO audit_runs ({col_names}) VALUES ({placeholders})",
                values,
            )
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
