"""E2E: GET /api/status health check (no query params → 200 healthy)."""
from __future__ import annotations

import pytest
import httpx


pytestmark = pytest.mark.e2e


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, async_client: httpx.AsyncClient) -> None:
        """GET /api/status with no params must return HTTP 200."""
        resp = await async_client.get("/api/status")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    @pytest.mark.asyncio
    async def test_health_body_has_status_field(self, async_client: httpx.AsyncClient) -> None:
        """Response body must include status=healthy."""
        resp = await async_client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "healthy"

    @pytest.mark.asyncio
    async def test_health_body_has_orchestrator_enabled(self, async_client: httpx.AsyncClient) -> None:
        """Response body must include orchestrator_enabled field."""
        resp = await async_client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "orchestrator_enabled" in body

    @pytest.mark.asyncio
    async def test_health_content_type_is_json(self, async_client: httpx.AsyncClient) -> None:
        """Health response must be application/json."""
        resp = await async_client.get("/api/status")
        assert "application/json" in resp.headers.get("content-type", "")
