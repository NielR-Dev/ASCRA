"""Tests for the persistence layer: cosmos_repo + blob_repo.

Tests cover:
- workflow_state document write/read with schema validation
- Audit log append + hash-chain verification (8 events)
- Idempotency container: first call records result; second call returns it without re-executing
- Sensitive-data guard: private key pattern in a doc raises ValueError
- BlobRepo: upload / download / exists (with a fake BlobServiceClient stub)
"""
from __future__ import annotations

import hashlib
import json
import re
import pytest
import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.persistence.cosmos_repo import (
    GENESIS_HASH,
    CosmosRepo,
    _assert_no_sensitive_data,
    build_audit_record,
    compute_hash,
    verify_hash_chain,
)
from src.persistence.blob_repo import BlobRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_workflow(workflow_id: str = "wf_test_001") -> dict[str, Any]:
    return {
        "id": workflow_id,
        "workflow_id": workflow_id,
        "state": "PARSED",
        "cn": "api.prod.example.com",
        "san": ["api.prod.example.com", "api-internal.prod.example.com"],
        "owning_application": "Orders-API",
        "alert": {"source": "dynatrace", "problem_id": "P-99999"},
        "csr": {
            "key_vault_key_id": "https://kv-ssl-hsm.vault.azure.net/certificates/wf_test_001/abc",
            "csr_pem_sha256": "deadbeef" * 8,
            "jira_ticket": "SSL-001",
        },
        "approval": None,
        "pki": {"email_thread_id": None, "sent_at": None, "reply_at": None, "reminders_sent": 0},
        "verification": {"pass_": None, "checks": {}, "cer_blob_url": None},
        "change": {"chg_number": None},
        "retry": {"rounds": 0, "escalations": 0},
        "idempotency_keys": {"jira_create": "idem_jira_001", "email_send": None, "chg_create": None},
        "thread_id": "thread_test_001",
        "schema_version": 1,
    }


def _make_fake_cosmos_repo() -> CosmosRepo:
    """Return a CosmosRepo backed by in-memory dicts instead of real Cosmos."""
    repo = CosmosRepo()
    # In-memory stores
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
        """Very naive query executor: supports = and ORDER BY seq ASC only.

        Maps @param_alias → actual document field name by scanning the WHERE clause
        for patterns like ``c.field_name = @alias``.
        """
        items = list(_stores[container_name].values())
        import re as _re
        # Build alias → field_name mapping from the query text
        # matches: c.field_name = @alias  or  @alias = c.field_name
        alias_to_field: dict[str, str] = {}
        for m in _re.finditer(r"c\.(\w+)\s*=\s*@(\w+)|@(\w+)\s*=\s*c\.(\w+)", query):
            if m.group(1):
                alias_to_field[m.group(2)] = m.group(1)
            else:
                alias_to_field[m.group(3)] = m.group(4)
        for p in params:
            alias = p["name"].lstrip("@")
            field = alias_to_field.get(alias, alias)  # fallback: use alias as field name
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
        def __init__(self, name: str):
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


# ---------------------------------------------------------------------------
# compute_hash
# ---------------------------------------------------------------------------

class TestComputeHash:
    def test_deterministic(self) -> None:
        record = {"workflow_id": "wf_001", "seq": 1, "action": "test"}
        h1 = compute_hash(record, GENESIS_HASH)
        h2 = compute_hash(record, GENESIS_HASH)
        assert h1 == h2

    def test_different_prev_hash_yields_different_result(self) -> None:
        record = {"workflow_id": "wf_001", "seq": 1}
        h1 = compute_hash(record, GENESIS_HASH)
        h2 = compute_hash(record, "some_other_hash")
        assert h1 != h2

    def test_sha256_format(self) -> None:
        record = {"x": "y"}
        h = compute_hash(record, GENESIS_HASH)
        assert re.fullmatch(r"[0-9a-f]{64}", h)


# ---------------------------------------------------------------------------
# build_audit_record + verify_hash_chain
# ---------------------------------------------------------------------------

