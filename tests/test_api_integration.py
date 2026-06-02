"""Integration tests for API endpoints.

Tests authentication, chat, and voucher endpoints with actual HTTP requests
via httpx AsyncClient (ASGI transport, no server process needed).
"""

import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """Create an httpx.AsyncClient bound to the FastAPI app via ASGI transport.

    Uses a temporary database so tests do not pollute the real data/ember.db.
    """
    import database
    import helpers.auth as auth_helpers

    test_db = tmp_path / "ember.db"
    test_sessions = tmp_path / "sessions"
    test_sessions.mkdir()

    # Patch DB path and session directory before any DB operations
    monkeypatch.setattr(database, "DB_PATH", test_db)
    monkeypatch.setattr(auth_helpers, "SESSION_DIR", test_sessions)

    # Manually initialize database with the test path
    await database.init_db()
    await database.seed_default_rules()

    # Import server and patch out heavy startup (agents/workspace)
    import server as server_module
    from unittest.mock import AsyncMock, MagicMock
    from agentscope.message import Msg

    # Replace startup handlers to avoid agent/workspace initialization
    server_module.app.router.on_startup = []
    server_module.app.router.on_shutdown = []

    # Build a mock intent_agent that the chat route can call
    intent_agent = AsyncMock()
    intent_agent.observe = AsyncMock()

    async def _fake_reply(inputs=None):
        """Async generator mimicking agent._reply."""
        yield Msg(
            name="assistant",
            content='{"intent":"chat","reply":"mock reply","business_type":null,"transaction":null}',
            metadata={
                "parse_result": {
                    "intent": "chat",
                    "reply": "mock reply",
                    "business_type": None,
                    "transaction": None,
                }
            },
        )
    intent_agent._reply = _fake_reply

    # Set required app.state attributes
    server_module.app.state.intent_agent = intent_agent
    server_module.app.state.voucher_agent = MagicMock()
    server_module.app.state.ocr_agent = MagicMock()
    server_module.app.state.workspace = MagicMock()

    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=server_module.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_token(client):
    """Login as default admin and return the token."""
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200
    data = resp.json()
    return data["token"]


# ---------------------------------------------------------------------------
# 1. Authentication flow
# ---------------------------------------------------------------------------

class TestAuthLogin:
    """POST /api/auth/login"""

    @pytest.mark.asyncio
    async def test_login_valid_credentials(self, client):
        resp = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "user" in data
        assert isinstance(data["token"], str)
        assert len(data["token"]) > 0
        assert data["user"]["username"] == "admin"

    @pytest.mark.asyncio
    async def test_login_invalid_password(self, client):
        resp = await client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert isinstance(data["error"], str)

    @pytest.mark.asyncio
    async def test_login_invalid_username(self, client):
        resp = await client.post("/api/auth/login", json={"username": "nonexistent", "password": "whatever"})
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert isinstance(data["error"], str)

    @pytest.mark.asyncio
    async def test_login_empty_username(self, client):
        resp = await client.post("/api/auth/login", json={"username": "", "password": "admin123"})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert isinstance(data["error"], str)

    @pytest.mark.asyncio
    async def test_login_empty_password(self, client):
        resp = await client.post("/api/auth/login", json={"username": "admin", "password": ""})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert isinstance(data["error"], str)


class TestAuthMe:
    """GET /api/auth/me"""

    @pytest.mark.asyncio
    async def test_me_with_valid_token(self, client, admin_token):
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "user" in data
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "admin"

    @pytest.mark.asyncio
    async def test_me_with_invalid_token(self, client):
        resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer invalidtoken123"})
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert isinstance(data["error"], str)

    @pytest.mark.asyncio
    async def test_me_without_auth_header(self, client):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert isinstance(data["error"], str)


# ---------------------------------------------------------------------------
# 2. Chat endpoint
# ---------------------------------------------------------------------------

class TestChat:
    """POST /api/chat (SSE streaming response)"""

    @pytest.mark.asyncio
    async def test_chat_with_valid_auth_and_message(self, client, admin_token):
        """Authenticated request with a message returns SSE stream."""
        resp = await client.post(
            "/api/chat",
            json={"message": "你好"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        body = resp.text
        assert "data:" in body
        events = _parse_sse_events(body)
        assert len(events) > 0
        for event in events:
            assert isinstance(event, dict)

    @pytest.mark.asyncio
    async def test_chat_without_auth(self, client):
        """Unauthenticated request returns an SSE error event (not a hard 401)."""
        resp = await client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        events = _parse_sse_events(resp.text)
        assert len(events) >= 1
        assert events[0].get("type") == "error"
        assert isinstance(events[0].get("reply", events[0].get("error", "")), str)

    @pytest.mark.asyncio
    async def test_chat_with_empty_message(self, client, admin_token):
        """Empty message returns a help/prompt SSE result."""
        resp = await client.post(
            "/api/chat",
            json={"message": ""},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        assert len(events) >= 1
        result_event = events[-1]
        assert result_event.get("type") == "result"
        assert isinstance(result_event.get("reply", ""), str)
        assert len(result_event["reply"]) > 0


# ---------------------------------------------------------------------------
# 3. Voucher endpoints
# ---------------------------------------------------------------------------

class TestVouchers:
    """GET /api/vouchers"""

    @pytest.mark.asyncio
    async def test_vouchers_with_auth(self, client, admin_token):
        """Authenticated admin can list vouchers (may be empty)."""
        resp = await client.get(
            "/api/vouchers",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "vouchers" in data
        assert "total" in data
        assert isinstance(data["vouchers"], list)
        assert isinstance(data["total"], int)

    @pytest.mark.asyncio
    async def test_vouchers_without_auth(self, client):
        """Unauthenticated request returns 401."""
        resp = await client.get("/api/vouchers")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data or "error" in data
        error_msg = data.get("detail", data.get("error", ""))
        assert isinstance(error_msg, str)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sse_events(body: str) -> list[dict]:
    """Parse SSE data lines from a response body into a list of dicts."""
    events = []
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            payload = line[6:]
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass
    return events
