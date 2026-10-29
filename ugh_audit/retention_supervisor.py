"""Single-host stop/clean/restart supervision for the opt-in invitation beta.

Production automation requires POSIX process groups and an inherited lifetime
lock. Old deployments and direct, unmanaged servers must be stopped before the
first supervised start. No provider request is made by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
from threading import Thread
import time
from typing import Callable, Mapping, Optional, Sequence

from .retention import maintain_audits, maintain_usage


INTERVAL_SECONDS = 23 * 60 * 60
MAINTENANCE_MARGIN_SECONDS = 60 * 60
STOP_GRACE_SECONDS = 30
LOCK_FD_ENV = "UGH_RETENTION_LOCK_FD"
PARENT_PID_ENV = "UGH_RETENTION_SUPERVISOR_PID"
CHILD_DEADLINE_ENV = "UGH_RETENTION_CHILD_DEADLINE_UTC"


class SupervisorError(Exception):
    """A fixed public code; never an exception body containing private data."""


def _regular_target(path: Path) -> Path:
    if (not path.is_absolute() or not path.parent.is_dir()
            or any(part.is_symlink() for part in (path, *path.parents))
            or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1))):
        raise SupervisorError("retention_path_invalid")
    return path.resolve()


def state_path(audit: Path) -> Path:
    return Path(str(audit) + ".retention.json")


@contextmanager
def service_lock(audit: Path):
    """Keep this FD open for the complete HTTP lifetime, across child restarts."""
    path = _regular_target(Path(str(_regular_target(audit)) + ".supervisor.lock"))
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SupervisorError("retention_lock_invalid")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SupervisorError("retention_service_already_running") from None
        os.set_inheritable(descriptor, True)
        yield descriptor
    finally:
        os.close(descriptor)


def validate_managed_child() -> None:
    """Reject direct uvicorn/MCP starts when automated maintenance is enabled."""
    if os.name != "posix":
        raise SupervisorError("retention_automation_requires_posix")
    try:
        import fcntl
        descriptor = int(os.environ[LOCK_FD_ENV])
        parent = int(os.environ[PARENT_PID_ENV])
        # The Docker CMD supervisor legitimately runs as PID 1.
        if parent != os.getppid() or parent < 1 or descriptor < 3:
            raise ValueError
        lock = _regular_target(Path(os.environ["UGH_AUDIT_DB"] + ".supervisor.lock"))
        actual, expected = os.fstat(descriptor), lock.stat()
        if (not stat.S_ISREG(actual.st_mode) or actual.st_ino != expected.st_ino
                or actual.st_dev != expected.st_dev):
            raise ValueError
        # A different open description must conflict with the inherited lock.
        probe = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
        finally:
            os.close(probe)
    except (KeyError, ValueError, OSError, SupervisorError):
        pass
    raise SupervisorError("retention_supervisor_required")


def _time(clock: Callable[[], float]) -> float:
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise SupervisorError("retention_clock_invalid")
    return float(value)


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def start_managed_child_watchdog(
    *, poll_seconds: float = 0.25, grace_seconds: float = STOP_GRACE_SECONDS,
    clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
) -> Thread:
    """Bound a child's lifetime even if its supervisor dies or stops scheduling.

    Deliberately never cancelled on ASGI lifespan exit: synchronous workers may
    still keep the process and its inherited lock alive after HTTP shutdown.
    This daemon ends with the process, or escalates its own stop after the grace
    deadline. Production exposes no timing overrides outside this function.
    """
    validate_managed_child()
    if not 0 < poll_seconds <= 1 or not 0 < grace_seconds <= STOP_GRACE_SECONDS:
        raise SupervisorError("retention_watchdog_timing_invalid")
    try:
        parent = int(os.environ[PARENT_PID_ENV])
        deadline = datetime.fromisoformat(os.environ[CHILD_DEADLINE_ENV])
        if deadline.utcoffset() is None:
            raise ValueError
        deadline_utc = deadline.timestamp()
        observed = _time(clock)
        remaining = deadline_utc - observed
        if not 0 < remaining <= INTERVAL_SECONDS + STOP_GRACE_SECONDS:
            raise ValueError
        deadline_monotonic = monotonic() + remaining
    except (KeyError, TypeError, ValueError, OverflowError):
        raise SupervisorError("retention_child_deadline_invalid") from None

    def watch() -> None:
        last_seen = observed
        while True:
            try:
                current = _time(clock)
                if (os.getppid() != parent or current < last_seen
                        or current >= deadline_utc or monotonic() >= deadline_monotonic):
                    break
                last_seen = current
                time.sleep(poll_seconds)
            except Exception:
                break
        # Do not write logs here: a full pipe or another thread holding a stream
        # lock must not delay termination. The living supervisor reports exits.
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(grace_seconds)
            # The supervisor gave this child a dedicated process group.
            # Include any unexpected descendants retaining the runtime FD.
            if os.getpgrp() == os.getpid():
                os.killpg(os.getpgrp(), signal.SIGKILL)
            else:
                os.kill(os.getpid(), signal.SIGKILL)
        finally:
            os._exit(1)

    thread = Thread(target=watch, name="ugh-retention-watchdog", daemon=True)
    thread.start()
    return thread


def _read_state(path: Path) -> Optional[dict]:
    path = _regular_target(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError
        result = json.loads(path.read_text(encoding="utf-8"))
        times = []
        for name in ("cycle_started_utc", "last_success_utc", "next_due_utc"):
            parsed = datetime.fromisoformat(result[name])
            if parsed.utcoffset() is None:
                raise ValueError
            times.append(parsed.timestamp())
        if (result["schema_version"] != 1 or not times[0] <= times[1] < times[2]
                or not 0 < times[2] - times[0] <= INTERVAL_SECONDS):
            raise ValueError
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise SupervisorError("retention_state_invalid") from None


def _write_state(path: Path, value: dict) -> None:
    path = _regular_target(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".retention-receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _clean(environ: Mapping[str, str], now: datetime) -> dict:
    from .managed_files import clean_managed_files

    audit = maintain_audits(Path(environ["UGH_AUDIT_DB"]), older_than_days=29,
                            apply=True, service_stopped=True, now=now)
    usage = maintain_usage(Path(environ["UGH_USAGE_DB"]), apply=True,
                           service_stopped=True, now=now)
    managed = clean_managed_files(environ, now=now)
    return {"audit": audit, "usage": usage, "managed_files": managed}


def _group_exists(process: subprocess.Popen) -> bool:
    if os.name != "posix":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
        return True
    except ProcessLookupError:
        return False


def _signal_child(process: subprocess.Popen, *, force: bool = False) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif process.poll() is None:
            # Test helpers can exercise ordering on Windows; production AUTO=1
            # is explicitly rejected there because no inherited flock is available.
            process.kill() if force else process.terminate()
    except ProcessLookupError:
        pass


def _stop_child(process: subprocess.Popen, grace: float) -> bool:
    """Return false after forced termination; cleanup must not then proceed."""
    _signal_child(process)
    forced = False
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        forced = True
        _signal_child(process, force=True)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            raise SupervisorError("retention_child_not_reaped") from None
    # uvicorn uses one process. Unexpected descendants must also be gone before
    # touching SQLite. Killing a residual group never authorizes a cleanup.
    if _group_exists(process):
        forced = True
        _signal_child(process, force=True)
        deadline = time.monotonic() + 5
        while _group_exists(process) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _group_exists(process):
            raise SupervisorError("retention_descendants_not_reaped")
    return not forced


def supervise(
    command: Sequence[str], environ: Mapping[str, str], lock_fd: int, *,
    interval_seconds: float = INTERVAL_SECONDS, grace_seconds: float = STOP_GRACE_SECONDS,
    poll_seconds: float = 0.25, clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    cleaner: Callable[[Mapping[str, str], datetime], dict] = _clean,
) -> int:
    """Start only after cleanup; every subsequent cleanup runs after child reap.

    Timing and cleaner injection support short, real-subprocess integration
    tests. The deployment entrypoint exposes no timing overrides to production.
    """
    if (not 0 < interval_seconds <= INTERVAL_SECONDS or not 0 < grace_seconds <= STOP_GRACE_SECONDS
            or not 0 < poll_seconds <= 1):
        raise SupervisorError("retention_timing_invalid")
    stopping = False
    child = None
    previous_deadline_monotonic = None
    previous_deadline_utc = None

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    old_handlers = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        path = state_path(Path(environ["UGH_AUDIT_DB"]))
        previous = _read_state(path)
        observed = _time(clock)
        if previous and observed < datetime.fromisoformat(previous["last_success_utc"]).timestamp():
            raise SupervisorError("retention_clock_moved_backwards")
        while not stopping:
            started, monotonic_started = _time(clock), monotonic()
            if started < observed:
                raise SupervisorError("retention_clock_moved_backwards")
            receipt = cleaner(environ, datetime.fromtimestamp(started, timezone.utc))
            finished = _time(clock)
            monotonic_finished = monotonic()
            if finished < started:
                raise SupervisorError("retention_clock_moved_backwards")
            # Reserve the grace period and poll delay inside the one-hour
            # margin, rather than adding them after a full hour of cleanup.
            cleanup_budget = MAINTENANCE_MARGIN_SECONDS - STOP_GRACE_SECONDS - 1
            if (monotonic_finished - monotonic_started >= cleanup_budget
                    or finished - started >= min(cleanup_budget, interval_seconds)
                    or (previous_deadline_monotonic is not None
                        and monotonic_finished >= previous_deadline_monotonic)
                    or (previous_deadline_utc is not None and finished >= previous_deadline_utc)):
                raise SupervisorError("retention_cleanup_deadline_exceeded")
            _write_state(path, {"schema_version": 1, "event": "retention_success",
                                "cycle_started_utc": _iso(started), "last_success_utc": _iso(finished),
                                "next_due_utc": _iso(started + interval_seconds), **receipt})
            print(json.dumps({"event": "retention_success", "next_due_utc": _iso(
                started + interval_seconds), **receipt}), flush=True)
            if stopping:
                return 0
            child_env = {**environ, LOCK_FD_ENV: str(lock_fd), PARENT_PID_ENV: str(os.getpid()),
                         CHILD_DEADLINE_ENV: _iso(started + interval_seconds + STOP_GRACE_SECONDS)}
            kwargs = {"start_new_session": True, "pass_fds": (lock_fd,)} if os.name == "posix" else {}
            child = subprocess.Popen(list(command), env=child_env, **kwargs)
            print(json.dumps({"event": "retention_http_started"}), flush=True)
            observed = finished
            previous_deadline_monotonic = monotonic_started + INTERVAL_SECONDS + MAINTENANCE_MARGIN_SECONDS
            previous_deadline_utc = started + INTERVAL_SECONDS + MAINTENANCE_MARGIN_SECONDS
            while not stopping:
                current = _time(clock)
                if current < observed:
                    raise SupervisorError("retention_clock_moved_backwards")
                observed = current
                if child.poll() is not None:
                    raise SupervisorError("retention_http_exited")
                if (monotonic() - monotonic_started >= interval_seconds
                        or current >= started + interval_seconds):
                    break
                time.sleep(poll_seconds)
            graceful = _stop_child(child, grace_seconds)
            child = None
            print(json.dumps({"event": "retention_http_stopped"}), flush=True)
            if not graceful:
                raise SupervisorError("retention_child_stop_timeout")
        return 0
    except SupervisorError:
        raise
    except Exception:
        raise SupervisorError("retention_maintenance_failed") from None
    finally:
        try:
            if child is not None:
                _stop_child(child, grace_seconds)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
