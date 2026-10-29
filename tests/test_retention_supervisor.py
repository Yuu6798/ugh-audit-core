"""Real subprocess stop/reap/cleanup/restart; no network or provider calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from ugh_audit.retention_supervisor import (
    LOCK_FD_ENV, PARENT_PID_ENV, service_lock, state_path, SupervisorError, validate_managed_child,
)
from ugh_audit.storage.audit_db import AuditDB
from ugh_audit.usage_limits import UsageLimiter


CHILD = r'''
import json, os, signal, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from ugh_audit.retention_supervisor import validate_managed_child
db, marker, ignore = sys.argv[1:]
if os.name == "posix":
    validate_managed_child()
    if ignore == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
connection = sqlite3.connect(db)
connection.execute("INSERT INTO audit_runs(owner_id, response, created_at) VALUES (?, ?, ?)",
                   ("alice", "child-generated-expired-audit", (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()))
connection.commit()
connection.execute("BEGIN IMMEDIATE")
connection.execute("UPDATE audit_runs SET session_id='child-holds-active-transaction'")
Path(marker).write_text(json.dumps({"pid":os.getpid(), "validated":os.name == "posix"}))
while True:
    time.sleep(.03)
'''


RUNNER = r'''
import json, os, signal, sys, time
from datetime import datetime, timezone
from pathlib import Path
from ugh_audit.retention_supervisor import service_lock, supervise, SupervisorError
from ugh_audit.retention import maintain_audits, maintain_usage
db, usage, child, marker, cycles, fail_at, ignore, rollback = sys.argv[1:]
cycles, fail_at = int(cycles), int(fail_at)
environ = dict(os.environ, UGH_AUDIT_DB=db, UGH_USAGE_DB=usage, UGH_RETENTION_AUTOMATION="1")
count, reads = 0, 0
initial = time.time()
def clock():
    global reads
    reads += 1
    if rollback == "1" and reads >= 8:
        return initial - 60
    return time.time()
def clean(env, now):
    global count
    count += 1
    if count == fail_at:
        raise RuntimeError("PRIVATE_EXCEPTION_BODY_MUST_NOT_LEAK")
    receipt = {"audit": maintain_audits(Path(db), older_than_days=29, apply=True,
                                       service_stopped=True, now=now),
               "usage": maintain_usage(Path(usage), apply=True, service_stopped=True, now=now)}
    if cycles and count == cycles:
        signal.raise_signal(signal.SIGTERM)
    return receipt
try:
    with service_lock(Path(db)) as fd:
        result = supervise([sys.executable, child, db, marker, ignore], environ, fd,
                           interval_seconds=2, grace_seconds=.2, poll_seconds=.03,
                           cleaner=clean, clock=clock)
except SupervisorError as error:
    print(json.dumps({"error":str(error)}), flush=True)
    result = 1
sys.exit(result)
'''


@pytest.fixture
def run_files(tmp_path):
    audit, usage = tmp_path / "audit.db", tmp_path / "usage.db"
    db = AuditDB(audit)
    db.save(owner_id="alice", response="initial-expired-audit")
    with sqlite3.connect(audit) as conn:
        conn.execute("UPDATE audit_runs SET created_at=?",
                     ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),))
    UsageLimiter(usage)
    child, runner, marker = tmp_path / "child.py", tmp_path / "runner.py", tmp_path / "running.json"
    child.write_text(CHILD)
    runner.write_text(RUNNER)
    return audit, usage, child, runner, marker


def _start(files, *, cycles=3, fail_at=0, ignore=False, rollback=False):
    audit, usage, child, runner, marker = files
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("UGH_", "RAILWAY_"))}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return subprocess.Popen([sys.executable, str(runner), str(audit), str(usage), str(child),
                             str(marker), str(cycles), str(fail_at), str(int(ignore)),
                             str(int(rollback))], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)


def _finish(process):
    try:
        out, err = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)
        raise
    return [json.loads(line) for line in out.splitlines()], err


def _wait_started(process, marker):
    deadline = time.monotonic() + 10
    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(.03)
    assert marker.exists(), "HTTP child never reached its active SQLite transaction"
    return json.loads(marker.read_text())


def test_cleanup_waits_for_real_child_transaction_release_and_restarts(run_files):
    process = _start(run_files)
    records, err = _finish(process)
    assert process.returncode == 0, (records, err)
    assert [record["event"] for record in records] == [
        "retention_success", "retention_http_started", "retention_http_stopped",
        "retention_success", "retention_http_started", "retention_http_stopped", "retention_success",
    ]
    assert [r["audit"]["deleted_audits"] for r in records if r["event"] == "retention_success"] == [1, 1, 1]
    audit = run_files[0]
    with sqlite3.connect(audit) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_runs").fetchone() == (0,)
    state = json.loads(state_path(audit).read_text())
    assert state["last_success_utc"] < state["next_due_utc"]
    assert "alice" not in json.dumps(state) and "child-generated" not in json.dumps(state)


def test_cleanup_failure_keeps_http_stopped_and_previous_success_receipt(run_files):
    process = _start(run_files, cycles=0, fail_at=2)
    records, err = _finish(process)
    assert process.returncode == 1
    assert [r.get("event") for r in records].count("retention_http_started") == 1
    assert records[-1] == {"error": "retention_maintenance_failed"}
    assert records[-2] == {"event": "retention_http_stopped"}
    assert "PRIVATE_EXCEPTION" not in json.dumps(records) + err
    assert json.loads(state_path(run_files[0]).read_text())["audit"]["deleted_audits"] == 1
    # No managed child retains its SQLite write transaction after failure.
    with sqlite3.connect(run_files[0], timeout=0) as conn:
        conn.execute("BEGIN EXCLUSIVE")


def test_first_cleanup_failure_never_starts_http(run_files):
    process = _start(run_files, cycles=0, fail_at=1)
    records, _ = _finish(process)
    assert process.returncode == 1 and records == [{"error": "retention_maintenance_failed"}]
    assert not run_files[-1].exists() and not state_path(run_files[0]).exists()


def test_live_clock_rollback_stops_http_without_new_cleanup(run_files):
    process = _start(run_files, cycles=0, rollback=True)
    records, _ = _finish(process)
    assert process.returncode == 1
    assert records[-1] == {"error": "retention_clock_moved_backwards"}
    assert [r.get("event") for r in records].count("retention_success") == 1
    with sqlite3.connect(run_files[0], timeout=0) as conn:
        conn.execute("BEGIN EXCLUSIVE")


def test_restart_clock_before_saved_success_refuses_even_first_cleanup(run_files):
    audit = run_files[0]
    future = datetime.now(timezone.utc) + timedelta(days=1)
    state_path(audit).write_text(json.dumps({"schema_version": 1,
        "cycle_started_utc": future.isoformat(), "last_success_utc": future.isoformat(),
        "next_due_utc": (future + timedelta(hours=23)).isoformat()}))
    process = _start(run_files)
    records, _ = _finish(process)
    assert process.returncode == 1 and records == [{"error": "retention_clock_moved_backwards"}]
    assert not run_files[-1].exists()


def test_lifetime_lock_refuses_second_supervisor(run_files):
    with service_lock(run_files[0]):
        process = _start(run_files)
        records, _ = _finish(process)
        assert process.returncode == 1
        assert records == [{"error": "retention_service_already_running"}]


@pytest.mark.skipif(os.name != "posix", reason="Container PID1 and flock are POSIX production paths")
def test_managed_child_accepts_container_supervisor_pid_one(run_files, monkeypatch):
    with service_lock(run_files[0]) as descriptor:
        monkeypatch.setenv("UGH_AUDIT_DB", str(run_files[0]))
        monkeypatch.setenv(LOCK_FD_ENV, str(descriptor))
        monkeypatch.setenv(PARENT_PID_ENV, "1")
        monkeypatch.setattr(os, "getppid", lambda: 1)
        validate_managed_child()


@pytest.mark.skipif(os.name != "posix", reason="Requires inherited flock across exec")
def test_automation_off_exec_keeps_lifetime_lock(run_files):
    audit, _, _, _, marker = run_files
    program = ("import os, sys; from pathlib import Path; "
               "from ugh_audit.retention_supervisor import service_lock; "
               "context=service_lock(Path(sys.argv[1])); context.__enter__(); "
               "os.execvp(sys.executable, [sys.executable, '-c', "
               "\"import sys,time; from pathlib import Path; "
               "Path(sys.argv[1]).write_text('{}'); time.sleep(30)\", sys.argv[2]])")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    process = subprocess.Popen([sys.executable, "-c", program, str(audit), str(marker)], env=env)
    try:
        _wait_started(process, marker)
        with pytest.raises(SupervisorError, match="retention_service_already_running"):
            with service_lock(audit):
                pass
    finally:
        process.terminate()
        process.wait(timeout=5)
    with service_lock(audit):
        pass


def test_unmanaged_automation_start_is_refused(monkeypatch):
    monkeypatch.delenv(LOCK_FD_ENV, raising=False)
    monkeypatch.delenv(PARENT_PID_ENV, raising=False)
    expected = "retention_supervisor_required" if os.name == "posix" else "retention_automation_requires_posix"
    with pytest.raises(SupervisorError, match=expected):
        validate_managed_child()


@pytest.mark.skipif(os.name != "posix", reason="Production automation explicitly requires POSIX")
def test_sigterm_reaps_child_before_lifetime_lock_is_released(run_files):
    process = _start(run_files, cycles=0)
    try:
        _wait_started(process, run_files[-1])
        process.send_signal(signal.SIGTERM)
        records, err = _finish(process)
        assert process.returncode == 0, (records, err)
        assert records[-1] == {"event": "retention_http_stopped"}
        with service_lock(run_files[0]), sqlite3.connect(run_files[0], timeout=0) as conn:
            conn.execute("BEGIN EXCLUSIVE")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX process groups and inherited flock")
def test_forced_child_stop_does_not_authorize_cleanup_or_restart(run_files):
    process = _start(run_files, cycles=0, ignore=True)
    records, err = _finish(process)
    assert process.returncode == 1, err
    assert records[-1] == {"error": "retention_child_stop_timeout"}
    assert [r.get("event") for r in records].count("retention_success") == 1
    with sqlite3.connect(run_files[0], timeout=0) as conn:
        conn.execute("BEGIN EXCLUSIVE")


@pytest.mark.skipif(os.name != "posix", reason="Requires inherited POSIX flock")
def test_orphan_child_holds_runtime_lock_after_parent_sigkill(run_files):
    process = _start(run_files, cycles=0)
    child_pid = None
    try:
        child_pid = _wait_started(process, run_files[-1])["pid"]
        process.kill()
        process.wait(timeout=5)
        with pytest.raises(SupervisorError, match="retention_service_already_running"):
            with service_lock(run_files[0]):
                pass
    finally:
        if child_pid:
            os.killpg(child_pid, signal.SIGKILL)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.communicate(timeout=5)
