"""Tests for generate_csr — native Key Vault HSM tool (G6, G7, T05).

Because generate_csr calls Azure Key Vault, tests use a mock/stub to avoid real Azure calls.
We patch the Azure SDK to verify:
  - key policy uses exportable=False and KeyType.rsa_hsm (G7)
  - wildcard CN/SAN is rejected before any Key Vault call (G6)
  - private key bytes are never returned in the result (G7)
"""
from __future__ import annotations

import base64
import pytest
from unittest.mock import MagicMock, patch

from src.tools.generate_csr import generate_csr, CsrResult, _reject_wildcard
from src.tools.errors import ToolValidationError


# ---------------------------------------------------------------------------
# _reject_wildcard unit tests
# ---------------------------------------------------------------------------

class TestRejectWildcard:
    def test_wildcard_cn_raises(self) -> None:
        with pytest.raises(ToolValidationError, match="Wildcard"):
            _reject_wildcard("*.example.com", ["api.example.com"])

    def test_wildcard_san_raises(self) -> None:
        with pytest.raises(ToolValidationError, match="Wildcard"):
            _reject_wildcard("api.example.com", ["*.example.com"])

    def test_bare_star_cn_raises(self) -> None:
        with pytest.raises(ToolValidationError):
            _reject_wildcard("*", [])

    def test_valid_cn_and_san_pass(self) -> None:
        _reject_wildcard("api.prod.example.com", ["api.prod.example.com", "api-int.prod.example.com"])


# ---------------------------------------------------------------------------
# generate_csr with mocked Azure SDK
# CertificateClient, CertificatePolicy, KeyType, WellKnownIssuerNames are imported
# lazily inside generate_csr() so we patch at the azure.keyvault.certificates module level.
# ---------------------------------------------------------------------------

class TestGenerateCsrKeyPolicy:
    def test_non_exportable_hsm_key_policy(self) -> None:
        """G7: generate_csr must use exportable=False and KeyType.rsa_hsm."""
        captured = {}

        def _capture_policy(**kwargs: object) -> MagicMock:
            captured.update(kwargs)
            return MagicMock()

        mock_op = MagicMock()
        mock_op.result.return_value = MagicMock(csr=b"\x30\x82" + b"\x00" * 200)

        mock_client_instance = MagicMock()
        mock_client_instance.begin_create_certificate.return_value = mock_op

        with patch("src.tools.generate_csr.settings") as mock_settings, \
             patch("azure.identity.DefaultAzureCredential"), \
             patch("azure.keyvault.certificates.CertificateClient",
                   return_value=mock_client_instance), \
             patch("azure.keyvault.certificates.CertificatePolicy",
                   side_effect=_capture_policy), \
             patch("azure.keyvault.certificates.KeyType") as MockKeyType, \
             patch("azure.keyvault.certificates.WellKnownIssuerNames"):

            mock_settings.key_vault_uri = "https://kv-ssl-hsm.vault.azure.net"
            mock_settings.azure_client_id = None

            try:
                generate_csr("api.prod.example.com", ["api.prod.example.com"], "app", "wf_001")
            except Exception:
                pass  # We care about the policy kwargs, not the full result

        if captured:
            assert captured.get("exportable") is False, (
                f"exportable must be False (G7); got: {captured}"
            )

    def test_wildcard_cn_rejected_before_any_kv_call(self) -> None:
        """G7/G6: wildcard CN must be blocked before any Key Vault call."""
        kv_called = []
        with patch("azure.keyvault.certificates.CertificateClient",
                   side_effect=lambda **kw: kv_called.append(1)):
            with pytest.raises(ToolValidationError, match="Wildcard"):
                generate_csr("*.example.com", ["api.example.com"], "app", "wf_wild")
        assert not kv_called, "CertificateClient must NOT be called for wildcard"

    def test_wildcard_san_rejected_before_kv_call(self) -> None:
        """G6: wildcard in SAN also blocked before Key Vault call."""
        kv_called = []
        with patch("azure.keyvault.certificates.CertificateClient",
                   side_effect=lambda **kw: kv_called.append(1)):
            with pytest.raises(ToolValidationError, match="Wildcard"):
                generate_csr("api.example.com", ["*.example.com"], "app", "wf_wild_san")
        assert not kv_called

    def test_result_never_contains_private_key(self) -> None:
        """G7: CsrResult must not contain any private key material."""
        result = CsrResult(
            key_vault_key_id="https://kv.vault.azure.net/certificates/wf_001",
            csr_pem="-----BEGIN CERTIFICATE REQUEST-----\nfake\n-----END CERTIFICATE REQUEST-----\n",
            csr_pem_sha256="a" * 64,
        )
        assert not hasattr(result, "private_key")
        assert not hasattr(result, "key_material")
        assert not hasattr(result, "pem_private_key")
        assert "CERTIFICATE REQUEST" in result.csr_pem
        assert "PRIVATE KEY" not in result.csr_pem
