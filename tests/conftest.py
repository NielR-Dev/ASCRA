"""Pytest configuration for the SSL Renewal Agent test suite.

Sets required environment variables BEFORE any src module is imported so that
pydantic-settings' Settings() does not fail at collection time.

All values are fake/test-only — no real Azure endpoints are contacted.
"""
from __future__ import annotations

import base64
import os
from typing import Any

import pytest

# These must be set before any src.* import triggers `settings = Settings()`.
_TEST_ENV = {
    "FOUNDRY_PROJECT_ENDPOINT": "https://test.api.azureml.ms",
    "KEY_VAULT_URI": "https://kv-test.vault.azure.net",
    "COSMOS_ENDPOINT": "https://cosmos-test.documents.azure.com:443/",
    "BLOB_ACCOUNT_URL": "https://blobtest.blob.core.windows.net",
    # Optional fields with non-empty test values
    "AZURE_OPENAI_DEPLOYMENT": "gpt-4o-2024-11-20",
    "COSMOS_DATABASE": "ssl_renewal_test",
    "ORCHESTRATOR_ENABLED": "true",
    "LOG_LEVEL": "DEBUG",
}

for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)


# ---------------------------------------------------------------------------
# Shared factories — import after env vars are set
# ---------------------------------------------------------------------------

from tests.factories import (  # noqa: E402 — must be after env setup
    make_batch_alerts,
    make_canonical_alert,
    make_workflow_state,
)


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_alert() -> dict[str, Any]:
    """Canonical alert dict for api.prod.test-domain.com."""
    return make_canonical_alert()


@pytest.fixture
def sample_workflow() -> dict[str, Any]:
    """workflow_state document at PARSED state."""
    return make_workflow_state("PARSED")


@pytest.fixture
def sample_workflow_approved() -> dict[str, Any]:
    """workflow_state document at APPROVED state (PD approved, PKI email pending)."""
    return make_workflow_state("APPROVED")


@pytest.fixture
def batch_alerts() -> list[dict[str, Any]]:
    """List of 5 canonical alert dicts for batch tests."""
    return make_batch_alerts(5)


@pytest.fixture
def fake_cosmos_repo() -> Any:
    """CosmosRepo backed by in-memory dicts — no real Azure connections."""
    from src.persistence.cosmos_repo import CosmosRepo

    repo = CosmosRepo()
    _stores: dict[str, dict[str, Any]] = {
        "workflow_state": {},
        "audit_log": {},
        "idempotency": {},
        "batch": {},
    }

    async def _upsert(container_name: str, doc: dict) -> None:
        _stores[container_name][doc["id"]] = doc

    async def _read(container_name: str, item_id: str) -> dict[str, Any]:
        from azure.cosmos import exceptions as cx_exc

        doc = _stores[container_name].get(item_id)
        if doc is None:
            raise cx_exc.CosmosResourceNotFoundError(message="not found", response=None, error=None)
        return doc

    async def _query(container_name: str, query: str, params: list) -> list[dict]:
        import re as _re

        items = list(_stores[container_name].values())
        alias_to_field: dict[str, str] = {}
        for m in _re.finditer(r"c\.(\w+)\s*=\s*@(\w+)|@(\w+)\s*=\s*c\.(\w+)", query):
            if m.group(1):
                alias_to_field[m.group(2)] = m.group(1)
            else:
                alias_to_field[m.group(3)] = m.group(4)
        for p in params:
            alias = p["name"].lstrip("@")
            field = alias_to_field.get(alias, alias)
            val = p["value"]
            items = [i for i in items if i.get(field) == val]
        if "ORDER BY c.seq ASC" in query:
            items.sort(key=lambda i: i.get("seq", 0))
        elif "ORDER BY c.seq DESC" in query:
            items.sort(key=lambda i: i.get("seq", 0), reverse=True)
        elif "ORDER BY c.created_at DESC" in query:
            items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
        if "TOP 1" in query:
            items = items[:1]
        if "COUNT(1)" in query:
            return [len(items)]
        return items

    class FakeContainer:
        def __init__(self, name: str) -> None:
            self._name = name

        async def upsert_item(self, body: dict, **kwargs: Any) -> None:
            await _upsert(self._name, body)

        async def create_item(self, body: dict, **kwargs: Any) -> None:
            await _upsert(self._name, body)

        async def read_item(self, item: str, partition_key: str, **kwargs: Any) -> dict[str, Any]:
            return await _read(self._name, item)

        async def query_items(self, query: str, parameters: list = None, **kwargs: Any):
            results = await _query(self._name, query, parameters or [])
            for r in results:
                yield r

    repo._containers = {name: FakeContainer(name) for name in _stores}
    return repo


@pytest.fixture
def sample_cer_der() -> bytes:
    """DER-encoded self-signed certificate for e2e-test.test-domain.com, valid 400 days."""
    import pathlib
    from cryptography.hazmat.primitives import serialization

    pem_path = pathlib.Path(__file__).parent / "fixtures" / "certs" / "test_cert_valid.pem"
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(pem_path.read_bytes())
    return cert.public_bytes(serialization.Encoding.DER)


@pytest.fixture
def sample_cer_b64(sample_cer_der: bytes) -> str:
    """Base64-encoded DER certificate for e2e-test.test-domain.com."""
    return base64.b64encode(sample_cer_der).decode()
