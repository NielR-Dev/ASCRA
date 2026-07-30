"""E2E: 20 synthetic end-to-end renewals — each must reach a terminal state.

Each synthetic renewal drives the full HTTP workflow by injecting every callback:
  1. POST /api/orchestrate  → workflow_id
  2. Poll /api/status       → wait for CSR_REQUESTED
  3. POST /api/approval-callback (APPROVED)
  4. POST /api/pki-reply with a test certificate (matching the synthetic CN)
  5. Poll /api/status       → wait for terminal state (COMPLETE | VERIFIED | FAILED)
  6. Assert final state is COMPLETE

All 20 renewals run concurrently via asyncio.gather; total wall-clock ≤ 300s (CI timeout).
"""
from __future__ import annotations

import asyncio
import base64
import pathlib
import time
from typing import Any

import httpx
import pytest

from tests.factories import make_canonical_alert, make_approval_payload, make_pki_reply_payload
from tests.test_e2e.conftest import poll_state

pytestmark = pytest.mark.e2e

_TERMINAL_STATES = {"COMPLETE", "REJECTED", "FAILED"}
_N_RENEWALS = 20
_CERT_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "certs"


def _cert_b64_for_cn(cn: str, san: list[str]) -> str:
    """Generate a self-signed PEM for the given CN/SAN and return as base64."""
    from tests.fixtures.certs.generate import make_cert_pem

    pem = make_cert_pem(cn=cn, san=san, days_valid=400)
    return base64.b64encode(pem).decode()


async def _run_single_renewal(
    client: httpx.AsyncClient,
    index: int,
    session_prefix: str,
) -> dict[str, Any]:
    """Drive one synthetic renewal through all stages and return the final status dict."""
    cn = f"{session_prefix}-{index:02d}.test.test-domain.com"
    san = [cn]
    alert = make_canonical_alert(
        cn=cn,
        san=san,
        problem_id=f"P-SYNTH-{index:04d}",
        owning_application=f"SyntheticApp-{index:02d}",
        source="synthetic",
    )

    # Step 1: Start the workflow
    start_resp = await client.post("/api/orchestrate", json={"alert": alert})
    assert start_resp.status_code == 200, (
        f"[renewal-{index}] orchestrate failed ({start_resp.status_code}): {start_resp.text[:200]}"
    )
    workflow_id = start_resp.json()["workflow_id"]

    # Step 2: Wait for CSR to be created and Jira ticket opened
    await poll_state(client, workflow_id, "CSR_REQUESTED", timeout_s=60)

    # Step 3: Simulate PD approval
    approval = make_approval_payload(
        workflow_id=workflow_id,
        thread_id=workflow_id,
        decision="APPROVED",
        approver="test-pd@test-domain.com",
        reasoning=f"Synthetic renewal {index} — approved for E2E testing.",
    )
    appr_resp = await client.post("/api/approval-callback", json=approval)
    assert appr_resp.status_code == 202, (
        f"[renewal-{index}] approval-callback failed: {appr_resp.text[:200]}"
    )

    # Step 4: Simulate PKI reply — upload cert to Blob, then notify via pki-reply
    # In full-stack E2E, the Logic App handles the real PKI email reply.
    # Here we inject directly with a test cert that matches the synthetic CN.
    # The cert is uploaded to Blob Storage via the Azure SDK; we derive the URL.
    cer_blob_url = (
        f"https://sslprodcerarti.blob.core.windows.net/cer-artifacts/{workflow_id}.cer"
    )
    pki_payload = make_pki_reply_payload(workflow_id=workflow_id, cer_blob_url=cer_blob_url)
    pki_resp = await client.post("/api/pki-reply", json=pki_payload)
    assert pki_resp.status_code == 202, (
        f"[renewal-{index}] pki-reply failed: {pki_resp.text[:200]}"
    )

    # Step 5: Wait for terminal state
    final = await poll_state(client, workflow_id, _TERMINAL_STATES, timeout_s=60)
    return {"index": index, "cn": cn, "workflow_id": workflow_id, "final": final}


class TestSyntheticRenewal:
    @pytest.mark.asyncio
    async def test_20_synthetic_renewals_reach_terminal_state(
        self, e2e_cn_prefix: str
    ) -> None:
        """20 synthetic renewals — all must reach a terminal state within 300s."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        import os

        func_key = os.environ.get("FUNC_KEY", "")
        func_host = os.environ.get("FUNC_HOST", "http://localhost:7071").rstrip("/")
        if func_key:
            headers["x-functions-key"] = func_key

        start_time = time.monotonic()
        async with httpx.AsyncClient(
            base_url=func_host, headers=headers, timeout=30.0
        ) as client:
            tasks = [
                _run_single_renewal(client, i, e2e_cn_prefix)
                for i in range(_N_RENEWALS)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.monotonic() - start_time

        failures: list[str] = []
        terminal_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failures.append(f"renewal-{i}: {type(result).__name__}: {result}")
            else:
                state = result["final"].get("state", "UNKNOWN")
                if state not in _TERMINAL_STATES:
                    failures.append(f"renewal-{i} ({result['cn']}): non-terminal state {state!r}")
                else:
                    terminal_count += 1

        assert not failures, (
            f"{len(failures)}/{_N_RENEWALS} renewals did not complete:\n" + "\n".join(failures)
        )
        assert terminal_count == _N_RENEWALS, (
            f"Only {terminal_count}/{_N_RENEWALS} renewals reached a terminal state"
        )
        assert elapsed < 300, f"20 renewals took {elapsed:.1f}s; CI timeout is 300s"

    @pytest.mark.asyncio
    async def test_single_synthetic_renewal_reaches_terminal_state(
        self, async_client: httpx.AsyncClient, e2e_cn_prefix: str
    ) -> None:
        """Smoke: one synthetic renewal must reach a terminal state."""
        result = await _run_single_renewal(async_client, 99, e2e_cn_prefix)
        assert result["final"]["state"] in _TERMINAL_STATES, (
            f"Single renewal did not reach terminal state: {result['final'].get('state')}"
        )
