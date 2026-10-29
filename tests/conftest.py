"""Offline test boundaries and explicitly requested semantic control fixtures."""
import json
import socket

import pytest


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    """No regression may silently use an external model or download a model."""
    original = socket.getaddrinfo

    def local_only(host, *args, **kwargs):
        if host not in (None, "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1"):
            raise AssertionError("External network is disabled during regression tests")
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)


@pytest.fixture
def semantic_complete_judge():
    """A grounded protocol double for tests isolating legacy numerical behavior.

    This fixture does not test semantic accuracy; production-model recorded
    replays and the separate fail-closed tests cover other behaviors.
    """
    from ugh_audit.semantic_matching import MODEL

    def judge(requirement, answer, *, timeout_seconds):
        value = {"status": "fulfilled", "missing_kind": None, "evidence": [answer],
                 "reason": "Explicit semantic test control for isolated legacy regression"}
        return {**value, "unknown_source": None, "api_called": True,
                "requested_model": MODEL, "returned_model": MODEL,
                "stop_reason": "end_turn", "raw_response": json.dumps(value, ensure_ascii=False)}

    return judge


@pytest.fixture(autouse=True)
def explicit_local_access(monkeypatch):
    """Legacy unit tests opt into trusted local mode; beta tests override this."""
    monkeypatch.setenv("UGH_ACCESS_MODE", "local")
