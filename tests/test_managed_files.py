"""Only synthetic temporary artifacts; no model, host or production data."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import zipfile

import numpy as np
import pytest

from ugh_audit import managed_files as managed


NOW = datetime(2026, 10, 29, 16, tzinfo=timezone.utc)
BACKUP = "audit-before-5a6bb677fcb9-458d152300154c808466ec28ec5d7b23.sqlite"
SECRET = "synthetic audit text never belongs in receipts"


def database(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE audit_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "question TEXT, response TEXT, created_at TEXT)")
        conn.execute("INSERT INTO audit_runs(question,response,created_at) VALUES(?,?,?)",
                     (SECRET, SECRET, NOW.isoformat()))
        conn.commit()


@pytest.fixture
def environment(tmp_path):
    audit = tmp_path / "audit.db"
    database(audit)
    return {"UGH_AUDIT_DB": str(audit), "UGH_META_CACHE_DIR": str(tmp_path / "meta_cache"),
            "UGH_AUDIT_CACHE_DIR": str(tmp_path)}


def backup(environment, *, age=0, registered=True, name=BACKUP):
    directory = Path(environment["UGH_AUDIT_DB"]).parent / "deployment-backups"
    directory.mkdir(exist_ok=True)
    path = directory / name
    database(path)
    if registered:
        managed.register_managed_backup(path, now=NOW - timedelta(days=age))
    return path


def metadata(environment):
    directory = Path(environment["UGH_META_CACHE_DIR"])
    directory.mkdir(exist_ok=True)
    path = directory / ("a" * 64 + ".json")
    path.write_text(json.dumps({"requirements": [SECRET]}), encoding="utf-8")
    return path


def embedding(environment):
    path = Path(environment["UGH_AUDIT_CACHE_DIR"]) / "embedding_cache.npz"
    np.savez(path, **{"b" * 24: np.array([0.5, 1.5], dtype=np.float32)})
    return path


def assert_failed_unchanged(environment, paths, match=None):
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(managed.ManagedFilesError, match=match):
        managed.clean_managed_files(environment, now=NOW)
    assert {path: path.read_bytes() for path in paths} == before


def test_no_artifacts_no_directories_created(environment):
    root = Path(environment["UGH_AUDIT_DB"]).parent
    before = list(root.iterdir())
    receipt = managed.clean_managed_files(environment, now=NOW)
    assert all(value == 0 for value in receipt.values())
    assert list(root.iterdir()) == before


def test_known_caches_removed_fresh_backup_and_database_preserved(environment, capsys):
    old_cache, embed = metadata(environment), embedding(environment)
    fresh = backup(environment)
    before_audit = Path(environment["UGH_AUDIT_DB"]).read_bytes()
    receipt = managed.clean_managed_files(environment, now=NOW)
    assert receipt == {"backups_deleted": 0, "backups_retained": 1,
                       "orphan_manifests_deleted": 0, "metadata_cache_deleted": 1,
                       "embedding_cache_deleted": 1}
    assert not old_cache.exists() and not embed.exists() and fresh.exists()
    assert Path(environment["UGH_AUDIT_DB"]).read_bytes() == before_audit
    captured = capsys.readouterr()
    assert SECRET not in json.dumps(receipt) + captured.out + captured.err
    assert old_cache.parent.is_dir() and fresh.parent.is_dir()


@pytest.mark.parametrize("age, deleted", [(timedelta(days=29, microseconds=-1), False),
                                        (timedelta(days=29), True), (timedelta(days=100), True)])
def test_backup_cutoff_uses_origin_not_newer_filesystem_time(environment, age, deleted):
    path = backup(environment, registered=False)
    managed.register_managed_backup(path, now=NOW - age)
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    receipt = managed.clean_managed_files(environment, now=NOW)
    assert receipt["backups_deleted"] == int(deleted)
    assert path.exists() is not deleted
    assert path.with_name(path.name + managed.MANIFEST_SUFFIX).exists() is not deleted


def test_old_mtime_cannot_delete_new_backup(environment):
    path = backup(environment)
    os.utime(path, (1, 1))
    assert managed.clean_managed_files(environment, now=NOW)["backups_retained"] == 1
    assert path.exists()


def test_unknown_backup_requires_explicit_evidence_before_any_cache_deletion(environment):
    path, cache = backup(environment, registered=False), metadata(environment)
    assert_failed_unchanged(environment, [path, cache], "unregistered_backup")


def test_reviewed_lower_bound_adoption_not_mtime(environment):
    path = backup(environment, registered=False)
    origin = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
    os.utime(path, (1, 1))
    managed.register_legacy_backup(path.parent, name=path.name, retention_origin=origin,
        expected_size=path.stat().st_size, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), now=NOW)
    manifest = json.loads(path.with_name(path.name + managed.MANIFEST_SUFFIX).read_text())
    assert manifest["origin_kind"] == "observed_lower_bound"
    assert manifest["retention_origin_utc"] == origin.isoformat()
    assert managed.clean_managed_files(environment, now=NOW)["backups_retained"] == 1
    assert managed.clean_managed_files(environment, now=origin + timedelta(days=29))["backups_deleted"] == 1


@pytest.mark.parametrize("wrong", ["size", "hash", "future", "name"])
def test_legacy_adoption_requires_matching_reviewed_evidence(environment, wrong):
    path = backup(environment, registered=False)
    arguments = dict(name=path.name, retention_origin=NOW, expected_size=path.stat().st_size,
                     expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), now=NOW)
    if wrong == "size":
        arguments["expected_size"] += 1
    elif wrong == "hash":
        arguments["expected_sha256"] = "0" * 64
    elif wrong == "future":
        arguments["retention_origin"] += timedelta(seconds=1)
    else:
        arguments["name"] = "../" + path.name
    before = path.read_bytes()
    with pytest.raises(managed.ManagedFilesError):
        managed.register_legacy_backup(path.parent, **arguments)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_registration_cannot_reset_origin(environment):
    path = backup(environment, age=29)
    manifest = path.with_name(path.name + managed.MANIFEST_SUFFIX)
    before = manifest.read_bytes()
    with pytest.raises(managed.ManagedFilesError, match="registration_failed"):
        managed.register_managed_backup(path, now=NOW)
    assert manifest.read_bytes() == before
    assert managed.clean_managed_files(environment, now=NOW)["backups_deleted"] == 1


def test_changed_backup_content_rejected_before_other_deletions(environment):
    path, cache = backup(environment, age=29), metadata(environment)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE audit_runs SET response='changed'")
        conn.commit()
    assert_failed_unchanged(environment, [path, cache], "content_changed")


@pytest.mark.parametrize("kind", ["unknown_file", "directory", "sqlite_sidecar", "corrupt_json",
                                  "duplicate_json", "wrong_root_json", "future_origin", "manifest_name"])
def test_backup_directory_is_fail_closed(environment, kind):
    path, cache = backup(environment, age=29), metadata(environment)
    manifest = path.with_name(path.name + managed.MANIFEST_SUFFIX)
    if kind == "unknown_file":
        (path.parent / "unknown.sqlite").write_text(SECRET)
    elif kind == "directory":
        (path.parent / "nested").mkdir()
    elif kind == "sqlite_sidecar":
        Path(str(path) + "-wal").write_text(SECRET)
    elif kind == "corrupt_json":
        manifest.write_text("{")
    elif kind == "duplicate_json":
        manifest.write_text('{"version":1,"version":1}')
    elif kind == "wrong_root_json":
        manifest.write_text("[]")
    else:
        value = json.loads(manifest.read_text())
        value["retention_origin_utc" if kind == "future_origin" else "backup_name"] = (
            (NOW + timedelta(seconds=1)).isoformat() if kind == "future_origin" else "../audit.db")
        manifest.write_text(json.dumps(value))
    assert_failed_unchanged(environment, [path, cache])


def test_valid_orphan_manifest_resumes_second_unlink(environment):
    path = backup(environment, age=29)
    manifest = path.with_name(path.name + managed.MANIFEST_SUFFIX)
    path.unlink()
    receipt = managed.clean_managed_files(environment, now=NOW)
    assert receipt["orphan_manifests_deleted"] == 1
    assert not manifest.exists()


@pytest.mark.parametrize("content", ["[]", "null", "not JSON", '{"x":NaN}', '{"x":1,"x":2}'])
def test_invalid_metadata_blocks_even_expired_backup_deletion(environment, content):
    path, cache = backup(environment, age=29), metadata(environment)
    cache.write_text(content)
    assert_failed_unchanged(environment, [path, cache], "invalid_managed_json")


def test_unknown_metadata_filename_blocks_deletion(environment):
    path, cache = backup(environment, age=29), metadata(environment)
    (cache.parent / "operator-notes.json").write_text("{}")
    assert_failed_unchanged(environment, [path, cache], "unexpected_metadata")


@pytest.mark.parametrize("kind", ["pickle", "nested_name", "bad_shape", "bad_zip"])
def test_embedding_content_rejected_without_pickle_loading(environment, kind):
    path, cache = backup(environment, age=29), metadata(environment)
    embed = embedding(environment)
    if kind == "pickle":
        np.savez(embed, **{"c" * 24: np.array([{"private": SECRET}], dtype=object)})
    elif kind == "nested_name":
        with zipfile.ZipFile(embed, "w") as archive:
            archive.writestr("../outside.npy", SECRET)
    elif kind == "bad_shape":
        np.savez(embed, **{"c" * 24: np.ones((2, 2))})
    else:
        embed.write_text(SECRET)
    assert_failed_unchanged(environment, [path, cache, embed], "invalid_embedding_cache")


@pytest.mark.parametrize("target", ["backup", "manifest", "metadata", "embedding", "audit"])
def test_hardlinks_rejected_before_any_deletion(environment, tmp_path, target):
    path, cache, embed = backup(environment, age=29), metadata(environment), embedding(environment)
    linked = {"backup": path, "manifest": path.with_name(path.name + managed.MANIFEST_SUFFIX),
              "metadata": cache, "embedding": embed, "audit": Path(environment["UGH_AUDIT_DB"])}[target]
    os.link(linked, tmp_path / "unrelated-hardlink")
    assert_failed_unchanged(environment, [path, cache, embed], "regular_file_required")


def test_parent_symlink_rejected(environment, tmp_path):
    cache = metadata(environment)
    linked = tmp_path / "linked-cache"
    try:
        linked.symlink_to(cache.parent, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit creating symlinks")
    environment["UGH_META_CACHE_DIR"] = str(linked)
    assert_failed_unchanged(environment, [cache], "managed_link_rejected")


@pytest.mark.parametrize("bad", ["root", "outside", "overlap", "relative", "dotdot"])
def test_unsafe_cache_roots_rejected(environment, tmp_path, bad):
    cache = metadata(environment)
    if bad == "root":
        environment["UGH_META_CACHE_DIR"] = str(tmp_path)
    elif bad == "outside":
        environment["UGH_META_CACHE_DIR"] = str(tmp_path.parent / "somewhere-else")
    elif bad == "overlap":
        environment["UGH_META_CACHE_DIR"] = str(tmp_path / "deployment-backups")
    elif bad == "relative":
        environment["UGH_META_CACHE_DIR"] = "meta_cache"
    else:
        environment["UGH_META_CACHE_DIR"] = str(tmp_path / "meta_cache" / ".." / "meta_cache")
    assert_failed_unchanged(environment, [cache])


def test_file_changed_between_preflight_and_unlink_is_not_removed(environment, monkeypatch):
    cache = metadata(environment)
    unlink = managed._unlink
    def changed(path, snapshot):
        path.write_text('{"changed":true}')
        unlink(path, snapshot)
    monkeypatch.setattr(managed, "_unlink", changed)
    with pytest.raises(managed.ManagedFilesError, match="managed_file_changed"):
        managed.clean_managed_files(environment, now=NOW)
    assert cache.exists()


@pytest.mark.parametrize("variable", ["UGH_INVITE_FILE", "UGH_USAGE_DB"])
def test_active_config_with_cache_compatible_name_is_never_deleted(environment, variable):
    path, cache = backup(environment, age=29), metadata(environment)
    environment[variable] = str(cache)
    assert_failed_unchanged(environment, [path, cache], "overlap_active_state")


def test_embedding_path_cannot_alias_live_invite_registry(environment):
    path, embed = backup(environment, age=29), embedding(environment)
    environment["UGH_INVITE_FILE"] = str(embed)
    assert_failed_unchanged(environment, [path, embed], "overlap_active_state")


def test_backup_filename_cannot_alias_live_usage_database(environment):
    path = backup(environment, age=29)
    environment["UGH_USAGE_DB"] = str(path)
    assert_failed_unchanged(environment, [path], "overlap_active_state")


def test_unfinished_embedding_write_requires_review(environment):
    path, cache = backup(environment, age=29), metadata(environment)
    temporary = Path(environment["UGH_AUDIT_CACHE_DIR"]) / "embedding_cache.npz.12-deadbeef.tmp"
    temporary.write_text(SECRET)
    assert_failed_unchanged(environment, [path, cache, temporary], "unexpected_embedding_cache_entry")


def test_unhashable_manifest_field_is_fixed_error(environment):
    path, cache = backup(environment, age=29), metadata(environment)
    manifest = path.with_name(path.name + managed.MANIFEST_SUFFIX)
    value = json.loads(manifest.read_text())
    value["origin_kind"] = [SECRET]
    manifest.write_text(json.dumps(value))
    assert_failed_unchanged(environment, [path, cache], "invalid_backup_manifest")


def test_corrupt_sqlite_cannot_be_registered(environment):
    path = backup(environment, registered=False)
    path.write_text(SECRET)
    with pytest.raises(managed.ManagedFilesError, match="invalid_managed_backup_database"):
        managed.register_managed_backup(path, now=NOW)
    assert not path.with_name(path.name + managed.MANIFEST_SUFFIX).exists()


def test_adoption_cli_outputs_only_counts_and_fixed_errors(environment, capsys):
    path = backup(environment, registered=False)
    args = ["adopt-legacy", "--directory", str(path.parent), "--name", path.name,
            "--retention-origin", "2026-10-28T15:00:00Z", "--expected-size", str(path.stat().st_size),
            "--expected-sha256", hashlib.sha256(path.read_bytes()).hexdigest()]
    assert managed.main(args) == 0
    success = capsys.readouterr()
    assert json.loads(success.out) == {"event": "managed_backup_adopted", "backups_registered": 1}
    assert managed.main(args) == 1
    error = capsys.readouterr()
    assert json.loads(error.err)["code"] == "managed_backup_registration_failed"
    assert SECRET not in success.out + success.err + error.out + error.err
    assert path.name not in success.out + error.err
