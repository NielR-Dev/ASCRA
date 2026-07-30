"""E2E: POST /api/pki-reply endpoint contract tests."""
from __future__ import annotations

import pytest
import httpx

from tests.factories import make_pki_reply_payload

pytestmark = pytest.mark.e2e


class TestPkiReplyContract:
    @pytest.mark.asyncio
    async def test_missing_workflow_id_returns_400(self, async_client: httpx.AsyncClient) -> None:
        payload = make_pki_reply_payload()
        del payload["workflow_id"]
        resp = await async_client.post("/api/pki-reply", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_workflow_id"

    @pytest.mark.asyncio
    async def test_missing_cer_blob_url_returns_400(self, async_client: httpx.AsyncClient) -> None:
        payload = make_pki_reply_payload()
        del payload["cer_blob_url"]
        resp = await async_client.post("/api/pki-reply", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_cer_blob_url"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post(
            "/api/pki-reply",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    @pytest.mark.asyncio
    async def test_valid_payload_returns_202(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        """A well-formed pki-reply returns 202 accepted."""
        from tests.factories import make_canonical_alert

        cn = f"{e2e_cn_prefix}-pki.test.test-domain.com"
        alert = make_canonical_alert(cn=cn, san=[cn])
        start = await async_client.post("/api/orchestrate", json={"alert": alert})
        assert start.status_code == 200
        workflow_id = start.json()["workflow_id"]

        blob_url = (
            f"https://sslprodcerarti.blob.core.windows.net/cer-artifacts/{workflow_id}.cer"
        )
        payload = make_pki_reply_payload(workflow_id=workflow_id, cer_blob_url=blob_url)
        resp = await async_client.post("/api/pki-reply", json=payload)
        assert resp.status_code == 202, f"Unexpected: {resp.text}"
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["workflow_id"] == workflow_id
