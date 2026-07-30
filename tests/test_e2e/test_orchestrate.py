"""E2E: POST /api/orchestrate endpoint contract tests."""
from __future__ import annotations

import pytest
import httpx

from tests.factories import make_canonical_alert

pytestmark = pytest.mark.e2e


class TestOrchestrateContract:
    @pytest.mark.asyncio
    async def test_missing_alert_field_returns_400(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post("/api/orchestrate", json={"not_alert": {}})
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "missing_alert"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post(
            "/api/orchestrate",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    @pytest.mark.asyncio
    async def test_valid_alert_returns_200(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        """A well-formed alert returns 200 with workflow_id in the response."""
        cn = f"{e2e_cn_prefix}-contract.test.test-domain.com"
        alert = make_canonical_alert(cn=cn, san=[cn])
        resp = await async_client.post("/api/orchestrate", json={"alert": alert})
        assert resp.status_code == 200, f"Unexpected: {resp.text}"
        body = resp.json()
        assert "workflow_id" in body
        assert body.get("state") == "PARSED"
        assert body.get("schema_version") == 1

    @pytest.mark.asyncio
    async def test_response_includes_correlation_id_header(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        cn = f"{e2e_cn_prefix}-hdr.test.test-domain.com"
        alert = make_canonical_alert(cn=cn, san=[cn])
        resp = await async_client.post("/api/orchestrate", json={"alert": alert})
        assert resp.status_code == 200
        assert "X-Correlation-Id" in resp.headers

    @pytest.mark.asyncio
    async def test_correlation_id_header_echoed(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        import uuid

        cn = f"{e2e_cn_prefix}-echo.test.test-domain.com"
        alert = make_canonical_alert(cn=cn, san=[cn])
        correlation_id = str(uuid.uuid4())
        resp = await async_client.post(
            "/api/orchestrate",
            json={"alert": alert},
            headers={"X-Correlation-Id": correlation_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Correlation-Id") == correlation_id
