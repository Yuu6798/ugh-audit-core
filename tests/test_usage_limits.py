"""Persistent admission accounting, concurrency and crash-recovery boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import multiprocessing
import sqlite3
from threading import Barrier, Event
import time

import pytest

from ugh_audit.usage_limits import LimitExceeded, Limits, UsageLimiter, UsageUnavailable


class Clock:
    def __init__(self, now: float = 1000) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def attempt_count(path, owner: str) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(attempts), 0) FROM beta_daily_audits WHERE owner_id=?", (owner,),
        ).fetchone()[0]


@pytest.mark.parametrize("field", [
    "requests_per_minute", "audits_per_day", "global_audits_per_day",
    "concurrent_per_user", "concurrent_global",
])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10", 1_000_001])
def test_invalid_limits_fail_closed(field, value):
    with pytest.raises(ValueError):
        Limits(**{field: value})


@pytest.mark.parametrize("owner", [None, "", " ", "../user", "user\n", "u" * 129, True])
def test_invalid_owner_cannot_use_shared_bucket(tmp_path, owner):
    limiter = UsageLimiter(tmp_path / "usage.db")
    with pytest.raises(ValueError):
        limiter.check_request(owner)
    with pytest.raises(ValueError):
        limiter.acquire_audit(owner)


def test_request_sliding_window_is_shared_by_instances_and_survives_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "usage.db"
    limits = Limits(requests_per_minute=2)
    first = UsageLimiter(path, limits, clock=clock)
    first.check_request("alice")
    clock.now += 30
    second = UsageLimiter(path, limits, clock=clock)
    second.check_request("alice")
    with pytest.raises(LimitExceeded) as rejected:
        first.check_request("alice")
    assert rejected.value.code == "request_rate_exceeded"
    assert rejected.value.retry_after == 30
    second.check_request("bob")
    clock.now = 1060
    restarted = UsageLimiter(path, limits, clock=clock)
    restarted.check_request("alice")
    with pytest.raises(LimitExceeded):
        restarted.check_request("alice")


def test_request_counter_cannot_refund_when_clock_moves_back(tmp_path):
    clock = Clock()
    limiter = UsageLimiter(tmp_path / "usage.db", Limits(requests_per_minute=1), clock=clock)
    limiter.check_request("alice")
    clock.now = 500
    with pytest.raises(LimitExceeded) as rejected:
        limiter.check_request("alice")
    assert rejected.value.retry_after == 60


def test_daily_quotas_charge_admitted_attempts_not_results_or_token_ids(tmp_path):
    clock = Clock(86390)
    path = tmp_path / "usage.db"
    limits = Limits(audits_per_day=1, global_audits_per_day=2)
    limiter = UsageLimiter(path, limits, clock=clock)
    permit = limiter.acquire_audit("alice")
    permit.ensure_valid()
    permit.release()
    permit.release()
    restarted = UsageLimiter(path, limits, clock=clock)
    with pytest.raises(LimitExceeded) as rejected:
        restarted.acquire_audit("alice")
    assert rejected.value.code == "daily_audit_limit"
    assert rejected.value.retry_after == 10
    with restarted.acquire_audit("bob"):
        pass
    with pytest.raises(LimitExceeded) as global_rejected:
        restarted.acquire_audit("charlie")
    assert global_rejected.value.code == "global_daily_audit_limit"
    assert attempt_count(path, "charlie") == 0
    clock.now = 86400
    with restarted.acquire_audit("alice"):
        pass
    assert attempt_count(path, "alice") == 2


def test_failed_model_work_does_not_refund_daily_quota(tmp_path):
    path = tmp_path / "usage.db"
    limiter = UsageLimiter(path, Limits(audits_per_day=1))
    with pytest.raises(RuntimeError, match="model failed"):
        with limiter.acquire_audit("alice"):
            raise RuntimeError("model failed")
    with pytest.raises(LimitExceeded) as rejected:
        limiter.acquire_audit("alice")
    assert rejected.value.code == "daily_audit_limit"
    assert attempt_count(path, "alice") == 1


def test_per_owner_and_global_concurrency_rejections_do_not_charge(tmp_path):
    path = tmp_path / "usage.db"
    limits = Limits(concurrent_global=2)
    first = UsageLimiter(path, limits)
    second = UsageLimiter(path, limits)
    a = first.acquire_audit("alice")
    b = second.acquire_audit("bob")
    try:
        with pytest.raises(LimitExceeded) as same:
            second.acquire_audit("alice")
        assert same.value.code == "concurrent_audit_limit"
        with pytest.raises(LimitExceeded) as other:
            first.acquire_audit("charlie")
        assert other.value.code == "global_concurrent_audit_limit"
        assert attempt_count(path, "alice") == 1
        assert attempt_count(path, "charlie") == 0
    finally:
        a.release()
        b.release()
    with second.acquire_audit("charlie"):
        pass


def test_concurrent_worker_instances_cannot_oversubscribe_one_slot(tmp_path):
    path = tmp_path / "usage.db"
    limits = Limits(concurrent_global=1)
    barrier = Barrier(8)

    def contend(index):
        limiter = UsageLimiter(path, limits)
        barrier.wait(timeout=10)
        try:
            return limiter.acquire_audit(f"owner-{index}")
        except LimitExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        permits = list(pool.map(contend, range(8)))
    granted = [permit for permit in permits if permit is not None]
    try:
        assert len(granted) == 1
    finally:
        for permit in granted:
            permit.release()


def _process_daily_attempt(path, index, start, results):
    limiter = UsageLimiter(path, Limits(global_audits_per_day=2, concurrent_global=10))
    start.wait(timeout=20)
    try:
        with limiter.acquire_audit(f"process-{index}"):
            pass
    except LimitExceeded as exc:
        results.put(exc.code)
    else:
        results.put("admitted")


def test_competing_processes_observe_one_global_daily_limit(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    path = str(tmp_path / "usage.db")
    processes = [context.Process(target=_process_daily_attempt, args=(path, i, start, results))
                 for i in range(4)]
    for process in processes:
        process.start()
    start.set()
    try:
        observed = [results.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert observed.count("admitted") == 2
        assert observed.count("global_daily_audit_limit") == 2
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        results.close()


def test_expired_crashed_lease_is_recovered_without_refunding_attempt(tmp_path):
    path = tmp_path / "usage.db"
    clock = Clock()
    first = UsageLimiter(path, clock=clock)
    abandoned = first.acquire_audit("alice")
    try:
        # The worker has no opportunity to heartbeat before this deterministic jump.
        clock.now += 301
        second = UsageLimiter(path, clock=clock)
        recovered = second.acquire_audit("alice")
        try:
            with pytest.raises(UsageUnavailable):
                abandoned.ensure_valid()
            abandoned.release()
            recovered.ensure_valid()
            assert attempt_count(path, "alice") == 2
        finally:
            recovered.release()
    finally:
        abandoned.release()


def test_heartbeat_holds_slot_beyond_initial_expiry(tmp_path):
    path = tmp_path / "usage.db"
    clock = Clock()
    limiter = UsageLimiter(path, clock=clock, lease_seconds=1, heartbeat_seconds=0.02)
    permit = limiter.acquire_audit("alice")
    try:
        # Observe each successful heartbeat before advancing the fake clock again.
        # The final time exceeds the initial lease without depending on CPU speed.
        for _ in range(3):
            clock.now += 0.5
            deadline = time.monotonic() + 5
            while True:
                with sqlite3.connect(path) as conn:
                    expiry = conn.execute("SELECT expires_at FROM beta_audit_leases").fetchone()[0]
                if expiry >= clock.now + 1:
                    break
                assert time.monotonic() < deadline, "heartbeat did not renew the live lease"
                Event().wait(0.01)
        contender = UsageLimiter(path, clock=clock)
        with pytest.raises(LimitExceeded) as rejected:
            contender.acquire_audit("alice")
        assert rejected.value.code == "concurrent_audit_limit"
        permit.ensure_valid()
    finally:
        permit.release()


def test_heartbeat_failure_does_not_silently_reauthorize_more_work(tmp_path):
    failed_read = Event()
    invalid = Event()

    def clock():
        if invalid.is_set():
            failed_read.set()
            return float("nan")
        return 1000

    limiter = UsageLimiter(tmp_path / "usage.db", clock=clock,
                           lease_seconds=1, heartbeat_seconds=0.02)
    permit = limiter.acquire_audit("alice")
    try:
        invalid.set()
        assert failed_read.wait(timeout=5)
        invalid.clear()
        # Even though the clock is valid again, the interrupted reservation fails closed.
        with pytest.raises(UsageUnavailable):
            permit.ensure_valid()
    finally:
        invalid.clear()
        permit.release()


def test_storage_failure_blocks_admission_and_lease_renewal(tmp_path):
    path = tmp_path / "usage.db"
    limiter = UsageLimiter(path)
    permit = limiter.acquire_audit("alice")
    try:
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TABLE beta_usage_clock")
        with pytest.raises(UsageUnavailable):
            limiter.check_request("alice")
        with pytest.raises(UsageUnavailable):
            limiter.acquire_audit("bob")
        with pytest.raises(UsageUnavailable):
            permit.ensure_valid()
    finally:
        # Repair fixture only to allow deterministic release without a live thread.
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE beta_usage_clock (id INTEGER PRIMARY KEY, last_seen REAL)")
            conn.execute("INSERT INTO beta_usage_clock VALUES (1, ?)", (time.time(),))
        permit.release()


def test_unopenable_database_fails_closed(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(UsageUnavailable):
        UsageLimiter(directory)


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -1, True, "invalid"])
def test_invalid_clock_fails_closed(tmp_path, now):
    limiter = UsageLimiter(tmp_path / "usage.db", clock=lambda: now)
    with pytest.raises(UsageUnavailable):
        limiter.check_request("alice")
    with pytest.raises(UsageUnavailable):
        limiter.acquire_audit("alice")


def test_configuration_with_nonpersistent_db_or_unsafe_lease_fails():
    with pytest.raises(ValueError):
        UsageLimiter(":memory:")
    with pytest.raises(ValueError):
        UsageLimiter("unused.db", heartbeat_seconds=300)
    assert replace(Limits(), audits_per_day=5).audits_per_day == 5
