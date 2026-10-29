"""Persistent invitation-beta quotas shared by workers using the same SQLite file.

HTTP requests use a sliding 60-second window. Admitted audit attempts consume
per-owner and global UTC-day quotas even when a model fails or a client leaves.
Audit permits use renewable leases: callers release only after the actual worker
finishes, not when an HTTP response is cancelled. A crashed worker's lease expires.
The ledger stores identifiers and counters, never credentials or audit content.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields
import math
from pathlib import Path
import re
import sqlite3
from threading import Event, Lock, Thread
import time
from typing import Callable, Iterator
import uuid


class UsageUnavailable(RuntimeError):
    """Usage state could not be checked; callers must not run an audit."""

    def __init__(self) -> None:
        super().__init__("Usage accounting unavailable")


class LimitExceeded(RuntimeError):
    """A known quota refusal, suitable for HTTP 429 and Retry-After."""

    def __init__(self, code: str, retry_after: int) -> None:
        self.code = code
        self.retry_after = max(1, retry_after)
        super().__init__(code)


@dataclass(frozen=True)
class Limits:
    requests_per_minute: int = 60
    audits_per_day: int = 100
    global_audits_per_day: int = 500
    concurrent_per_user: int = 1
    concurrent_global: int = 4

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or not 1 <= value <= 1_000_000:
                raise ValueError(f"{field.name} must be an integer from 1 to 1000000")


def _owner(owner_id: str) -> str:
    if not isinstance(owner_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", owner_id):
        raise ValueError("Invalid usage owner")
    return owner_id


class UsageLimiter:
    """Atomic quotas for one deployment sharing one persistent SQLite database.

    ``clock`` and lease timings are injectable for deterministic/short tests.
    Production defaults allow five minutes for crash recovery and renew every
    thirty seconds. A durable clock watermark avoids quota refunds on rollback
    of the host clock. Different deployments must not use unrelated ledger files
    and expect a shared global cap.
    """

    def __init__(
        self, db_path: Path, limits: Limits = Limits(), *,
        clock: Callable[[], float] = time.time,
        lease_seconds: float = 300.0, heartbeat_seconds: float = 30.0,
    ) -> None:
        if not isinstance(limits, Limits) or not callable(clock):
            raise ValueError("Invalid limiter configuration")
        for value in (lease_seconds, heartbeat_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Lease timings must be finite positive numbers")
        if heartbeat_seconds >= lease_seconds / 2:
            raise ValueError("Heartbeat must be shorter than half the lease duration")
        if str(db_path) == ":memory:":
            raise ValueError("Usage limits require a persistent database")
        self.db_path = Path(db_path)
        self.limits = limits
        self.clock = clock
        self.lease_seconds = float(lease_seconds)
        self.heartbeat_seconds = float(heartbeat_seconds)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS beta_requests (
                    id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, at REAL NOT NULL
                )""")
                conn.execute("CREATE INDEX IF NOT EXISTS beta_request_window ON beta_requests(owner_id, at)")
                conn.execute("""CREATE TABLE IF NOT EXISTS beta_daily_audits (
                    day INTEGER NOT NULL, owner_id TEXT NOT NULL, attempts INTEGER NOT NULL,
                    PRIMARY KEY(day, owner_id)
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS beta_audit_leases (
                    lease_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, expires_at REAL NOT NULL
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS beta_usage_clock (
                    id INTEGER PRIMARY KEY CHECK(id=1), last_seen REAL NOT NULL
                )""")
                conn.execute("INSERT OR IGNORE INTO beta_usage_clock(id, last_seen) VALUES (1, 0)")
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            raise UsageUnavailable() from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, float]]:
        try:
            try:
                raw_now = self.clock()
            except Exception as exc:
                raise UsageUnavailable() from exc
            if (type(raw_now) not in (int, float) or not math.isfinite(raw_now)
                    or not 0 <= raw_now <= 253402300799):
                raise UsageUnavailable()
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                previous = conn.execute("SELECT last_seen FROM beta_usage_clock WHERE id=1").fetchone()
                if (previous is None or type(previous[0]) not in (int, float)
                        or not math.isfinite(previous[0]) or not 0 <= previous[0] <= 253402300799):
                    raise UsageUnavailable()
                now = max(float(raw_now), previous[0])
                conn.execute("UPDATE beta_usage_clock SET last_seen=? WHERE id=1", (now,))
                yield conn, now
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            raise UsageUnavailable() from exc

    def check_request(self, owner_id: str) -> None:
        """Charge one authenticated HTTP request, rejecting beyond a rolling minute."""
        owner_id = _owner(owner_id)
        with self._transaction() as (conn, now):
            conn.execute("DELETE FROM beta_requests WHERE at<=?", (now - 60,))
            count, earliest = conn.execute(
                "SELECT COUNT(*), MIN(at) FROM beta_requests WHERE owner_id=?", (owner_id,),
            ).fetchone()
            if count >= self.limits.requests_per_minute:
                raise LimitExceeded("request_rate_exceeded", math.ceil(earliest + 60 - now))
            conn.execute("INSERT INTO beta_requests(owner_id, at) VALUES (?, ?)", (owner_id, now))

    def acquire_audit(self, owner_id: str) -> Permit:
        """Charge and reserve an audit before queueing work; never refund admission."""
        owner_id = _owner(owner_id)
        lease_id = uuid.uuid4().hex
        with self._transaction() as (conn, now):
            day = int(now // 86400)
            conn.execute("DELETE FROM beta_daily_audits WHERE day<?", (day - 7,))
            conn.execute("DELETE FROM beta_audit_leases WHERE expires_at<=?", (now,))
            owner_attempts = conn.execute(
                "SELECT attempts FROM beta_daily_audits WHERE day=? AND owner_id=?", (day, owner_id),
            ).fetchone()
            if owner_attempts and owner_attempts[0] >= self.limits.audits_per_day:
                raise LimitExceeded("daily_audit_limit", math.ceil((day + 1) * 86400 - now))
            total = conn.execute(
                "SELECT COALESCE(SUM(attempts), 0) FROM beta_daily_audits WHERE day=?", (day,),
            ).fetchone()[0]
            if total >= self.limits.global_audits_per_day:
                raise LimitExceeded("global_daily_audit_limit", math.ceil((day + 1) * 86400 - now))
            active = conn.execute(
                "SELECT COUNT(*) FROM beta_audit_leases WHERE owner_id=?", (owner_id,),
            ).fetchone()[0]
            if active >= self.limits.concurrent_per_user:
                raise LimitExceeded("concurrent_audit_limit", 1)
            active_global = conn.execute("SELECT COUNT(*) FROM beta_audit_leases").fetchone()[0]
            if active_global >= self.limits.concurrent_global:
                raise LimitExceeded("global_concurrent_audit_limit", 1)
            conn.execute("""INSERT INTO beta_daily_audits(day, owner_id, attempts) VALUES (?, ?, 1)
                ON CONFLICT(day, owner_id) DO UPDATE SET attempts=attempts+1""", (day, owner_id))
            conn.execute(
                "INSERT INTO beta_audit_leases(lease_id, owner_id, expires_at) VALUES (?, ?, ?)",
                (lease_id, owner_id, now + self.lease_seconds),
            )
        return Permit(self, lease_id)

    def _renew(self, lease_id: str) -> None:
        with self._transaction() as (conn, now):
            updated = conn.execute(
                "UPDATE beta_audit_leases SET expires_at=? WHERE lease_id=? AND expires_at>?",
                (now + self.lease_seconds, lease_id, now),
            )
            if updated.rowcount != 1:
                raise UsageUnavailable()

    def _release(self, lease_id: str) -> None:
        with self._transaction() as (conn, _):
            conn.execute("DELETE FROM beta_audit_leases WHERE lease_id=?", (lease_id,))


class Permit:
    """An audit slot held by the actual worker, with independent lease renewal.

    Use ``ensure_valid`` immediately before starting work, and before any later
    separately queued expensive stage. Renewal errors poison the permit even if
    the heartbeat subsequently recovers; a caller must not start more work on it.
    Existing in-flight external work cannot be forcibly stopped by this ledger.
    """

    def __init__(self, limiter: UsageLimiter, lease_id: str) -> None:
        self._limiter = limiter
        self._lease_id = lease_id
        self._stop = Event()
        self._lock = Lock()
        self._released = False
        self._releasing = False
        self._failure = False
        self._thread = Thread(target=self._heartbeat, name="ugh-audit-lease", daemon=True)
        try:
            self._thread.start()
        except RuntimeError as exc:
            self._limiter._release(self._lease_id)
            raise UsageUnavailable() from exc

    def _heartbeat(self) -> None:
        while not self._stop.wait(self._limiter.heartbeat_seconds):
            with self._lock:
                if self._releasing or self._released:
                    return
                try:
                    self._limiter._renew(self._lease_id)
                except UsageUnavailable:
                    self._failure = True

    def ensure_valid(self) -> None:
        with self._lock:
            if self._released or self._releasing or self._failure:
                raise UsageUnavailable()
            self._limiter._renew(self._lease_id)

    def release(self) -> None:
        """Release once work is finished. Repeated successful releases are no-ops."""
        self._stop.set()
        with self._lock:
            if self._released:
                return
            self._releasing = True
            self._limiter._release(self._lease_id)
            self._released = True

    def __enter__(self) -> Permit:
        try:
            self.ensure_valid()
        except UsageUnavailable:
            self.release()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
