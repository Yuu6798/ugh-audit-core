"""
ugh_audit/storage/audit_db.py
監査結果の SQLite 永続化レイヤー（パイプライン A 対応）
"""
from __future__ import annotations

from contextlib import closing

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = Path.home() / ".ugh_audit" / "audit.db"


def _validate_owner_id(owner_id: Optional[str]) -> None:
    """None is trusted unscoped access; an invalid identity must never imply it."""
    if owner_id is not None and (not isinstance(owner_id, str) or not owner_id.strip()):
        raise ValueError("owner_id must be a non-empty string or None")


def guard_history_row(row: dict) -> dict:
    """Recheck saved assessments without altering historical database records."""
    from ..semantic_matching import apply_semantic_gate, DECISION_SCOPE, METRICS_SCOPE

    decoded = dict(row)
    for name in ("semantic_matching", "legacy_metrics", "question_meta", "structural_gate"):
        value = decoded.get(name)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                value = None
        decoded[name] = value if isinstance(value, dict) else None
    legacy_missing = decoded["legacy_metrics"] is None
    if legacy_missing:
        decoded["legacy_metrics"] = {name: row.get(name) for name in (
            "S", "C", "delta_e", "quality_score", "verdict", "mode", "hit_rate",
            "is_reliable", "hit_sources", "grv", "verdict_advisory", "advisory_flags",
        )}
    decoded.setdefault(
        "mode", "computed" if decoded.get("delta_e") is not None else "degraded",
    )
    decoded.setdefault("is_reliable", decoded["mode"] == "computed")
    if not isinstance(decoded.get("structural_gate"), dict):
        values = {name: decoded.get(name) for name in ("f1", "f2", "f3", "f4")}
        known = all(type(value) in (int, float) and 0 <= value <= 1
                    and math.isfinite(value) for value in values.values())
        if known:
            worst = max(values.values())
            gate = "fail" if worst >= 1 else "pass" if worst == 0 else "warn"
            primary = "none" if worst == 0 else max(values, key=values.get)
            decoded["structural_gate"] = {**values, "gate_verdict": gate, "primary_fail": primary}
        else:
            decoded["structural_gate"] = {**values, "gate_verdict": "incomplete"}
    if legacy_missing:
        decoded["legacy_metrics"]["structural_gate"] = dict(decoded["structural_gate"])
    context = (decoded["question_meta"] if isinstance(decoded.get("question_meta"), dict)
               else decoded.get("assessment_context"))
    source_schema = decoded.get("source_schema_version", decoded.get("schema_version", ""))
    source_schema = source_schema if isinstance(source_schema, str) else ""
    assessment = decoded.get("semantic_matching")
    # A valid pending assessment is degraded and may retain confirmed rows.
    # The semantic gate below still rejects malformed or forged pending counts.
    pending = (isinstance(assessment, dict)
               and ((type(assessment.get("unknown_count")) is int and assessment["unknown_count"] > 0)
                    or (type(assessment.get("requirements_total")) is int
                        and assessment["requirements_total"] == 0)))
    explicit_failure = decoded.get("mode") == "degraded" or decoded.get("verdict") == "degraded"
    eligible = (
        decoded.get("schema_version") == "4.0.0"
        and source_schema == "4.0.0"
        and decoded.get("decision_scope") == DECISION_SCOPE
        and decoded.get("metrics_scope") == METRICS_SCOPE
        and (not explicit_failure or pending)
        and decoded.get("metadata_source") != "fallback"
        and isinstance(context, dict) and bool(context)
        and isinstance(decoded.get("response"), str)
    )
    projected = apply_semantic_gate(
        decoded, decoded.get("semantic_matching"), response=decoded.get("response"),
        question_meta=context, eligible=eligible,
        ineligible_reason="historical_context_or_contract_unverified",
    )
    projected["schema_version"] = "4.0.0"
    projected["source_schema_version"] = source_schema
    projected["assessment_context"] = context if isinstance(context, dict) else None
    return projected


def _sql_segments(body: str) -> list[str]:
    """Split SQL column definitions without touching quoted text or CHECK clauses."""
    parts, start, depth, quote, i = [], 0, 0, None, 0
    while i < len(body):
        char = body[i]
        if quote:
            end = "]" if quote == "[" else quote
            if char == end:
                if i + 1 < len(body) and body[i + 1] == end:
                    i += 1
                else:
                    quote = None
        elif char in "'\"`[":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
        i += 1
    return [*parts, body[start:]]


