from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Optional

from .reference.golden_store import GoldenStore
from .storage.audit_db import AuditDB

_db: Optional[AuditDB] = None
_golden: Optional[GoldenStore] = None
_lock = Lock()


def get_db() -> AuditDB:
    """Return a shared, lazily initialized AuditDB instance."""
    global _db
    if _db is None:
        with _lock:
            if _db is None:
                db_path = os.environ.get("UGH_AUDIT_DB")
                _db = AuditDB(db_path=Path(db_path) if db_path else None)
    return _db


def get_golden() -> GoldenStore:
    """Return a shared, lazily initialized GoldenStore instance."""
    global _golden
    if _golden is None:
        with _lock:
            if _golden is None:
                _golden = GoldenStore()
    return _golden


def configure(
    db: Optional[AuditDB] = None,
    golden: Optional[GoldenStore] = None,
) -> None:
    """Inject test dependencies or override shared singletons."""
    global _db, _golden
    if db is not None:
        _db = db
    if golden is not None:
        _golden = golden


def reset() -> None:
    """Clear shared singletons."""
    global _db, _golden
    _db = None
    _golden = None