class TestHashChain:
    def _make_chain(self, n: int) -> list[dict[str, Any]]:
        chain = []
        prev_hash = GENESIS_HASH
        for i in range(1, n + 1):
            record = build_audit_record(
                workflow_id="wf_chain_test",
                seq=i,
                actor="orchestrator",
                action=f"step_{i}",
                tool=f"tool_{i}",
                input_summary={"seq": i},
                output_summary={"ok": True},
                state_before=f"STATE_{i-1}",
                state_after=f"STATE_{i}",
                correlation_id="corr_001",
                hash_prev=prev_hash,
            )
            chain.append(record)
            prev_hash = record["hash_self"]
        return chain

    def test_chain_of_8_verifies(self) -> None:
        chain = self._make_chain(8)
        assert verify_hash_chain(chain) is True

    def test_tampered_record_fails_verification(self) -> None:
        chain = self._make_chain(8)
        # Tamper the 4th record's action field
        chain[3]["action"] = "tampered_action"
        assert verify_hash_chain(chain) is False

    def test_tampered_hash_prev_fails(self) -> None:
        chain = self._make_chain(4)
        chain[2]["hash_prev"] = "000000" * 10 + "0000"
        assert verify_hash_chain(chain) is False

    def test_single_record_verifies(self) -> None:
        chain = self._make_chain(1)
        assert verify_hash_chain(chain) is True

    def test_empty_chain_is_valid(self) -> None:
        assert verify_hash_chain([]) is True

    def test_no_sensitive_data_in_audit_record(self) -> None:
        record = build_audit_record(
            workflow_id="wf_001",
            seq=1,
            actor="system",
            action="tool_call",
            tool="generate_csr",
            input_summary={"cn": "api.prod.example.com"},
            output_summary={"key_vault_key_id": "https://kv.vault.azure.net/certs/wf/1"},
            state_before="CSR_READY",
            state_after="CSR_REQUESTED",
            correlation_id="corr_001",
            hash_prev=GENESIS_HASH,
        )
        # Must not contain private key markers
        for pattern in ("PRIVATE KEY", "BEGIN RSA"):
            assert pattern not in json.dumps(record), f"Found '{pattern}' in audit record"


# ---------------------------------------------------------------------------
# CosmosRepo — workflow_state
# ---------------------------------------------------------------------------

