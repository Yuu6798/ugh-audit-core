"""Offline cleanup of explicitly managed backups and retired HTTP caches.

The caller must stop all HTTP workers and exclude deployments/writers first.
Never infer backup age from mtime or recursively remove a directory. This module
does not restore backups or replace the operator's post-restore deletion steps.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Mapping
import zipfile


BACKUP_NAME = re.compile(r"audit-before-[0-9a-f]{12}-[0-9a-f]{32}\.sqlite\Z")
MANIFEST_SUFFIX = ".retention.json"
BACKUP_CLEANUP_DAYS = 29  # A supervisor interval <=23h keeps the 30-day ceiling.
_MAX_META_BYTES = 2 * 1024 * 1024
_MAX_EMBED_BYTES = 128 * 1024 * 1024


class ManagedFilesError(Exception):
    """Fixed codes only. A failed cleanup can already have removed eligible files."""


def _clock(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ManagedFilesError("timezone_aware_clock_required")
    return value.astimezone(timezone.utc)


def _timestamp(value: str) -> datetime:
    try:
        return _clock(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ManagedFilesError("invalid_backup_retention_origin") from None


def _identity(value: os.stat_result) -> tuple:
    # Windows path stat and handle fstat can disagree on the deprecated ctime
    # interpretation (birth time versus change time). Neither is an age source.
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns if os.name != "nt" else None)


def _safe_path(value: Path, *, directory: bool = False, missing: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise ManagedFilesError("invalid_managed_path")
    # lstat catches dangling links as well as Windows junction/reparse points.
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise ManagedFilesError("missing_managed_path") from None
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ManagedFilesError("managed_link_rejected")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise ManagedFilesError("invalid_managed_parent")
        if part == path:
            if directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise ManagedFilesError("managed_directory_required")
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ManagedFilesError("managed_regular_file_required")
    return path


def _file_snapshot(path: Path) -> tuple:
    _safe_path(path)
    return _identity(path.lstat())


def _read(path: Path, limit: int) -> tuple[bytes, tuple]:
    before = _file_snapshot(path)
    if before[4] > limit:
        raise ManagedFilesError("managed_file_too_large")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if _identity(os.fstat(stream.fileno())) != before:
            raise ManagedFilesError("managed_file_changed")
        value = stream.read(limit + 1)
        if _identity(os.fstat(stream.fileno())) != before:
            raise ManagedFilesError("managed_file_changed")
    if len(value) > limit or _file_snapshot(path) != before:
        raise ManagedFilesError("managed_file_changed")
    return value, before


def _hash(path: Path) -> tuple[str, tuple]:
    before = _file_snapshot(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        if _identity(os.fstat(stream.fileno())) != before:
            raise ManagedFilesError("managed_file_changed")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        if _identity(os.fstat(stream.fileno())) != before:
            raise ManagedFilesError("managed_file_changed")
    if _file_snapshot(path) != before:
        raise ManagedFilesError("managed_file_changed")
    return digest.hexdigest(), before


def _json(value: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError
            result[key] = item
        return result

    try:
        result = json.loads(value, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (UnicodeError, ValueError, RecursionError):
        raise ManagedFilesError("invalid_managed_json") from None


def _backup(path: Path) -> tuple[str, tuple]:
    if path.parent.name != "deployment-backups" or not BACKUP_NAME.fullmatch(path.name):
        raise ManagedFilesError("invalid_managed_backup_name")
    _safe_path(path.parent, directory=True)
    digest, snapshot = _hash(path)
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(str(path) + suffix):
            raise ManagedFilesError("managed_backup_sidecar_rejected")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            conn.execute("PRAGMA trusted_schema=OFF")
            if (conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                    or conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                    "AND name NOT LIKE 'sqlite_%'").fetchall() != [("audit_runs",)]
                    or conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger')").fetchone()):
                raise ManagedFilesError("invalid_managed_backup_database")
            columns = {row[1]: row[2].upper() for row in conn.execute("PRAGMA table_info(audit_runs)")}
            if any(columns.get(name) != kind for name, kind in {
                    "id": "INTEGER", "question": "TEXT", "response": "TEXT", "created_at": "TEXT"}.items()):
                raise ManagedFilesError("invalid_managed_backup_database")
    except sqlite3.Error:
        raise ManagedFilesError("invalid_managed_backup_database") from None
    if _file_snapshot(path) != snapshot:
        raise ManagedFilesError("managed_file_changed")
    return digest, snapshot


def _manifest(path: Path, now: datetime) -> tuple[dict, tuple]:
    value, snapshot = _read(path, 4096)
    result = _json(value)
    if (set(result) != {"version", "backup_name", "retention_origin_utc", "origin_kind", "size", "sha256"}
            or type(result["version"]) is not int or result["version"] != 1
            or not isinstance(result["backup_name"], str)
            or not BACKUP_NAME.fullmatch(result["backup_name"])
            or path.name != result["backup_name"] + MANIFEST_SUFFIX
            or not isinstance(result["origin_kind"], str)
            or result["origin_kind"] not in {"recorded_at_creation", "observed_lower_bound"}
            or type(result["size"]) is not int or result["size"] <= 0
            or not isinstance(result["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", result["sha256"])):
        raise ManagedFilesError("invalid_backup_manifest")
    origin = _timestamp(result["retention_origin_utc"])
    if origin > now:
        raise ManagedFilesError("backup_origin_in_future")
    return result, snapshot


def _register(path: Path, origin: datetime, kind: str, *, expected_size: int | None = None,
              expected_sha256: str | None = None) -> dict:
    origin = _clock(origin)
    path = Path(path)
    digest, snapshot = _backup(path)
    if ((expected_size is not None and (type(expected_size) is not int or snapshot[4] != expected_size))
            or (expected_sha256 is not None and digest != expected_sha256)):
        raise ManagedFilesError("legacy_backup_evidence_mismatch")
    manifest = path.with_name(path.name + MANIFEST_SUFFIX)
    _safe_path(manifest, missing=True)
    result = {"version": 1, "backup_name": path.name, "retention_origin_utc": origin.isoformat(),
              "origin_kind": kind, "size": snapshot[4], "sha256": digest}
    descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write((json.dumps(result, sort_keys=True) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    if _file_snapshot(path) != snapshot:
        raise ManagedFilesError("managed_file_changed")
    return {"backups_registered": 1}


def register_managed_backup(path: Path, *, now: datetime) -> dict:
    """Call once immediately after making a new stopped-service SQLite backup.

    Pass the backup operation's start time. Existing records are never replaced,
    including when a copied file has a more recent filesystem timestamp.
    """
    try:
        return _register(path, now, "recorded_at_creation")
    except OSError:
        raise ManagedFilesError("managed_backup_registration_failed") from None


def register_legacy_backup(directory: Path, *, name: str, retention_origin: datetime,
                           expected_size: int, expected_sha256: str,
                           now: datetime | None = None) -> dict:
    """Adopt only with independently reviewed creation evidence plus size/hash.

    observed_lower_bound means a time known to precede actual creation (for
    example, the earliest UTC time in a logged creation-date range). It is NOT
    the time a file was first discovered, an mtime, or a copy's creation time.
    If creation cannot be bounded, leave the file unregistered for review.
    """
    clock = _clock(now or datetime.now(timezone.utc))
    if (not isinstance(name, str) or not BACKUP_NAME.fullmatch(name)
            or type(expected_size) is not int or expected_size <= 0
            or not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or _clock(retention_origin) > clock):
        raise ManagedFilesError("invalid_legacy_backup_evidence")
    try:
        return _register(Path(directory) / name, retention_origin, "observed_lower_bound",
                         expected_size=expected_size, expected_sha256=expected_sha256)
    except OSError:
        raise ManagedFilesError("managed_backup_registration_failed") from None


def _embedding(path: Path) -> tuple:
    value, snapshot = _read(path, _MAX_EMBED_BYTES)
    try:
        import numpy as np

        with zipfile.ZipFile(io.BytesIO(value)) as archive:
            entries = archive.infolist()
            if (len(entries) > 10000 or len({entry.filename for entry in entries}) != len(entries)
                    or sum(entry.file_size for entry in entries) > _MAX_EMBED_BYTES):
                raise ValueError
            for entry in entries:
                if (not re.fullmatch(r"[0-9a-f]{24}\.npy", entry.filename)
                        or entry.flag_bits & 1 or entry.is_dir()):
                    raise ValueError
                with archive.open(entry) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version == (1, 0):
                        shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                    elif version == (2, 0):
                        shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                    else:
                        raise ValueError
                    if (dtype.kind != "f" or len(shape) != 1 or not 1 <= shape[0] <= 65536
                            or entry.file_size - stream.tell() != shape[0] * dtype.itemsize):
                        raise ValueError
            if archive.testzip() is not None:
                raise ValueError
    except Exception:
        # Untrusted archive/header errors must not include their payload in logs.
        raise ManagedFilesError("invalid_embedding_cache") from None
    return snapshot


def _unlink(path: Path, snapshot: tuple) -> None:
    if _file_snapshot(path) != snapshot:
        raise ManagedFilesError("managed_file_changed")
    path.unlink()


def _reserved_paths(environ: Mapping[str, str], audit: Path) -> set[Path]:
    usage = _safe_path(Path(environ.get("UGH_USAGE_DB", str(audit.with_name("usage.db")))), missing=True)
    registry = _safe_path(Path(environ.get("UGH_INVITE_FILE", str(audit.with_name("invites.json")))), missing=True)
    return {audit, usage, registry, Path(str(registry) + ".lock"),
            *(Path(str(path) + suffix) for path in (audit, usage)
              for suffix in ("-wal", "-shm", "-journal", ".deployment.lock")),
            *(Path(str(audit) + suffix) for suffix in (".supervisor.lock", ".retention.json"))}


def clean_managed_files(environ: Mapping[str, str], *, now: datetime) -> dict:
    """Preflight all known artifacts, then unlink eligible files individually.

    No metadata row, owner, path, cache key or credential enters the receipt.
    Missing directories are a no-op; unknown files fail before any unlink.
    """
    clock = _clock(now)
    try:
        audit = _safe_path(Path(environ.get("UGH_AUDIT_DB", "")))
        root = _safe_path(audit.parent, directory=True)
        directory = _safe_path(root / "deployment-backups", directory=True, missing=True)
        meta = _safe_path(Path(environ.get("UGH_META_CACHE_DIR", str(root / "meta_cache"))),
                          directory=True, missing=True)
        embed_dir = _safe_path(Path(environ.get("UGH_AUDIT_CACHE_DIR", str(root))),
                               directory=True, missing=True)
        if (meta == root or not meta.is_relative_to(root) or not embed_dir.is_relative_to(root)
                or meta.is_relative_to(directory) or embed_dir.is_relative_to(directory)
                or meta == embed_dir or directory.is_relative_to(meta)):
            raise ManagedFilesError("managed_paths_overlap_or_escape")
        embed = embed_dir / "embedding_cache.npz"
        if any(path.is_relative_to(meta) or path.is_relative_to(directory) or path == embed
               for path in _reserved_paths(environ, audit)):
            raise ManagedFilesError("managed_paths_overlap_active_state")

        removals: list[tuple[Path, tuple]] = []
        receipt = {"backups_deleted": 0, "backups_retained": 0,
                   "orphan_manifests_deleted": 0, "metadata_cache_deleted": 0,
                   "embedding_cache_deleted": 0}
        if directory.exists():
            entries = {path.name: path for path in directory.iterdir()}
            manifests = {}
            for name, path in entries.items():
                _safe_path(path)
                if BACKUP_NAME.fullmatch(name):
                    continue
                if not name.endswith(MANIFEST_SUFFIX) or not BACKUP_NAME.fullmatch(name[:-len(MANIFEST_SUFFIX)]):
                    raise ManagedFilesError("unexpected_backup_entry")
                manifests[name[:-len(MANIFEST_SUFFIX)]] = (path, *_manifest(path, clock))
            for name, path in entries.items():
                if not BACKUP_NAME.fullmatch(name):
                    continue
                if name not in manifests:
                    raise ManagedFilesError("unregistered_backup_requires_adoption")
                manifest_path, record, manifest_snapshot = manifests.pop(name)
                digest, snapshot = _backup(path)
                if digest != record["sha256"] or snapshot[4] != record["size"]:
                    raise ManagedFilesError("managed_backup_content_changed")
                if clock - _timestamp(record["retention_origin_utc"]) >= timedelta(days=BACKUP_CLEANUP_DAYS):
                    removals.extend(((path, snapshot), (manifest_path, manifest_snapshot)))
                    receipt["backups_deleted"] += 1
                else:
                    receipt["backups_retained"] += 1
            # Resume the safe second unlink after a crash between backup and manifest.
            for path, _, snapshot in manifests.values():
                removals.append((path, snapshot))
                receipt["orphan_manifests_deleted"] += 1
        if meta.exists():
            for path in meta.iterdir():
                if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                    raise ManagedFilesError("unexpected_metadata_cache_entry")
                value, snapshot = _read(path, _MAX_META_BYTES)
                _json(value)
                removals.append((path, snapshot))
                receipt["metadata_cache_deleted"] += 1
        if embed_dir.exists() and any(path.name.startswith("embedding_cache.npz.")
                                      for path in embed_dir.iterdir()):
            raise ManagedFilesError("unexpected_embedding_cache_entry")
        if os.path.lexists(embed):
            removals.append((embed, _embedding(embed)))
            receipt["embedding_cache_deleted"] = 1
        for path, snapshot in removals:
            _unlink(path, snapshot)
        return receipt
    except OSError:
        raise ManagedFilesError("managed_files_unavailable") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    adopt = actions.add_parser("adopt-legacy", help="Register an independently reviewed backup origin")
    adopt.add_argument("--directory", required=True, type=Path)
    adopt.add_argument("--name", required=True)
    adopt.add_argument("--retention-origin", required=True,
                       help="Reviewed UTC lower bound of original creation; never first-observed time or mtime")
    adopt.add_argument("--expected-size", required=True, type=int)
    adopt.add_argument("--expected-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = register_legacy_backup(args.directory, name=args.name,
            retention_origin=_timestamp(args.retention_origin), expected_size=args.expected_size,
            expected_sha256=args.expected_sha256)
    except ManagedFilesError as error:
        print(json.dumps({"event": "managed_files_failed", "code": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({"event": "managed_backup_adopted", **receipt}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
