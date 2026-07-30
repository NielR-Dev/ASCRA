"""Simulate a PKI team reply by uploading a test CER to Blob and notifying pki-reply.

Usage:
    python -m scripts.inject_pki_reply --workflow-id wf_... --cert tests/fixtures/certs/test_cert_valid.pem
    python -m scripts.inject_pki_reply --workflow-id wf_... --cert /path/to/real.cer --env uat

The script uploads the certificate to Azure Blob Storage using DefaultAzureCredential,
then calls POST /api/pki-reply with the resulting blob URL.

Environment variables:
    FUNC_HOST         Base URL (overrides --env)
    FUNC_KEY          Function App host key
    BLOB_ACCOUNT_URL  Azure Blob Storage account URL (required for real Blob upload)
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

from scripts._client import abort, make_client


def _upload_cert_to_blob(workflow_id: str, cert_bytes: bytes) -> str:
    """Upload cert bytes to Azure Blob Storage and return the blob URL."""
    blob_account_url = os.environ.get("BLOB_ACCOUNT_URL", "")
    if not blob_account_url:
        abort("BLOB_ACCOUNT_URL env var is required for Blob upload. Set it and retry.")

    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url=blob_account_url, credential=credential)
    container_client = service_client.get_container_client("cer-artifacts")
    blob_name = f"{workflow_id}.cer"
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(cert_bytes, overwrite=True)
    return blob_client.url


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject a PKI reply (CER file) into a workflow.")
    parser.add_argument("--workflow-id", required=True, dest="workflow_id")
    parser.add_argument("--cert", required=True, help="Path to PEM or DER certificate file")
    parser.add_argument(
        "--blob-url",
        dest="blob_url",
        help="Pre-existing Blob URL (skip upload step)",
    )
    parser.add_argument("--env", choices=["local", "dev", "uat", "prod"], default=None)
    args = parser.parse_args()

    cert_path = pathlib.Path(args.cert)
    if not cert_path.exists():
        abort(f"Certificate file not found: {cert_path}")

    cert_bytes = cert_path.read_bytes()

    if args.blob_url:
        cer_blob_url = args.blob_url
        print(f"Using provided blob URL: {cer_blob_url}")
    else:
        print(f"Uploading {cert_path.name} to Blob Storage...")
        cer_blob_url = _upload_cert_to_blob(args.workflow_id, cert_bytes)
        print(f"Uploaded  blob_url={cer_blob_url}")

    payload = {
        "workflow_id": args.workflow_id,
        "cer_blob_url": cer_blob_url,
    }
    with make_client(args.env) as client:
        resp = client.post("/api/pki-reply", json=payload)

    if resp.status_code == 202:
        print(f"Accepted  workflow_id={args.workflow_id}")
    else:
        abort(f"HTTP {resp.status_code}: {resp.text}")


if __name__ == "__main__":
    main()
    sys.exit(0)