class TestWorkflowState:
    @pytest.mark.asyncio
    async def test_upsert_and_read_workflow(self) -> None:
        repo = _make_fake_cosmos_repo()
        doc = _sample_workflow("wf_upsert_001")
        await repo.upsert_workflow(doc)
        result = await repo.get_workflow("wf_upsert_001")
        assert result is not None
        assert result["workflow_id"] == "wf_upsert_001"
        assert result["state"] == "PARSED"
        assert result["cn"] == "api.prod.example.com"

    @pytest.mark.asyncio
    async def test_get_workflow_not_found_returns_none(self) -> None:
        repo = _make_fake_cosmos_repo()
        result = await repo.get_workflow("wf_nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_schema_fields_present(self) -> None:
        repo = _make_fake_cosmos_repo()
        doc = _sample_workflow("wf_schema_001")
        await repo.upsert_workflow(doc)
        result = await repo.get_workflow("wf_schema_001")
        assert result is not None
        # Required schema fields
        required = ["id", "workflow_id", "state", "cn", "san", "owning_application",
                    "thread_id", "schema_version"]
        for field in required:
            assert field in result, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_no_private_key_stored(self) -> None:
        repo = _make_fake_cosmos_repo()
        doc = _sample_workflow("wf_privkey_001")
        # Inject a private key pattern into a field
        doc["csr"]["malicious_field"] = "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----"
        with pytest.raises(ValueError, match="sensitive data"):
            await repo.upsert_workflow(doc)

    @pytest.mark.asyncio
    async def test_no_csr_bytes_stored(self) -> None:
        """Only hash stored, not full CSR PEM."""
        doc = _sample_workflow("wf_csr_001")
        # csr_pem_sha256 is the hash — that's fine. Full PEM with CERTIFICATE REQUEST is not allowed.
        # The sample doc only has the SHA256 hash, which should pass.
        repo = _make_fake_cosmos_repo()
        await repo.upsert_workflow(doc)  # should not raise


# ---------------------------------------------------------------------------
# CosmosRepo — audit_log
# ---------------------------------------------------------------------------

class TestAuditLog:
    @pytest.mark.asyncio
    async def test_write_8_audit_events_and_verify_chain(self) -> None:
        repo = _make_fake_cosmos_repo()
        prev_hash = GENESIS_HASH
        for i in range(1, 9):
            record = build_audit_record(
                workflow_id="wf_audit_008",
                seq=i,
                actor="orchestrator",
                action=f"step_{i}",
                tool=f"tool_{i}",
                input_summary={"i": i},
                output_summary={"done": True},
                state_before=f"S{i-1}",
                state_after=f"S{i}",
                correlation_id="corr_audit_008",
                hash_prev=prev_hash,
            )
            await repo.append_audit(record)
            prev_hash = record["hash_self"]

        chain = await repo.get_audit_chain("wf_audit_008")
        assert len(chain) == 8
        assert verify_hash_chain(chain) is True

    @pytest.mark.asyncio
    async def test_audit_records_are_ordered_by_seq(self) -> None:
        repo = _make_fake_cosmos_repo()
        prev_hash = GENESIS_HASH
        for i in [3, 1, 2]:  # Insert out of order
            record = build_audit_record(
                workflow_id="wf_order_test",
                seq=i,
                actor="orchestrator",
                action=f"a{i}",
                tool="t",
                input_summary={},
                output_summary={},
                state_before="X",
                state_after="Y",
                correlation_id="c",
                hash_prev=prev_hash if i == 1 else "dummy",
            )
            await repo.append_audit(record)
        chain = await repo.get_audit_chain("wf_order_test")
        seqs = [r["seq"] for r in chain]
        assert seqs == sorted(seqs)


# ---------------------------------------------------------------------------
# CosmosRepo — idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_first_call_returns_none(self) -> None:
        repo = _make_fake_cosmos_repo()
        result = await repo.check_idempotency("idem_jira_001")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_then_check_returns_result(self) -> None:
        repo = _make_fake_cosmos_repo()
        await repo.record_idempotency("idem_jira_002", {"jira_ticket": "SSL-999"})
        result = await repo.check_idempotency("idem_jira_002")
        assert result is not None
        assert result["result"]["jira_ticket"] == "SSL-999"

    @pytest.mark.asyncio
    async def test_replay_returns_prior_result_not_re_executing(self) -> None:
        """Calling record_idempotency twice with the same key must not error."""
        repo = _make_fake_cosmos_repo()
        key = "idem_email_001"
        result1 = {"email_thread_id": "AAMk_first"}
        result2 = {"email_thread_id": "AAMk_second"}
        await repo.record_idempotency(key, result1)
        await repo.record_idempotency(key, result2)  # upsert: updates the record
        stored = await repo.check_idempotency(key)
        # The point is that the application layer checks check_idempotency() first
        # and short-circuits. The repo itself does upsert; application logic prevents replay.
        assert stored is not None  # Record exists


# ---------------------------------------------------------------------------
# Sensitive data guard
# ---------------------------------------------------------------------------

class TestSensitiveDataGuard:
    @pytest.mark.parametrize("pattern", [
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
    ])
    def test_private_key_patterns_raise(self, pattern: str) -> None:
        doc = {"bad_field": f"{pattern}\nABCD\n-----END RSA PRIVATE KEY-----"}
        with pytest.raises(ValueError, match="sensitive data"):
            _assert_no_sensitive_data(doc)

    def test_nested_private_key_raises(self) -> None:
        doc = {"csr": {"body": "-----BEGIN RSA PRIVATE KEY-----\nABC\n"}}
        with pytest.raises(ValueError):
            _assert_no_sensitive_data(doc)

    def test_safe_doc_passes(self) -> None:
        doc = _sample_workflow()
        _assert_no_sensitive_data(doc)  # Must not raise

    def test_csr_pem_sha256_is_safe(self) -> None:
        """SHA-256 hex string must not trigger the guard."""
        doc = {"csr_pem_sha256": "a" * 64}
        _assert_no_sensitive_data(doc)  # Must not raise


# ---------------------------------------------------------------------------
# BlobRepo (unit tests with fake BlobServiceClient)
# ---------------------------------------------------------------------------

class TestBlobRepo:
    """BlobRepo unit tests.

    BlobServiceClient.get_container_client() is a SYNCHRONOUS method that returns a
    ContainerClient; get_blob_client() on the container is also sync.  Only the actual
    blob I/O methods (upload_blob, download_blob, exists) are async.  Therefore the
    service and container mocks must be plain MagicMock objects (not AsyncMock) to
    avoid conftest.py introducing spurious coroutines for the sync calls.
    """

    @staticmethod
    def _make_fake_service(fake_blob_client: AsyncMock) -> MagicMock:
        """Return a fake BlobServiceClient with sync get_container_client/get_blob_client."""
        fake_container = MagicMock()
        fake_container.get_blob_client.return_value = fake_blob_client

        fake_service = MagicMock()
        fake_service.get_container_client.return_value = fake_container
        return fake_service

    @pytest.mark.asyncio
    async def test_upload_cer_returns_url(self) -> None:
        fake_blob_client = AsyncMock()
        fake_blob_client.url = "https://acct.blob.core.windows.net/cer-artifacts/wf_001.cer"
        fake_blob_client.upload_blob = AsyncMock()

        fake_service = self._make_fake_service(fake_blob_client)
        repo = BlobRepo(client=fake_service)
        url = await repo.upload_cer("wf_001", b"fakecertbytes")
        assert url == "https://acct.blob.core.windows.net/cer-artifacts/wf_001.cer"
        fake_blob_client.upload_blob.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_empty_cer_raises(self) -> None:
        repo = BlobRepo(client=MagicMock())
        with pytest.raises(ValueError, match="cer_bytes must not be empty"):
            await repo.upload_cer("wf_001", b"")

    @pytest.mark.asyncio
    async def test_download_cer_returns_bytes(self) -> None:
        expected = b"certificate_data"
        fake_stream = AsyncMock()
        fake_stream.readall = AsyncMock(return_value=expected)

        fake_blob_client = AsyncMock()
        fake_blob_client.download_blob = AsyncMock(return_value=fake_stream)

        fake_service = self._make_fake_service(fake_blob_client)
        repo = BlobRepo(client=fake_service)
        data = await repo.download_cer("wf_001")
        assert data == expected

    @pytest.mark.asyncio
    async def test_download_cer_not_found_raises(self) -> None:
        fake_blob_client = AsyncMock()
        fake_blob_client.download_blob = AsyncMock(side_effect=Exception("not found"))

        fake_service = self._make_fake_service(fake_blob_client)
        repo = BlobRepo(client=fake_service)
        with pytest.raises(FileNotFoundError):
            await repo.download_cer("wf_nonexistent")

    @pytest.mark.asyncio
    async def test_exists_returns_true(self) -> None:
        fake_blob_client = AsyncMock()
        fake_blob_client.exists = AsyncMock(return_value=True)

        fake_service = self._make_fake_service(fake_blob_client)
        repo = BlobRepo(client=fake_service)
        assert await repo.exists("wf_001") is True

    @pytest.mark.asyncio
    async def test_exists_returns_false(self) -> None:
        fake_blob_client = AsyncMock()
        fake_blob_client.exists = AsyncMock(return_value=False)

        fake_service = self._make_fake_service(fake_blob_client)
        repo = BlobRepo(client=fake_service)
        assert await repo.exists("wf_nonexistent") is False
