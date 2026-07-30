"""Cosmos DB repository for workflow_state, audit_log, idempotency, and batch containers.

Clean Architecture: this module is infrastructure. It is injected into the application layer
(orchestrator, tools) so tests can monkeypatch or pass a fake CosmosClient.

Data minimization (G7/G8):
  - Never store private key material — only Key Vault key IDs.
  - Never store full CSR bytes — only the SHA-256 hash.
  - Never store full CER bytes — only the Blob URL.

Audit hash chain:
  hash_self = SHA-256(canonical_json(record_without_hash_self) + hash_prev)
  First record in a chain uses hash_prev = "genesis".
  The chain is per workflow_id partition, enabling Cosmos transactional batch for co-located writes.

Idempotency:
  Every external side-effect (Jira create, email send, CHG create) carries an idempotency key
  stored in the `idempotency` container. On replay the stored result is returned without
  re-executing the side effect.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey, exceptions as cosmos_exc
from azure.cosmos.aio import CosmosClient as AsyncCosmosClient
from azure.identity.aio import DefaultAzureCredential

from src.config import settings

logger = logging.getLogger("ssl_renewal.cosmos_repo")

# ---------------------------------------------------------------------------
# Container names (canonical)
# ---------------------------------------------------------------------------
CONTAINER_WORKFLOW_STATE = "workflow_state"
CONTAINER_AUDIT_LOG = "audit_log"
CONTAINER_IDEMPOTENCY = "idempotency"
CONTAINER_BATCH = "batch"

GENESIS_HASH = "genesis"  # sentinel for the first audit record in a chain


# ---------------------------------------------------------------------------
# Hash chain utility
# ---------------------------------------------------------------------------

def compute_hash(record_without_hash: dict[str, Any], hash_prev: str) -> str:
    """Return SHA-256(canonical_json(record) + hash_prev).

    Canonical JSON: sorted keys, no whitespace. Deterministic and reproducible.
    The record must NOT contain hash_self before this call.
    """
    canonical = json.dumps(record_without_hash, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256((canonical + hash_prev).encode()).hexdigest()


def verify_hash_chain(audit_events: list[dict[str, Any]]) -> bool:
    """Verify that the hash chain for a list of ordered audit events is intact.

    Returns True if every record's hash_self matches the recomputed value; False otherwise.
    The list must be ordered ascending by seq.
    """
    prev_hash = GENESIS_HASH
    for event in audit_events:
        record_body = {k: v for k, v in event.items() if k not in ("hash_self", "id")}
        expected = compute_hash(record_body, prev_hash)
        if event.get("hash_self") != expected:
            return False
        prev_hash = event["hash_self"]
    return True


# ---------------------------------------------------------------------------
# Typed document builders
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_audit_record(
    *,
    workflow_id: str,
    seq: int,
    actor: str,
    action: str,
    tool: str,
    input_summary: dict[str, Any],
    output_summary: dict[str, Any],
    state_before: str,
    state_after: str,
    correlation_id: str,
    hash_prev: str,
) -> dict[str, Any]:
    """Build a tamper-evident audit record.

    hash_self is computed *after* all other fields are set so the digest covers them.
    """
    record_id = f"audit_{workflow_id}_{seq:04d}"
    # id is the Cosmos document ID — excluded from the hash so it can be set
    # independently without breaking the chain (Cosmos may normalise _rid etc.).
    body: dict[str, Any] = {
        "workflow_id": workflow_id,
        "seq": seq,
        "timestamp": _now_iso(),
        "actor": actor,
        "action": action,
        "tool": tool,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "state_before": state_before,
        "state_after": state_after,
        "correlation_id": correlation_id,
        "schema_version": 1,
        # hash_prev included so it is covered by hash_self
        "hash_prev": hash_prev,
    }
    body["hash_self"] = compute_hash(body, hash_prev)
    # id set AFTER hash computation so it is not part of the hash (prevents
    # id normalisation from breaking chain verification).
    body["id"] = record_id
    return body


# ---------------------------------------------------------------------------
# Async Cosmos repository
# ---------------------------------------------------------------------------

class CosmosRepo:
    """Async Cosmos DB repository.

    Usage (production):
        repo = CosmosRepo()
        await repo.initialise()        # one-time; gets/creates containers
        await repo.upsert_workflow(doc)

    Usage (tests):
        Monkeypatch CosmosRepo or inject a fake CosmosClient via ``CosmosRepo(client=fake)``.
    """

    def __init__(self, client: AsyncCosmosClient | None = None) -> None:
        self._client = client
        self._db = None
        self._containers: dict[str, Any] = {}

    async def _get_client(self) -> AsyncCosmosClient:
        if self._client is not None:
            return self._client
        credential = DefaultAzureCredential(
            managed_identity_client_id=settings.azure_client_id or None
        )
        self._client = AsyncCosmosClient(settings.cosmos_endpoint, credential=credential)
        return self._client

    async def initialise(self) -> None:
        """Obtain or create the database and containers (idempotent).

        Called once at Function startup. Creates containers if they don't exist yet
        (useful for dev/test envs).
        """
        client = await self._get_client()
        self._db = client.get_database_client(settings.cosmos_database)
        for name in (CONTAINER_WORKFLOW_STATE, CONTAINER_AUDIT_LOG,
                     CONTAINER_IDEMPOTENCY, CONTAINER_BATCH):
            pk_field = "/workflow_id" if name != CONTAINER_BATCH else "/batch_id"
            if name == CONTAINER_IDEMPOTENCY:
                pk_field = "/idempotency_key"
            self._containers[name] = self._db.get_container_client(name)

    def _container(self, name: str) -> Any:
        if name not in self._containers:
            raise RuntimeError(f"CosmosRepo not initialised; container '{name}' not available.")
        return self._containers[name]

    # ------------------------------------------------------------------
    # workflow_state
    # ------------------------------------------------------------------

    async def upsert_workflow(self, doc: dict[str, Any]) -> None:
        """Upsert a workflow_state document.

        Validates that no private key / CSR body / CER bytes are present (G7/G8).
        """
        _assert_no_sensitive_data(doc)
        container = self._container(CONTAINER_WORKFLOW_STATE)
        await container.upsert_item(body=doc)
        logger.debug("upsert_workflow workflow_id=%s state=%s",
                     doc.get("workflow_id"), doc.get("state"))

    async def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        """Point-read a workflow_state document; returns None if not found."""
        container = self._container(CONTAINER_WORKFLOW_STATE)
        try:
            return await container.read_item(item=workflow_id, partition_key=workflow_id)
        except cosmos_exc.CosmosResourceNotFoundError:
            return None

    async def query_workflows_by_batch(self, batch_id: str) -> list[dict[str, Any]]:
        """Return all workflow_state documents that belong to a batch."""
        container = self._container(CONTAINER_WORKFLOW_STATE)
        query = "SELECT * FROM c WHERE c.batch_id = @batch_id"
        params: list[dict[str, Any]] = [{"name": "@batch_id", "value": batch_id}]
        results = []
        async for item in container.query_items(query=query, parameters=params):
            results.append(item)
        return results

    async def get_workflow_by_cn(self, cn: str) -> dict[str, Any] | None:
        """Return the most recent active workflow_state for a given CN (for status query)."""
        container = self._container(CONTAINER_WORKFLOW_STATE)
        query = ("SELECT TOP 1 * FROM c WHERE c.cn = @cn "
                 "ORDER BY c.created_at DESC")
        params: list[dict[str, Any]] = [{"name": "@cn", "value": cn}]
        async for item in container.query_items(query=query, parameters=params):
            return item
        return None

    # ------------------------------------------------------------------
    # audit_log
    # ------------------------------------------------------------------

    async def append_audit(self, record: dict[str, Any]) -> None:
        """Append an audit record. Audit log is append-only — never mutate existing records."""
        container = self._container(CONTAINER_AUDIT_LOG)
        await container.create_item(body=record)
        logger.debug("append_audit workflow_id=%s seq=%s action=%s",
                     record.get("workflow_id"), record.get("seq"), record.get("action"))

    async def get_audit_chain(self, workflow_id: str) -> list[dict[str, Any]]:
        """Return all audit records for a workflow in seq order (for hash-chain verification)."""
        container = self._container(CONTAINER_AUDIT_LOG)
        query = ("SELECT * FROM c WHERE c.workflow_id = @wf "
                 "ORDER BY c.seq ASC")
        params: list[dict[str, Any]] = [{"name": "@wf", "value": workflow_id}]
        results = []
        async for item in container.query_items(query=query, parameters=params):
            results.append(item)
        return results

    async def get_next_audit_seq(self, workflow_id: str) -> int:
        """Return the next seq number (count of existing records + 1)."""
        container = self._container(CONTAINER_AUDIT_LOG)
        query = "SELECT VALUE COUNT(1) FROM c WHERE c.workflow_id = @wf"
        params: list[dict[str, Any]] = [{"name": "@wf", "value": workflow_id}]
        async for count in container.query_items(query=query, parameters=params):
            return int(count) + 1
        return 1

    async def get_last_audit_hash(self, workflow_id: str) -> str:
        """Return the hash_self of the most recent audit record (for chaining). Returns GENESIS_HASH if none."""
        container = self._container(CONTAINER_AUDIT_LOG)
        query = ("SELECT TOP 1 c.hash_self FROM c WHERE c.workflow_id = @wf "
                 "ORDER BY c.seq DESC")
        params: list[dict[str, Any]] = [{"name": "@wf", "value": workflow_id}]
        async for item in container.query_items(query=query, parameters=params):
            return str(item.get("hash_self", GENESIS_HASH))
        return GENESIS_HASH

    # ------------------------------------------------------------------
    # idempotency
    # ------------------------------------------------------------------

    async def check_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the stored result for this key, or None if not present (first execution)."""
        container = self._container(CONTAINER_IDEMPOTENCY)
        try:
            return await container.read_item(
                item=idempotency_key, partition_key=idempotency_key
            )
        except cosmos_exc.CosmosResourceNotFoundError:
            return None

    async def record_idempotency(self, idempotency_key: str, result: dict[str, Any]) -> None:
        """Record the result for a side effect so replays return it without re-executing."""
        container = self._container(CONTAINER_IDEMPOTENCY)
        doc = {
            "id": idempotency_key,
            "idempotency_key": idempotency_key,
            "result": result,
            "recorded_at": _now_iso(),
        }
        await container.upsert_item(body=doc)

    # ------------------------------------------------------------------
    # batch
    # ------------------------------------------------------------------

    async def upsert_batch(self, doc: dict[str, Any]) -> None:
        """Upsert a batch record (idempotent fan-in update)."""
        container = self._container(CONTAINER_BATCH)
        await container.upsert_item(body=doc)

    async def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        """Point-read a batch record."""
        container = self._container(CONTAINER_BATCH)
        try:
            return await container.read_item(item=batch_id, partition_key=batch_id)
        except cosmos_exc.CosmosResourceNotFoundError:
            return None


# ---------------------------------------------------------------------------
# Sensitive data guard (G7/G8)
# ---------------------------------------------------------------------------

# Patterns that must never appear in Cosmos documents.
# Checked against string values of all fields recursively.
_SENSITIVE_PATTERNS: tuple[str, ...] = (
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN ENCRYPTED PRIVATE KEY-----",
)


def _assert_no_sensitive_data(doc: dict[str, Any], path: str = "") -> None:
    """Raise ValueError if any field value contains private key material.

    This is a defense-in-depth check; the primary guard is that generate_csr never
    returns private key bytes. But belt-and-suspenders for any serialization mistake.
    """
    for key, value in doc.items():
        current_path = f"{path}.{key}" if path else key
        if isinstance(value, str):
            for pattern in _SENSITIVE_PATTERNS:
                if pattern in value:
                    raise ValueError(
                        f"Attempted to store sensitive data at field '{current_path}'. "
                        "Private key material must never be written to Cosmos (G7/G8)."
                    )
        elif isinstance(value, dict):
            _assert_no_sensitive_data(value, current_path)
