"""Bounded, TLS-verified Messages API transport with no retries or redirects."""
from __future__ import annotations

import http.client
import json
import ssl
import time
from types import SimpleNamespace

from ._semantic_v2 import _reject_constant, _unique_object

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AnthropicHTTPError(Exception):
    def __init__(self, status_code: int, request_id: str | None = None):
        super().__init__(f"Anthropic HTTP status {status_code}")
        self.status_code = status_code
        self.request_id = request_id


def _objects(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _objects(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_objects(item) for item in value]
    return value


class AnthropicHTTP:
    def __init__(self, api_key: str, timeout: float):
        self._key = api_key
        self.timeout = timeout
        self.messages = self

    def create(self, **payload):
        deadline = time.monotonic() + self.timeout

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("semantic request deadline exceeded")
            return value

        connection = http.client.HTTPSConnection(
            "api.anthropic.com", timeout=remaining(), context=ssl.create_default_context(),
        )
        try:
            connection.connect()
            socket = connection.sock
            socket.settimeout(remaining())
            connection.request("POST", "/v1/messages", body=json.dumps(
                payload, ensure_ascii=False,
            ).encode("utf-8"), headers={
                "x-api-key": self._key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                "user-agent": "ugh-audit-core/semantic-v2",
            })
            socket.settimeout(remaining())
            response = connection.getresponse()
            request_id = response.getheader("request-id")
            if response.status != 200:
                # Do not read/log provider error bodies: they can echo input.
                raise AnthropicHTTPError(response.status, request_id)
            chunks = []
            size = 0
            while True:
                socket.settimeout(remaining())
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ValueError("semantic response exceeds limit")
                if response.isclosed():
                    break
            remaining()
            result = _objects(json.loads(b"".join(chunks), object_pairs_hook=_unique_object,
                                         parse_constant=_reject_constant))
            result._request_id = request_id
            return result
        finally:
            connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._key = ""