def _nullable_metrics_sql(sql: str, table_name: str) -> str:
    """Preserve the complete table definition, changing only metric nullability."""
    opening, closing = sql.index("("), sql.rfind(")")
    definitions = _sql_segments(sql[opening + 1:closing])
    for pos, definition in enumerate(definitions):
        match = re.match(r'\s*(?:"([^"]+)"|`([^`]+)`|\[([^]]+)\]|(\w+))', definition)
        if not match or next((g for g in match.groups() if g is not None), "").lower() not in {
            "s", "c", "delta_e", "quality_score", "f1", "f2", "f3", "f4",
        }:
            continue
        # Tokens inside expressions and quoted strings are deliberately excluded.
        tokens, depth, quote, i = [], 0, None, match.end()
        while i < len(definition):
            char = definition[i]
            if quote:
                end = "]" if quote == "[" else quote
                if char == end:
                    if i + 1 < len(definition) and definition[i + 1] == end:
                        i += 1
                    else:
                        quote = None
            elif char in "'\"`[":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0 and (char.isalpha() or char == "_"):
                token = re.match(r"\w+", definition[i:]).group()
                tokens.append((token.upper(), i, i + len(token)))
                i += len(token) - 1
            i += 1
        spans = []
        for index, (left, right) in enumerate(zip(tokens, tokens[1:])):
            if (left[0] != "NOT" or right[0] != "NULL"
                    or definition[left[2]:right[1]].strip()):
                continue
            start, end = left[1], right[2]
            following = tokens[index + 2:index + 5]
            if (len(following) == 3 and [token[0] for token in following[:2]] == ["ON", "CONFLICT"]
                    and following[2][0] in {"ROLLBACK", "ABORT", "FAIL", "IGNORE", "REPLACE"}):
                end = following[2][2]
            named = re.search(
                r'\bCONSTRAINT\s+(?:"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^]]+\]|\w+)\s*$',
                definition[:start], re.IGNORECASE,
            )
            if named:
                start = named.start()
            spans.append((start, end))
        for start, end in reversed(spans):
            definition = definition[:start] + definition[end:]
        definitions[pos] = definition
    return f'CREATE TABLE "{table_name}" (' + ",".join(definitions) + sql[closing:]


def _migrate_nullable_metrics(conn: sqlite3.Connection) -> None:
    """Atomically rebuild old NOT NULL metric columns, retaining IDs and schema objects."""
    info = conn.execute("PRAGMA table_info(audit_runs)").fetchall()
    if not any(row[1].lower() in {"s", "c", "delta_e", "quality_score", "f1", "f2", "f3", "f4"} and row[3]
               for row in info):
        return
    original_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='audit_runs'",
    ).fetchone()[0]
    objects = conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='audit_runs' "
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL",
    ).fetchall()
    sequence = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='audit_runs'",
    ).fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'",
    ).fetchone() else None
    temporary = "audit_runs_nullable_v4"
    legacy_alter = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("SAVEPOINT nullable_metrics_v4")
    try:
        conn.execute(_nullable_metrics_sql(original_sql, temporary))
        columns = [row[1] for row in conn.execute("PRAGMA table_xinfo(audit_runs)") if row[6] == 0]
        quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
        conn.execute(f'INSERT INTO "{temporary}" ({quoted}) SELECT {quoted} FROM audit_runs')
        conn.execute("DROP TABLE audit_runs")
        # Existing views and foreign keys must continue to name audit_runs.
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute(f'ALTER TABLE "{temporary}" RENAME TO audit_runs')
        for (sql,) in objects:
            conn.execute(sql)
        if sequence:
            conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='audit_runs'", sequence)
        conn.execute("RELEASE SAVEPOINT nullable_metrics_v4")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT nullable_metrics_v4")
        conn.execute("RELEASE SAVEPOINT nullable_metrics_v4")
        raise
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={int(legacy_alter)}")


