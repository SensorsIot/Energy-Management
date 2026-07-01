"""Security test case — secret handling (FSD §8.1 SEC-06, CWE-532 / ASVS V6).

The HA supervisor token authenticates every HA REST call. This asserts it is
used for auth but never leaks into logs or the entity payload.

`run.py` pulls an optional heavy dependency (`aiomqtt`) and does a `/config`
mkdir at import, so we stub the missing module and redirect the log dir to a
temp path before importing it.
"""

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Redirect the import-time persistent-log mkdir away from /config, and stub the
# optional dependency that is absent in the test env — both must happen before
# `import run`.
os.environ.setdefault("OCPP_LOG_DIR", tempfile.mkdtemp(prefix="ocpp-test-logs-"))
sys.modules.setdefault("aiomqtt", MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run  # noqa: E402

SENTINEL = "SECRET-TOKEN-DO-NOT-LEAK-abc123XYZ"


class _FakeResponse:
    """Async-context-manager stand-in for an aiohttp response."""

    status = 200

    async def text(self) -> str:
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class TestSecretHandling:
    """SEC-06: the supervisor token must not reach logs or HA entity payloads."""

    @pytest.mark.asyncio
    async def test_sec06_token_authenticates_but_never_leaks(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("SUPERVISOR_TOKEN", SENTINEL)
        mgr = run.HAEntityManager()

        # The token IS used for auth — that is required, not a leak.
        assert SENTINEL in mgr._headers["Authorization"]

        # Capture the request the client would send.
        captured: dict = {}

        def fake_post(url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

        mgr._session = MagicMock()
        mgr._session.post = fake_post

        with caplog.at_level(logging.DEBUG):
            await mgr.set_state(
                "sensor.wallbox_power", 3940, {"unit_of_measurement": "W"}
            )

        # The token must NOT appear in the request body (entity state/attributes)...
        assert SENTINEL not in json.dumps(captured["json"])
        # ...and must NOT appear in any log line.
        assert SENTINEL not in caplog.text
        assert all(SENTINEL not in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_sec06_missing_token_does_not_crash(self, monkeypatch) -> None:
        """No token configured → header carries the literal `None`, no exception."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("HASSIO_TOKEN", raising=False)
        mgr = run.HAEntityManager()
        assert mgr._token is None
        assert "Bearer" in mgr._headers["Authorization"]  # built without raising


class TestConnectionGuard:
    """SEC-04 (CWE-287): a stray charge-point id can't disrupt an active transaction."""

    @staticmethod
    def _server():
        return run.OCPPServer({"wallbox_id": "wallbox1"})

    @staticmethod
    def _active_handler(cp_id="wallbox1", txn=1):
        h = MagicMock()
        h.id = cp_id
        h.transaction_id = txn
        return h

    def test_sec04_rejects_foreign_id_during_active_transaction(self):
        srv = self._server()
        srv.charge_point = self._active_handler(cp_id="wallbox1", txn=5)
        assert srv._reject_duplicate_connection("rogue-device") is True

    def test_sec04_allows_same_id_reconnect(self):
        """The same wallbox reconnecting (e.g. after a network drop) is allowed."""
        srv = self._server()
        srv.charge_point = self._active_handler(cp_id="wallbox1", txn=5)
        assert srv._reject_duplicate_connection("wallbox1") is False

    def test_sec04_allows_foreign_id_when_no_active_transaction(self):
        srv = self._server()
        srv.charge_point = self._active_handler(cp_id="wallbox1", txn=None)
        assert srv._reject_duplicate_connection("rogue-device") is False

    def test_sec04_allows_first_connection(self):
        srv = self._server()
        srv.charge_point = None
        assert srv._reject_duplicate_connection("anything") is False
