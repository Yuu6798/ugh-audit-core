"""Deployment preparation uses temporary data only and never contacts a host."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

import pytest

from ugh_audit import deployment, invites
from ugh_audit.storage.audit_db import AuditDB
from ugh_audit.usage_limits import UsageLimiter


TOKEN = "offline_deployment_test_" + "a" * 40
REGISTRY = {"users": [{"user_id": "alice", "enabled": True,
                       "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}]}


def _legacy(path, *, backfill=False, connection=None):
    conn = connection or sqlite3.connect(path)
    try:
        conn.execute("""CREATE TABLE audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '',
            response TEXT NOT NULL DEFAULT '', reference TEXT,
            S REAL NOT NULL, C REAL NOT NULL, delta_e REAL NOT NULL,
            quality_score REAL NOT NULL, verdict TEXT NOT NULL DEFAULT '',
            f1 REAL, f2 REAL, f3 REAL, f4 REAL, hit_rate TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, original_blob BLOB
        )""")
        conn.execute("""INSERT INTO audit_runs
            (id, session_id, response, S, C, delta_e, quality_score, verdict, created_at, original_blob)
            VALUES (7, 'legacy', 'private legacy answer', .75, .5, .2, ?, 'rewrite',
                    '2026-01-01T00:00:00Z', ?)""", (5 if backfill else 4.2, b"\x00original\xff"))
        conn.commit()
    finally:
        if connection is None:
            conn.close()


@pytest.fixture
def environment(tmp_path):
    audit = tmp_path / "audit.db"
    _legacy(audit)
    registry = tmp_path / "invites.json"
    registry.write_text(json.dumps({"users": [{**REGISTRY["users"][0], "enabled": False}]}))
    return {"UGH_AUDIT_DB": str(audit), "UGH_INVITE_FILE": str(registry),
            "UGH_USAGE_DB": str(tmp_path / "usage.db"), "UGH_INVITES_JSON": json.dumps(REGISTRY)}


def _original_rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT id, response, original_blob, quality_score, verdict FROM audit_runs").fetchall()


def test_expanded_registry_size_is_rejected_before_database_changes(environment):
    config = {"users": [{"user_id": "a" * 64, "token_sha256": f"{i:064x}",
                         "enabled": True, "expires_at": 10 ** 40} for i in range(1000)]}
    raw = json.dumps(config, separators=(",", ":"))
    assert len(raw.encode()) <= 262144 < len(json.dumps(config, indent=2).encode())
    audit = Path(environment["UGH_AUDIT_DB"])
    registry = Path(environment["UGH_INVITE_FILE"])
    before_audit, before_registry = audit.read_bytes(), registry.read_bytes()
    environment["UGH_INVITES_JSON"] = raw
    with pytest.raises(deployment.DeploymentError, match="invite_configuration_invalid"):
        deployment.prepare_deployment(environment)
    assert audit.read_bytes() == before_audit
    assert registry.read_bytes() == before_registry
    assert not Path(environment["UGH_USAGE_DB"]).exists()


def test_backup_migration_preserves_original_fields_and_null_owner_once(environment, capsys):
    audit = Path(environment["UGH_AUDIT_DB"])
    original = _original_rows(audit)
    receipt = deployment.prepare_deployment(environment)
    assert receipt["event"] == "deployment_prepared"
    assert receipt["audit_migration"] == "migrated"
    assert receipt["audit_rows"] == 1
    assert receipt["integrity"] == "ok"
    assert receipt["usage_state"] == "initialized"
    assert receipt["invite_source"] == "environment"
    backup, = receipt["backups"]
    assert _original_rows(backup) == original == _original_rows(audit)
    with sqlite3.connect(audit) as conn:
        assert conn.execute("SELECT owner_id FROM audit_runs").fetchall() == [(None,)]
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    with sqlite3.connect(backup) as conn:
        assert "owner_id" not in {row[1] for row in conn.execute("PRAGMA table_info(audit_runs)")}
    limiter = UsageLimiter(Path(environment["UGH_USAGE_DB"]))
    limiter.check_request("alice")
    limiter.acquire_audit("alice").release()
    again = deployment.prepare_deployment(environment)
    assert again["audit_migration"] == "unchanged"
    assert again["backups"] == []
    assert again["usage_state"] == "preserved"
    assert list((audit.parent / "deployment-backups").glob("*.sqlite")) == [Path(backup)]
    with sqlite3.connect(environment["UGH_USAGE_DB"]) as conn:
        assert conn.execute("SELECT SUM(attempts) FROM beta_daily_audits").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM beta_requests").fetchone() == (1,)
    printed = capsys.readouterr().out
    assert TOKEN not in printed
    assert REGISTRY["users"][0]["token_sha256"] not in printed
    assert "private legacy answer" not in printed
    assert json.loads(printed)["event"] == "deployment_backup_created"


@pytest.mark.parametrize("raw", ["", "broken", "null", '{"users":[]}',
    '{"users":[],"users":[]}', json.dumps({**REGISTRY, "unexpected": True}),
    json.dumps({"users": [{**REGISTRY["users"][0], "token": TOKEN}]}),
    json.dumps({"users": [{**REGISTRY["users"][0], "expires_at": True}]}),
    json.dumps({"users": [REGISTRY["users"][0]] * 2}), " " * 262145])
def test_invalid_environment_registry_preserves_all_existing_data(environment, raw):
    paths = [Path(environment[key]) for key in ("UGH_AUDIT_DB", "UGH_INVITE_FILE")]
    original = [path.read_bytes() for path in paths]
    environment["UGH_INVITES_JSON"] = raw
    with pytest.raises(deployment.DeploymentError, match="invite_configuration_invalid"):
        deployment.prepare_deployment(environment)
    assert [path.read_bytes() for path in paths] == original
    assert not paths[0].parent.joinpath("deployment-backups").exists()


def test_environment_reapplied_after_cli_change_and_file_is_private(environment, monkeypatch):
    from ugh_audit.beta_access import authenticate

    deployment.prepare_deployment(environment)
    registry = Path(environment["UGH_INVITE_FILE"])
    invites.revoke_invites(registry, "alice")
    assert json.loads(registry.read_text())["users"][0]["enabled"] is False
    deployment.prepare_deployment(environment)
    assert json.loads(registry.read_text()) == REGISTRY
    monkeypatch.setenv("UGH_ACCESS_MODE", "invite")
    monkeypatch.setenv("UGH_INVITE_FILE", str(registry))
    assert authenticate([(b"authorization", ("Bearer " + TOKEN).encode())]).owner_id == "alice"
    if os.name != "nt":
        assert stat.S_IMODE(registry.stat().st_mode) == 0o600


def test_missing_audit_requires_explicit_initialization(environment):
    environment["UGH_AUDIT_DB"] = str(Path(environment["UGH_AUDIT_DB"]).with_name("new.db"))
    registry = Path(environment["UGH_INVITE_FILE"])
    before = registry.read_bytes()
    with pytest.raises(deployment.DeploymentError, match="existing_audit_database_required"):
        deployment.prepare_deployment(environment)
    assert registry.read_bytes() == before
    assert not Path(environment["UGH_AUDIT_DB"]).exists()
    environment["UGH_ALLOW_NEW_AUDIT_DB"] = "1"
    receipt = deployment.prepare_deployment(environment)
    assert receipt["audit_migration"] == "initialized"
    assert receipt["audit_rows"] == 0
    assert receipt["backups"] == []


@pytest.mark.parametrize("existing", [b"", b"not a sqlite database"])
def test_empty_or_corrupt_existing_file_is_never_reinitialized(environment, existing):
    path = Path(environment["UGH_AUDIT_DB"])
    path.write_bytes(existing)
    environment["UGH_ALLOW_NEW_AUDIT_DB"] = "1"
    with pytest.raises(deployment.DeploymentError):
        deployment.prepare_deployment(environment)
    assert path.read_bytes() == existing


def test_backfill_change_is_detected_on_copy_before_touching_original(tmp_path):
    audit = tmp_path / "audit.db"
    _legacy(audit, backfill=True)
    original = audit.read_bytes()
    with pytest.raises(deployment.DeploymentError, match="audit_rows_changed_during_migration"):
        deployment.prepare_deployment({"UGH_AUDIT_DB": str(audit), "UGH_INVITES_JSON": json.dumps(REGISTRY)})
    assert audit.read_bytes() == original
    assert not (tmp_path / "invites.json").exists()
    assert len(list((tmp_path / "deployment-backups").glob("*.sqlite"))) == 1


def test_existing_incompatible_usage_ledger_is_not_reset(environment):
    usage = Path(environment["UGH_USAGE_DB"])
    with sqlite3.connect(usage) as conn:
        conn.execute("CREATE TABLE beta_daily_audits(secret_counter INTEGER)")
        conn.execute("INSERT INTO beta_daily_audits VALUES (123)")
    audit = Path(environment["UGH_AUDIT_DB"])
    old_audit, old_usage = audit.read_bytes(), usage.read_bytes()
    with pytest.raises(deployment.DeploymentError, match="usage_database_schema_unsupported"):
        deployment.prepare_deployment(environment)
    assert audit.read_bytes() == old_audit
    assert usage.read_bytes() == old_usage


def test_sqlite_backup_includes_committed_wal_rows(tmp_path):
    audit = tmp_path / "audit.db"
    with sqlite3.connect(audit) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        _legacy(audit, connection=writer)
        assert Path(str(audit) + "-wal").exists()
        receipt = deployment.prepare_deployment({"UGH_AUDIT_DB": str(audit),
                                                 "UGH_INVITES_JSON": json.dumps(REGISTRY)})
        assert _original_rows(receipt["backups"][0]) == _original_rows(audit)


def test_railway_requires_real_mount_and_restricts_all_paths(environment, monkeypatch, tmp_path):
    environment["RAILWAY_PROJECT_ID"] = "test-project"
    with pytest.raises(deployment.DeploymentError, match="persistent_volume_required"):
        deployment.prepare_deployment(environment)
    environment["RAILWAY_VOLUME_MOUNT_PATH"] = str(tmp_path)
    monkeypatch.setattr(deployment, "_is_mount", lambda path: False)
    with pytest.raises(deployment.DeploymentError, match="persistent_volume_not_mounted"):
        deployment.prepare_deployment(environment)
    monkeypatch.setattr(deployment, "_is_mount", lambda path: path == tmp_path)
    for variable in ("UGH_AUDIT_DB", "UGH_USAGE_DB", "UGH_INVITE_FILE"):
        changed = {**environment, variable: str(tmp_path.parent / "outside-file")}
        with pytest.raises(deployment.DeploymentError, match="path_outside_persistent_volume"):
            deployment.prepare_deployment(changed)
    assert deployment.prepare_deployment(environment)["volume_verified"] is True


def test_railway_cannot_start_anonymous_local_mode(environment, monkeypatch, tmp_path):
    environment.update(RAILWAY_VOLUME_MOUNT_PATH=str(tmp_path), UGH_ACCESS_MODE="local")
    monkeypatch.setattr(deployment, "_is_mount", lambda path: True)
    with pytest.raises(deployment.DeploymentError, match="invalid_access_mode"):
        deployment.prepare_deployment(environment)


def test_railway_root_filesystem_is_not_a_persistent_volume(environment, monkeypatch, tmp_path):
    environment["RAILWAY_VOLUME_MOUNT_PATH"] = str(Path(tmp_path.anchor))
    monkeypatch.setattr(deployment, "_is_mount", lambda path: True)
    with pytest.raises(deployment.DeploymentError, match="persistent_volume_not_mounted"):
        deployment.prepare_deployment(environment)


def test_invalid_limits_do_not_change_registry_or_database(environment):
    before = Path(environment["UGH_AUDIT_DB"]).read_bytes()
    environment["UGH_AUDITS_PER_DAY"] = "0"
    with pytest.raises(deployment.DeploymentError):
        deployment.prepare_deployment(environment)
    assert Path(environment["UGH_AUDIT_DB"]).read_bytes() == before


@pytest.mark.parametrize("variable,other,suffix", [
    ("UGH_INVITE_FILE", "UGH_AUDIT_DB", "-wal"),
    ("UGH_INVITE_FILE", "UGH_AUDIT_DB", "-shm"),
    ("UGH_INVITE_FILE", "UGH_AUDIT_DB", "-journal"),
    ("UGH_INVITE_FILE", "UGH_USAGE_DB", "-wal"),
    ("UGH_USAGE_DB", "UGH_AUDIT_DB", "-wal"),
    ("UGH_USAGE_DB", "UGH_INVITE_FILE", ".lock"),
])
def test_sqlite_sidecars_and_registry_lock_cannot_overlap(environment, variable, other, suffix):
    original = Path(environment["UGH_AUDIT_DB"]).read_bytes()
    environment[variable] = environment[other] + suffix
    with pytest.raises(deployment.DeploymentError, match="deployment_paths_overlap"):
        deployment.prepare_deployment(environment)
    assert Path(environment["UGH_AUDIT_DB"]).read_bytes() == original


def test_start_receipt_and_exec_do_not_expose_credentials(environment, monkeypatch, capsys):
    for name in list(os.environ):
        if name.startswith(("UGH_", "RAILWAY_")):
            monkeypatch.delenv(name)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PORT", "8765")
    calls = []
    monkeypatch.setattr(deployment.os, "execvp", lambda executable, arguments: calls.append((executable, arguments)))
    assert deployment.main(["start"]) == 0
    assert calls == [(sys.executable, [sys.executable, "-m", "uvicorn", "ugh_audit.server:app",
                                      "--host", "0.0.0.0", "--port", "8765"])]
    output = capsys.readouterr()
    records = [json.loads(line) for line in output.out.splitlines()]
    assert records[-1]["event"] == "deployment_prepared"
    assert TOKEN not in output.out + output.err
    assert REGISTRY["users"][0]["token_sha256"] not in output.out + output.err
    assert "private legacy answer" not in output.out + output.err


def test_failed_start_emits_only_fixed_error_code(environment, monkeypatch, capsys):
    # Reach preparation through a valid isolated path and lifetime lock, not an
    # incidental home directory or deployment configuration on the test host.
    for name in list(os.environ):
        if name.startswith(("UGH_", "RAILWAY_")):
            monkeypatch.delenv(name)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PORT", "8765")
    calls = []

    def failing_preparation():
        calls.append(True)
        raise OSError("private-path-and-credential-must-not-be-printed")

    monkeypatch.setattr(deployment, "prepare_deployment", failing_preparation)
    assert deployment.main(["start"]) == 1
    assert calls == [True]
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"event": "deployment_failed", "code": "deployment_start_failed"}


@pytest.mark.parametrize("invalid_schema", [False, True])
def test_audit_initializer_closes_connection_after_commit_or_rollback(tmp_path, invalid_schema):
    connections = []

    class ObservedDB(AuditDB):
        def _conn(self):
            connection = super()._conn()
            connections.append(connection)
            return connection

    path = tmp_path / "initializer.db"
    if invalid_schema:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE audit_runs(unexpected_column TEXT)")
        with pytest.raises(sqlite3.Error):
            ObservedDB(path)
    else:
        ObservedDB(path)
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_runs)")}
        assert (columns == {"unexpected_column"}) if invalid_schema else ("owner_id" in columns)


@pytest.mark.parametrize("operation", ["issue", "revoke"])
@pytest.mark.parametrize("environment_value", ["", json.dumps(REGISTRY)])
def test_invite_cli_refuses_environment_managed_registry(
        environment, monkeypatch, capsys, operation, environment_value):
    registry = Path(environment["UGH_INVITE_FILE"])
    before = registry.read_bytes()
    token_path = registry.with_name("new.token")
    monkeypatch.setenv("UGH_INVITES_JSON", environment_value)
    arguments = [operation, "--file", str(registry), "--user", "alice"]
    if operation == "issue":
        arguments += ["--token-out", str(token_path)]
    with pytest.raises(SystemExit) as failure:
        invites.main(arguments)
    assert failure.value.code == 1
    assert registry.read_bytes() == before
    assert not token_path.exists()
    captured = capsys.readouterr()
    assert "Registry is managed by UGH_INVITES_JSON" in captured.err
    assert "update the environment registry and redeploy" in captured.err
    assert TOKEN not in captured.err


@pytest.mark.parametrize("already_initialized", [False, True])
@pytest.mark.parametrize("populated", [False, True])
def test_v02_audit_schema_can_deploy_without_rewriting_existing_fields(
        tmp_path, already_initialized, populated):
    path = tmp_path / "legacy-v02.db"
    with sqlite3.connect(path) as conn:
        # The v0.2 schema supported by test_storage.test_migration_from_v02_schema.
        conn.execute("""CREATE TABLE audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            model_id TEXT NOT NULL DEFAULT 'unknown', question TEXT NOT NULL,
            response TEXT NOT NULL, reference TEXT, por REAL NOT NULL,
            por_fired INTEGER NOT NULL, delta_e REAL NOT NULL,
            grv_json TEXT NOT NULL DEFAULT '{}', meaning_drift TEXT NOT NULL,
            created_at TEXT NOT NULL, original_blob BLOB
        )""")
        if populated:
            conn.executemany("""INSERT INTO audit_runs
                (session_id, model_id, question, response, por, por_fired,
                 delta_e, meaning_drift, created_at, original_blob)
                VALUES ('shared', 'legacy-model', 'question', ?, .85, 1, ?,
                        'original meaning', '2026-01-01T00:00:00Z', ?)""",
                             [(f"legacy answer {index}", delta, b"\x00original\xff")
                              for index, delta in enumerate((0.03, 0.30, 0.0))])
    if already_initialized:
        AuditDB(path)
    with sqlite3.connect(path) as conn:
        original_columns = [row[1] for row in conn.execute("PRAGMA table_info(audit_runs)")]
        original_rows = conn.execute("SELECT * FROM audit_runs ORDER BY id").fetchall()

    environment = {"UGH_AUDIT_DB": str(path), "UGH_INVITES_JSON": json.dumps(REGISTRY)}
    receipt = deployment.prepare_deployment(environment)
    assert receipt["audit_migration"] == ("unchanged" if already_initialized else "migrated")
    assert receipt["audit_rows"] == (3 if populated else 0)
    with sqlite3.connect(path) as conn:
        columns = ", ".join('"' + name + '"' for name in original_columns)
        assert conn.execute(f"SELECT {columns} FROM audit_runs ORDER BY id").fetchall() == original_rows
        assert conn.execute("SELECT COUNT(*) FROM audit_runs WHERE owner_id IS NOT NULL").fetchone() == (0,)
        column_info = {row[1]: row for row in conn.execute("PRAGMA table_info(audit_runs)")}
        assert column_info["verdict"][3] == column_info["hit_rate"][3] == 0
    if not already_initialized:
        backup, = receipt["backups"]
        with sqlite3.connect(backup) as conn:
            assert conn.execute("SELECT * FROM audit_runs ORDER BY id").fetchall() == original_rows
    again = deployment.prepare_deployment(environment)
    assert again["audit_migration"] == "unchanged"
    assert again["backups"] == []


def _make_column_nullable(path, table, column, column_type):
    """Build a valid SQLite file with a weaker constraint, preserving its rows."""
    with sqlite3.connect(path) as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                           (table,)).fetchone()[0]
        replacement = sql.replace(f"{column} {column_type} NOT NULL", f"{column} {column_type}")
        assert replacement != sql
        indexes = [row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,))]
        conn.execute(f'ALTER TABLE "{table}" RENAME TO previous_table')
        conn.execute(replacement)
        conn.execute(f'INSERT INTO "{table}" SELECT * FROM previous_table')
        conn.execute("DROP TABLE previous_table")
        for index in indexes:
            conn.execute(index)


@pytest.mark.parametrize("table,column,column_type", [
    ("beta_requests", "owner_id", "TEXT"),
    ("beta_daily_audits", "attempts", "INTEGER"),
    ("beta_audit_leases", "expires_at", "REAL"),
])
def test_usage_nullability_remains_strict_after_audit_legacy_compatibility(
        environment, table, column, column_type):
    path = Path(environment["UGH_USAGE_DB"])
    limiter = UsageLimiter(path)
    limiter.check_request("alice")
    limiter.acquire_audit("alice").release()
    _make_column_nullable(path, table, column, column_type)
    audit = Path(environment["UGH_AUDIT_DB"])
    original_audit, original_usage = audit.read_bytes(), path.read_bytes()
    with pytest.raises(deployment.DeploymentError, match="usage_database_schema_unsupported"):
        deployment.prepare_deployment(environment)
    assert audit.read_bytes() == original_audit
    assert path.read_bytes() == original_usage
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT SUM(attempts) FROM beta_daily_audits").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM beta_requests").fetchone() == (1,)


@pytest.mark.parametrize("column", ["session_id", "question"])
def test_audit_nullability_exception_does_not_allow_other_columns(environment, column):
    path = Path(environment["UGH_AUDIT_DB"])
    AuditDB(path)
    _make_column_nullable(path, "audit_runs", column, "TEXT")
    original = path.read_bytes()
    with pytest.raises(deployment.DeploymentError, match="audit_database_schema_unsupported"):
        deployment.prepare_deployment(environment)
    assert path.read_bytes() == original
