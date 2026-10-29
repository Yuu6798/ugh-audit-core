"""Invite-only HTTP access and shared admission controls for the beta service."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
import time
from typing import Optional

import anyio
from starlette.responses import JSONResponse

from .usage_limits import Limits, LimitExceeded, UsageLimiter, UsageUnavailable

MAX_BODY_BYTES = 262144


class AccessError(Exception):
    def __init__(self, status: int, code: str, retry_after: Optional[int] = None):
        self.status, self.code, self.retry_after = status, code, retry_after
        super().__init__(code + (f"; retry_after={retry_after}" if retry_after else ""))


@dataclass(frozen=True)
class Principal:
    owner_id: Optional[str]
    token: Optional[str] = field(default=None, repr=False)


_principal: ContextVar[Optional[Principal]] = ContextVar("ugh_principal", default=None)
_limiter_lock = Lock()
_limiter_key = None
_limiter = None


def access_mode() -> str:
    mode = os.environ.get("UGH_ACCESS_MODE", "invite")
    if mode not in ("invite", "local"):
        raise AccessError(503, "access_configuration_invalid")
    return mode


def require_principal() -> Principal:
    principal = _principal.get()
    if principal is not None:
        return principal
    if access_mode() == "local":
        return Principal(None)
    raise AccessError(401, "authentication_required")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def authenticate(headers) -> Principal:
    if access_mode() == "local":
        return Principal(None)
    path = os.environ.get("UGH_INVITE_FILE")
    try:
        if not path:
            raise ValueError("Missing invite configuration")
        with Path(path).open("rb") as stream:
            raw = stream.read(262145)
        if len(raw) > 262144:
            raise ValueError("Oversized invite configuration")
        config = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(config, dict) or set(config) != {"users"}:
            raise ValueError("Invalid registry")
        users = config["users"]
        if not isinstance(users, list) or not users or len(users) > 1000:
            raise ValueError("Invalid users")
        digests = set()
        for user in users:
            if (not isinstance(user, dict)
                    or set(user) - {"user_id", "token_sha256", "enabled", "expires_at"}
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", user.get("user_id", ""))):
                raise ValueError("Invalid user")
            digest = user.get("token_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid digest")
            if digest in digests or type(user.get("enabled", True)) is not bool:
                raise ValueError("Invalid credential")
            digests.add(digest)
            expires = user.get("expires_at")
            if expires is not None and (type(expires) is not int or expires <= 0):
                raise ValueError("Invalid expiry")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise AccessError(503, "access_configuration_invalid") from None
    values = [value for key, value in headers if key.lower() == b"authorization"]
    if len(values) != 1:
        raise AccessError(401, "authentication_required")
    try:
        scheme, token = values[0].decode("ascii").split(" ")
        if scheme.lower() != "bearer" or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("Invalid token")
    except (ValueError, UnicodeError):
        raise AccessError(401, "invalid_credential") from None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    for user in users:
        if (hmac.compare_digest(digest, user["token_sha256"])
                and user.get("enabled", True)
                and (user.get("expires_at") is None or user["expires_at"] > time.time())):
            return Principal(user["user_id"], token)
    raise AccessError(401, "invalid_credential")


def usage_limiter() -> UsageLimiter:
    global _limiter, _limiter_key
    try:
        values = {
            name: int(os.environ.get(env, default)) for name, env, default in (
                ("requests_per_minute", "UGH_REQUESTS_PER_MINUTE", "60"),
                ("audits_per_day", "UGH_AUDITS_PER_DAY", "100"),
                ("global_audits_per_day", "UGH_GLOBAL_AUDITS_PER_DAY", "500"),
                ("concurrent_per_user", "UGH_CONCURRENT_PER_USER", "1"),
                ("concurrent_global", "UGH_CONCURRENT_GLOBAL", "4"),
            )
        }
        from . import dependencies
        path = Path(os.environ.get("UGH_USAGE_DB") or
                    dependencies.get_db().db_path.with_name("usage.db"))
        key = (str(path.resolve()), tuple(values.items()))
        with _limiter_lock:
            if key != _limiter_key:
                _limiter = UsageLimiter(path, Limits(**values))
                _limiter_key = key
            return _limiter
    except (ValueError, TypeError, OSError, sqlite3.Error, UsageUnavailable):
        raise AccessError(503, "usage_control_unavailable") from None


def _limit_error(exc):
    if isinstance(exc, LimitExceeded):
        return AccessError(429, exc.code, exc.retry_after)
    return AccessError(503, "usage_control_unavailable")


@contextmanager
def audit_access(db, retry_of=None):
    """Admit actual synchronous work; retain its slot through client cancellation."""
    principal = require_principal()
    if principal.owner_id is not None and retry_of is not None:
        if type(retry_of) is not int or retry_of <= 0 or db.get_by_id(
            retry_of, owner_id=principal.owner_id
        ) is None:
            raise AccessError(404, "audit_not_found")
    permit = None
    if principal.owner_id is not None:
        try:
            permit = usage_limiter().acquire_audit(principal.owner_id)
        except (LimitExceeded, UsageUnavailable) as exc:
            raise _limit_error(exc) from None
    try:
        if permit is not None:
            try:
                permit.ensure_valid()
            except UsageUnavailable:
                raise AccessError(503, "usage_control_unavailable") from None
        yield principal
    finally:
        if permit is not None:
            try:
                permit.release()
            except UsageUnavailable:
                raise AccessError(503, "usage_control_unavailable") from None


def validate_payload(question, response, reference=None, session_id=None, question_meta=None):
    for value, limit in ((question, 8000), (response, 32000), (reference, 32000), (session_id, 128)):
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise AccessError(422, "input_limit_exceeded")
    # JSON metadata is also bounded before optional diagnostics or generation.
    try:
        if len(json.dumps(question_meta, ensure_ascii=False).encode("utf-8")) > 131072:
            raise AccessError(422, "input_limit_exceeded")
    except (ValueError, TypeError, RecursionError):
        raise AccessError(422, "invalid_metadata") from None


def error_response(exc: AccessError):
    headers = {"Cache-Control": "no-store"}
    if exc.status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="ugh-audit"'
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse({"detail": exc.code}, status_code=exc.status, headers=headers)


class InviteMiddleware:
    """Protect REST, discovery and MCP; only liveness is anonymous by default."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == "/health" and scope.get("method") in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        try:
            principal = authenticate(scope.get("headers", []))
            if principal.owner_id is not None:
                try:
                    usage_limiter().check_request(principal.owner_id)
                except (LimitExceeded, UsageUnavailable) as exc:
                    raise _limit_error(exc) from None
            parts, size = [], 0
            try:
                with anyio.fail_after(15):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            raise AccessError(413, "request_body_too_large")
                        parts.append(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                raise AccessError(408, "request_body_timeout") from None
        except AccessError as exc:
            await error_response(exc)(scope, receive, send)
            return
        body, delivered = b"".join(parts), False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def private_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []),
                                                   (b"cache-control", b"no-store")]}
            await send(message)

        marker = _principal.set(principal)
        try:
            await self.app(scope, bounded_receive, private_send)
        finally:
            _principal.reset(marker)
