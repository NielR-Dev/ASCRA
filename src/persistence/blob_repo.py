"""Blob Storage repository for immutable CER file storage (WORM / legal hold).

Clean Architecture: this module is infrastructure. Inject BlobRepo into the application layer;
tests monkeypatch or pass a fake BlobServiceClient.

WORM / compliance:
  - CER files are written to the ``cer-artifacts`` container with immutability (WORM).
  - 7-year legal hold is configured at the container level via Bicep (infra/storage.bicep).
  - Blob versioning is enabled so CER overwrites (if any) are auditable.
  - Download URLs are SHORT-TTL SAS tokens (1 hour) — never long-lived presigned URLs.

Data minimization (G7/G8):
  - Only the Blob URL (not the CER bytes) is stored in Cosmos.
  - CER bytes are written once, referenced by URL.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import BlobSasPermissions, generate_blob_sas

from src.config import settings

logger = logging.getLogger("ssl_renewal.blob_repo")

CONTAINER_NAME = "cer-artifacts"
SAS_TTL_HOURS = 1


class BlobRepo:
    """Async Blob repository for CER WORM storage.

    Usage (production):
        repo = BlobRepo()
        url = await repo.upload_cer(workflow_id, cer_bytes)

    Usage (tests):
        Inject a fake BlobServiceClient: BlobRepo(client=fake_client)
    """

    def __init__(self, client: BlobServiceClient | None = None) -> None:
        self._client = client

    async def _get_client(self) -> BlobServiceClient:
        if self._client is not None:
            return self._client
        credential = DefaultAzureCredential(
            managed_identity_client_id=settings.azure_client_id or None
        )
        self._client = BlobServiceClient(
            account_url=settings.blob_account_url, credential=credential
        )
        return self._client

    async def upload_cer(self, workflow_id: str, cer_bytes: bytes) -> str:
        """Upload a CER file to the WORM container.

        The blob name is ``{workflow_id}.cer``. Returns the canonical Blob URL (no SAS).
        The canonical URL is what is stored in Cosmos (verification.cer_blob_url).

        Raises:
            ValueError: if cer_bytes is empty.
        """
        if not cer_bytes:
            raise ValueError("cer_bytes must not be empty.")

        blob_name = f"{workflow_id}.cer"
        client = await self._get_client()
        container_client = client.get_container_client(CONTAINER_NAME)
        blob_client = container_client.get_blob_client(blob_name)

        await blob_client.upload_blob(
            data=cer_bytes,
            overwrite=False,  # WORM: do not overwrite an existing CER for the same workflow
            content_settings=None,
            metadata={"workflow_id": workflow_id},
        )

        blob_url = blob_client.url
        logger.info(
            "cer_uploaded workflow_id=%s blob=%s size_bytes=%d",
            workflow_id, blob_name, len(cer_bytes)
        )
        return blob_url

    async def download_cer(self, workflow_id: str) -> bytes:
        """Download a CER file by workflow_id.

        Returns the raw bytes. Caller is responsible for validation (see verify_cer.py).

        Raises:
            FileNotFoundError: if the blob does not exist.
        """
        blob_name = f"{workflow_id}.cer"
        client = await self._get_client()
        container_client = client.get_container_client(CONTAINER_NAME)
        blob_client = container_client.get_blob_client(blob_name)

        try:
            stream = await blob_client.download_blob()
            data: bytes = await stream.readall()
        except Exception as exc:
            raise FileNotFoundError(
                f"CER blob not found for workflow_id={workflow_id}: {exc}"
            ) from exc

        logger.debug("cer_downloaded workflow_id=%s size_bytes=%d", workflow_id, len(data))
        return data

    def generate_sas_url(self, workflow_id: str, account_name: str, account_key: str) -> str:
        """Generate a short-TTL (1 hour) SAS URL for the CER blob.

        Used only for the completion card and status API — never stored in Cosmos.

        Note: account_key should be retrieved from Key Vault at runtime (G8 — no secrets in code).
        """
        blob_name = f"{workflow_id}.cer"
        expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=SAS_TTL_HOURS
        )
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=CONTAINER_NAME,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
        blob_url = (
            f"https://{account_name}.blob.core.windows.net"
            f"/{CONTAINER_NAME}/{blob_name}?{sas_token}"
        )
        logger.debug("sas_url_generated workflow_id=%s expiry=%s", workflow_id, expiry.isoformat())
        return blob_url

    async def exists(self, workflow_id: str) -> bool:
        """Return True if a CER blob exists for this workflow_id."""
        blob_name = f"{workflow_id}.cer"
        client = await self._get_client()
        container_client = client.get_container_client(CONTAINER_NAME)
        blob_client = container_client.get_blob_client(blob_name)
        return await blob_client.exists()
