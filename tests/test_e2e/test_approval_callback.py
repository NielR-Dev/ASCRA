"""E2E: POST /api/approval-callback endpoint contract tests."""
from __future__ import annotations

import pytest
import httpx

from tests.factories import make_approval_payload

pytestmark = pytest.mark.e2e


class TestApprovalCallbackContract:
    @pytest.mark.asyncio
    async def test_missing_thread_id_returns_400(self, async_client: httpx.AsyncClient) -> None:
        payload = make_approval_payload()
        del payload["thread_id"]
        resp = await async_client.post("/api/approval-callback", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_thread_id"

    @pytest.mark.asyncio
    async def test_missing_decision_returns_400(self, async_client: httpx.AsyncClient) -> None:
        payload = make_approval_payload()
        del payload["decision"]
        resp = await async_client.post("/api/approval-callback", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_decision"

    @pytest.mark.asyncio
    async def test_missing_approver_returns_400(self, async_client: httpx.AsyncClient) -> None:
        payload = make_approval_payload()
        del payload["approver"]
        resp = await async_client.post("/api/approval-callback", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_approver"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post(
            "/api/approval-callback",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    @pytest.mark.asyncio
    async def test_valid_approved_payload_accepted(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        """A well-formed APPROVED callback returns 202 accepted."""
        from tests.factories import make_canonical_alert

        cn = f"{e2e_cn_prefix}-appr.test.test-domain.com"
        alert = make_canonical_alert(cn=cn, san=[cn])
        start = await async_client.post("/api/orchestrate", json={"alert": alert})
        assert start.status_code == 200
        workflow_id = start.json()["workflow_id"]

        payload = make_approval_payload(workflow_id=workflow_id, thread_id=workflow_id)
        resp = await async_client.post("/api/approval-callback", json=payload)
        assert resp.status_code == 202, f"Unexpected: {resp.text}"
        assert resp.json()["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_valid_rejected_payload_accepted(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        """A well-formed REJECTED callback returns 202 accepted."""
        from tests.factories import make_canonical_alert

        cn = f"{e2e_cn_prefix}-rej.test.test-domain.com"
        alert = make_canonical_alert(cn=cn, san=[cn])
        start = await async_client.post("/api/orchestrate", json={"alert": alert})
        assert start.status_code == 200
        workflow_id = start.json()["workflow_id"]

        payload = make_approval_payload(
            workflow_id=workflow_id,
            thread_id=workflow_id,
            decision="REJECTED",
            reasoning="Test rejection.",
        )
        resp = await async_client.post("/api/approval-callback", json=payload)
        assert resp.status_code == 202, f"Unexpected: {resp.text}"
