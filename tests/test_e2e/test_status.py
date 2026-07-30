"""E2E: GET /api/status workflow query endpoint tests."""
from __future__ import annotations

import pytest
import httpx

from tests.factories import make_canonical_alert

pytestmark = pytest.mark.e2e


class TestStatusEndpoint:
    @pytest.mark.asyncio
    async def test_missing_params_returns_healthy(self, async_client: httpx.AsyncClient) -> None:
        """No params → health check path (200), not a missing_query_param error."""
        resp = await async_client.get("/api/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_workflow_id_returns_404(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/api/status", params={"workflow_id": "wf_nonexistent_00001"})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_unknown_cn_returns_404(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/api/status", params={"cn": "does-not-exist.test-domain.com"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_known_workflow_returns_safe_fields(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        """Start a workflow then query its status — response must not leak sensitive fields."""
        cn = f"{e2e_cn_prefix}-status.test.test-domain.com"
        alert = make_canonical_alert(cn=cn, san=[cn])
        start_resp = await async_client.post("/api/orchestrate", json={"alert": alert})
        assert start_resp.status_code == 200
        workflow_id = start_resp.json()["workflow_id"]

        status_resp = await async_client.get("/api/status", params={"workflow_id": workflow_id})
        assert status_resp.status_code == 200
        body = status_resp.json()

        # Must include required safe fields
        for field in ("workflow_id", "state", "cn", "san", "owning_application", "schema_version"):
            assert field in body, f"Missing field: {field}"

        # Must NOT include private key material
        body_text = str(body)
        for forbidden in ("PRIVATE KEY", "BEGIN RSA", "BEGIN CERTIFICATE REQUEST"):
            assert forbidden not in body_text, f"Sensitive field leaked: {forbidden}"

    @pytest.mark.asyncio
    async def test_content_type_is_json(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/api/status", params={"workflow_id": "wf_any"})
        assert "application/json" in resp.headers.get("content-type", "")