class AuditDB:
    """
    監査結果の SQLite 永続化

    スキーマ設計:
        - audit_runs: 全スコアリング結果を蓄積
        - owner_id を指定した読み取りはその所有者の行だけを対象とする
        - owner_id=None は既存の信頼されたローカル・管理者向け全件アクセス
        - 同一所有者内の session_id で会話単位の分析が可能
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with closing(self._conn()) as conn, conn:
            # sqlite3's context manager does not begin a transaction for DDL.
            # Keep schema additions, rebuilding and backfills atomic together.
            # Serialize initialization before schema reads. A deferred transaction
            # lets concurrent workers both read, then fail to upgrade their locks.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL DEFAULT '',
                    question        TEXT NOT NULL DEFAULT '',
                    response        TEXT NOT NULL DEFAULT '',
                    reference       TEXT,
                    S               REAL,
                    C               REAL,
                    delta_e         REAL,
                    quality_score   REAL,
                    verdict         TEXT NOT NULL DEFAULT '',
                    f1              REAL,
                    f2              REAL,
                    f3              REAL,
                    f4              REAL,
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
                ("semantic_matching", "TEXT DEFAULT ''"),
                ("requirement_verdict", "TEXT DEFAULT 'undetermined'"),
                ("requires_review", "INTEGER DEFAULT 1"),
                ("legacy_metrics", "TEXT DEFAULT ''"),
                ("schema_version", "TEXT DEFAULT ''"),
                ("decision_scope", "TEXT DEFAULT ''"),
                ("metrics_scope", "TEXT DEFAULT ''"),
                ("question_meta", "TEXT DEFAULT ''"),
                ("structural_gate", "TEXT DEFAULT ''"),
                ("mode", "TEXT DEFAULT 'computed'"),
                ("is_reliable", "INTEGER DEFAULT 0"),
                ("owner_id", "TEXT DEFAULT NULL"),
            ]
            existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_runs)")}
            for col_name, col_type in _new_columns:
                if col_name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE audit_runs ADD COLUMN {col_name} {col_type}"
                    )
            _migrate_nullable_metrics(conn)
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
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_owner_created_at
                ON audit_runs(owner_id, created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_owner_session
                ON audit_runs(owner_id, session_id)
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
        S: Optional[float] = None,
        C: Optional[float] = None,
        delta_e: Optional[float] = None,
        quality_score: Optional[float] = None,
        verdict: str = "",
        f1: Optional[float] = None,
        f2: Optional[float] = None,
        f3: Optional[float] = None,
        f4: Optional[float] = None,
        hit_rate: str = "",
        metadata_source: str = "inline",
        generated_meta: str = "",
        hit_sources: str = "",
        retry_of: Optional[int] = None,
        semantic_matching: Optional[dict] = None,
        requirement_verdict: str = "undetermined",
        requires_review: bool = True,
        legacy_metrics: Optional[dict] = None,
        schema_version: str = "",
        decision_scope: str = "",
        metrics_scope: str = "",
        question_meta: Optional[dict] = None,
        structural_gate: Optional[dict] = None,
        mode: str = "computed",
        is_reliable: bool = False,
        owner_id: Optional[str] = None,
    ) -> int:
        """監査結果を保存してrow idを返す"""
        _validate_owner_id(owner_id)
        if owner_id is not None and retry_of is not None and (
            type(retry_of) is not int or retry_of <= 0
        ):
            raise ValueError("Invalid retry reference")
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "owner_id": owner_id,
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
            "semantic_matching": json.dumps(semantic_matching, ensure_ascii=False),
            "requirement_verdict": requirement_verdict,
            "requires_review": int(requires_review),
            "legacy_metrics": json.dumps(legacy_metrics, ensure_ascii=False),
            "schema_version": schema_version,
            "decision_scope": decision_scope,
            "metrics_scope": metrics_scope,
            "question_meta": json.dumps(question_meta, ensure_ascii=False),
            "structural_gate": json.dumps(structural_gate, ensure_ascii=False),
            "mode": mode,
            "is_reliable": int(is_reliable),
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
            if owner_id is not None and retry_of is not None:
                # Check and insert under one writer lock. A concurrent owner
                # change/deletion cannot occur between authorization and save.
                conn.execute("BEGIN IMMEDIATE")
                previous = conn.execute(
                    "SELECT 1 FROM audit_runs WHERE id = ? AND owner_id = ?",
                    (retry_of, owner_id),
                ).fetchone()
                if previous is None:
                    raise ValueError("Invalid retry reference")
            cursor = conn.execute(
                f"INSERT INTO audit_runs ({col_names}) VALUES ({placeholders})",
                values,
            )
            return cursor.lastrowid

    def get_by_id(self, audit_id: int, *, owner_id: Optional[str] = None) -> Optional[dict]:
        """ID指定で1件取得"""
        _validate_owner_id(owner_id)
        owner_filter = " AND owner_id = ?" if owner_id is not None else ""
        values = (audit_id, owner_id) if owner_id is not None else (audit_id,)
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM audit_runs WHERE id = ?" + owner_filter, values,
            ).fetchone()
            return guard_history_row(dict(row)) if row else None

    def list_recent(self, limit: int = 20, *, owner_id: Optional[str] = None) -> List[dict]:
        """最近のスコアリング結果を返す"""
        _validate_owner_id(owner_id)
        owner_filter = " WHERE owner_id = ?" if owner_id is not None else ""
        values = (owner_id, limit) if owner_id is not None else (limit,)
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM audit_runs" + owner_filter + " ORDER BY created_at DESC LIMIT ?",
                values,
            ).fetchall()
            return [guard_history_row(dict(r)) for r in rows]

    def session_summary(self, session_id: str, *, owner_id: Optional[str] = None) -> dict:
        """セッション単位の集計サマリー"""
        from ..semantic_matching import DECISION_SCOPE, METRICS_SCOPE

        _validate_owner_id(owner_id)
        owner_filter = " AND owner_id = ?" if owner_id is not None else ""
        values = (session_id, owner_id) if owner_id is not None else (session_id,)
        with self._conn() as conn:
            stored = conn.execute(
                "SELECT * FROM audit_runs WHERE session_id = ?" + owner_filter, values,
            )
            columns = [item[0] for item in stored.description]
            guarded = [guard_history_row(dict(zip(columns, values))) for values in stored]
            def legacy_values(name):
                return [value for r in guarded for value in (r["legacy_metrics"].get(name),)
                        if type(value) in (int, float) and 0 <= value <= 5 and math.isfinite(value)]

            distances = legacy_values("delta_e")
            qualities = legacy_values("quality_score")
            legacy = {
                "session_id": session_id, "total": len(guarded),
                "avg_delta_e": round(sum(distances) / len(distances), 4) if distances else None,
                "min_delta_e": round(min(distances), 4) if distances else None,
                "max_delta_e": round(max(distances), 4) if distances else None,
                "avg_quality_score": round(sum(qualities) / len(qualities), 4) if qualities else None,
            }
            verified = sum(
                r.get("mode") in ("computed", "computed_ai_draft")
                and r.get("requirement_verdict") in ("complete", "incomplete")
                for r in guarded
            )
            return {
                "session_id": session_id,
                "schema_version": "4.0.0",
                "decision_scope": DECISION_SCOPE,
                "metrics_scope": METRICS_SCOPE,
                "total": len(guarded),
                "verified_total": verified,
                "unverified_total": len(guarded) - verified,
                "avg_S": None, "avg_delta_e": None, "min_delta_e": None,
                "max_delta_e": None, "avg_quality_score": None,
                "legacy_summary": legacy,
            }

    def drift_history(self, limit: int = 100, *, owner_id: Optional[str] = None) -> List[dict]:
        """要件充足率と判定の時系列（従来指標は legacy_metrics）。"""
        _validate_owner_id(owner_id)
        owner_filter = " WHERE owner_id = ?" if owner_id is not None else ""
        values = (owner_id, limit) if owner_id is not None else (limit,)
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM audit_runs" + owner_filter + " ORDER BY created_at ASC LIMIT ?",
                values,
            ).fetchall()
            fields = (
                "created_at", "S", "C", "delta_e", "quality_score", "verdict",
                "semantic_matching", "requirement_verdict", "requires_review", "legacy_metrics",
                "schema_version", "decision_scope", "metrics_scope", "assessment_context",
                "source_schema_version",
                "response", "mode", "is_reliable", "structural_gate", "metadata_source", "hit_rate",
            )
            return [
                {name: value for name, value in guard_history_row(dict(r)).items() if name in fields}
                for r in rows
            ]
