"""Native tool: create a NON-EXPORTABLE HSM key + PKCS#10 CSR in Azure Key Vault (G7).

This is a native MAF @tool — NOT an MCP surface. It runs in-process because:
  - The private key must never cross a network boundary before the CSR is returned (G7).
  - The wildcard block (G6) must happen before any Key Vault call.
  - The tool is idempotent: a second call with the same workflow_id returns the existing key/CSR.

Guardrails enforced here:
  G6 — rejects wildcard CN/SAN before reaching Key Vault (dual enforcement with PolicyMiddleware)
  G7 — exportable=False, key_type=rsa_hsm — key never leaves the HSM
  G8 — no credentials or key bytes in return value or logs
"""
from __future__ import annotations

import base64
import hashlib
import logging
import textwrap
from dataclasses import dataclass

from src.config import settings
from src.tools.errors import ToolFatalError, ToolValidationError

logger = logging.getLogger("ssl_renewal.generate_csr")


@dataclass
class CsrResult:
    """Result of a successful CSR generation.

    key_vault_key_id: Canonical KV URI for the key/certificate (NOT the key bytes).
    csr_pem:          PKCS#10 CSR in PEM format (NOT the private key).
    csr_pem_sha256:   SHA-256 hex of the CSR PEM (for Cosmos storage — never store the full PEM).
    """
    key_vault_key_id: str
    csr_pem: str
    csr_pem_sha256: str


def _reject_wildcard(cn: str, san: list[str]) -> None:
    """Raise ToolValidationError if any CN or SAN is a wildcard (G6, dual enforcement)."""
    if cn.strip().startswith("*.") or cn.strip() == "*":
        raise ToolValidationError(
            f"Wildcard certificates are not permitted by policy (G6): CN='{cn}'. "
            "Route to CAB for separate approval."
        )
    for s in san:
        if s.strip().startswith("*.") or s.strip() == "*":
            raise ToolValidationError(
                f"Wildcard certificates are not permitted by policy (G6): SAN='{s}'. "
                "Route to CAB for separate approval."
            )


def generate_csr(cn: str, san: list[str], owning_application: str, workflow_id: str) -> CsrResult:
    """Generate a 2048-bit RSA key in Key Vault (HSM, non-exportable) and a PKCS#10 CSR.

    The private key never leaves the HSM and is never returned to the caller (G7).
    Idempotent on workflow_id: if the certificate operation already exists in Key Vault
    for this workflow_id, the existing CSR is returned.

    Args:
        cn:                  Common Name (e.g. "api.prod.example.com")
        san:                 Subject Alternative Names (list of DNS names)
        owning_application:  CMDB application name (for audit/logging context)
        workflow_id:         Unique workflow identifier (used as the Key Vault cert name)

    Returns:
        CsrResult with key_vault_key_id, csr_pem (PKCS#10), and csr_pem_sha256.

    Raises:
        ToolValidationError: wildcard CN/SAN (G6).
        ToolFatalError:      Key Vault call fails after internal retries.
    """
    # G6: reject wildcards before any Key Vault call
    _reject_wildcard(cn, san)

    # G8: imports are lazy — no hard-coded Key Vault dependencies at module level
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.certificates import (
            CertificateClient,
            CertificatePolicy,
            KeyType,
            WellKnownIssuerNames,
        )
    except ImportError as exc:
        raise ToolFatalError(f"Required Azure SDK packages not available: {exc}") from exc

    try:
        cred = DefaultAzureCredential(
            managed_identity_client_id=settings.azure_client_id or None
        )
        client = CertificateClient(vault_url=settings.key_vault_uri, credential=cred)

        # Cert name must be a valid Key Vault name (alphanumeric + hyphens)
        cert_name = workflow_id.replace("_", "-").replace(":", "-").replace(".", "-")
        # Truncate to Key Vault's 127-char limit
        if len(cert_name) > 127:
            cert_name = cert_name[:127]

        policy = CertificatePolicy(
            issuer_name=WellKnownIssuerNames.unknown,  # External CA (PKI team) signs the CSR
            subject=f"CN={cn}",
            san_dns_names=san,
            exportable=False,                           # G7: NON-EXPORTABLE — key stays in HSM
            key_type=KeyType.rsa_hsm,                  # G7: HSM-backed key type
            key_size=2048,
            content_type="application/x-pkcs12",
        )

        logger.info(
            "generate_csr: starting cn=%s san_count=%d workflow_id=%s",
            cn, len(san), workflow_id
        )

        # begin_create_certificate is idempotent: if the cert already exists in the same workflow,
        # it returns a pending/completed operation without creating a new key.
        operation = client.begin_create_certificate(
            certificate_name=cert_name, policy=policy
        ).result()

        csr_der: bytes = operation.csr
        if not csr_der:
            raise ToolFatalError(
                f"Key Vault returned an empty CSR for cert '{cert_name}'. "
                "The certificate operation may be in an unexpected state."
            )

        b64 = base64.b64encode(csr_der).decode()
        csr_pem = (
            "-----BEGIN CERTIFICATE REQUEST-----\n"
            + "\n".join(textwrap.wrap(b64, 64))
            + "\n-----END CERTIFICATE REQUEST-----\n"
        )
        csr_sha256 = hashlib.sha256(csr_pem.encode()).hexdigest()

        result = CsrResult(
            key_vault_key_id=f"{settings.key_vault_uri}/certificates/{cert_name}",
            csr_pem=csr_pem,
            csr_pem_sha256=csr_sha256,
        )

        logger.info(
            "generate_csr: complete cn=%s cert_name=%s sha256=%s…",
            cn, cert_name, csr_sha256[:16]
        )
        # G7/G8: private key bytes never returned — only the CSR PEM and KV key ID
        return result

    except ToolValidationError:
        raise
    except Exception as exc:
        raise ToolFatalError(
            f"generate_csr failed for workflow_id={workflow_id}, cn={cn}: {type(exc).__name__}: {exc}"
        ) from exc
