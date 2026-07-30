"""E2E test configuration — connects to a running ASCRA Function App.

Environment variables:
    FUNC_HOST   Base URL of the Function App (default: http://localhost:7071)
    FUNC_KEY    Azure Function host key (omit for local dev with no auth)

Usage:
    # Against local Functions runtime
    FUNC_HOST=http://localhost:7071 pytest tests/test_e2e/ -m e2e -v

    # Against deployed prod
    FUNC_HOST=https://ssl-renewal-func-prod.azurewebsites.net \\
    FUNC_KEY=$FUNC_KEY_PROD pytest tests/test_e2e/ -m e2e -v --timeout=300
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import httpx
import pytest


_FUNC_HOST = os.environ.get("FUNC_HOST", "http://localhost:7071").rstrip("/")
_FUNC_KEY = os.environ.get("FUNC_KEY", "")

_POLL_INTERVAL_S = 2
_POLL_TIMEOUT_S = 60


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if _FUNC_KEY:
        h["x-functions-key"] = _FUNC_KEY
    return h


@pytest.fixture(scope="session")
def e2e_cn_prefix() -> str:
    """Short unique prefix for all CNs created in this test session."""
    return f"e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def async_client() -> httpx.AsyncClient:  # type: ignore[misc]
    """Async HTTP client pointing at the Function App."""
    async with httpx.AsyncClient(
        base_url=_FUNC_HOST,
        headers=_headers(),
        timeout=30.0,
    ) as client:
        yield client


async def poll_state(
    client: httpx.AsyncClient,
    workflow_id: str,
    target_state: str | set[str],
    timeout_s: int = _POLL_TIMEOUT_S,
) -> dict[str, Any]:
    """Poll GET /api/status until state reaches target_state (or times out).

    Args:
        client: Async HTTP client.
        workflow_id: Workflow to poll.
        target_state: A single state string or a set of acceptable terminal states.
        timeout_s: Max seconds to wait before raising TimeoutError.

    Returns:
        The final status response dict.

    Raises:
        TimeoutError: If the target state is not reached within timeout_s.
    """
    if isinstance(target_state, str):
        target_state = {target_state}

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get("/api/status", params={"workflow_id": workflow_id})
        if resp.status_code == 200:
            body = resp.json()
            if body.get("state") in target_state:
                return body
        await asyncio.sleep(_POLL_INTERVAL_S)

    raise TimeoutError(
        f"workflow_id={workflow_id!r} did not reach {target_state} within {timeout_s}s. "
        f"Last response: {resp.text[:200]}"
    )
