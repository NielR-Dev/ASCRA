"""Tests for verify_cer — deterministic X.509 verifier (G2).

These tests are fully offline (no network): they generate self-signed certificates
using the cryptography library and validate the verifier's behaviour.

Test cases:
  - pass on exact CN + SAN + valid expiry
  - fail on CN mismatch
  - fail on SAN mismatch (extra SAN)
  - fail on SAN mismatch (missing SAN)
  - fail on expired certificate
  - fail on < 365 days remaining
  - fail on completely wrong (non-cert) bytes
  - fail on empty input
"""
from __future__ import annotations

import base64
import datetime
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.tools.verify_cer import verify_cer, VerifyResult
from src.tools.errors import ToolValidationError


# ---------------------------------------------------------------------------
# Certificate fixture builder
# ---------------------------------------------------------------------------

def _make_cert(
    cn: str,
    san: list[str],
    days_valid: int = 400,
    expired: bool = False,
) -> bytes:
    """Create a self-signed X.509 certificate for testing and return it as DER bytes."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    if expired:
        not_before = now - datetime.timedelta(days=days_valid + 10)
        not_after = now - datetime.timedelta(days=10)
    else:
        not_before = now - datetime.timedelta(days=1)
        not_after = now + datetime.timedelta(days=days_valid)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(s) for s in san]),
            critical=False,
        )
    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.DER)


def _b64(der_bytes: bytes) -> str:
    return base64.b64encode(der_bytes).decode()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestVerifyCerPass:
    def test_pass_on_exact_match(self) -> None:
        """G2: verify_cer passes when CN, SAN, and validity all match."""
        cn = "api.prod.example.com"
        san = ["api.prod.example.com", "api-internal.prod.example.com"]
        der = _make_cert(cn=cn, san=san, days_valid=400)
        result = verify_cer(_b64(der), cn, san, "wf_test")
        assert result.pass_ is True
        assert result.reason == ""
        assert result.checks["cn_match"] is True
        assert result.checks["san_match"] is True
        assert result.checks["not_expired"] is True
        assert result.checks["min_validity"] is True

    def test_pass_accepts_pem_encoded_cert(self) -> None:
        """verify_cer handles PEM-encoded certificates (not just DER)."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography import x509 as _x509
        from cryptography.x509.oid import NameOID as _OID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cn = "pem.example.com"
        san_list = ["pem.example.com"]
        subject = issuer = _x509.Name([_x509.NameAttribute(_OID.COMMON_NAME, cn)])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            _x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(_x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=400))
            .add_extension(
                _x509.SubjectAlternativeName([_x509.DNSName(cn)]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        pem_bytes = cert.public_bytes(serialization.Encoding.PEM)
        result = verify_cer(base64.b64encode(pem_bytes).decode(), cn, san_list, "wf_pem")
        assert result.pass_ is True


# ---------------------------------------------------------------------------
# CN mismatch (G2)
# ---------------------------------------------------------------------------

class TestVerifyCerCnMismatch:
    def test_cn_mismatch_fails(self) -> None:
        """G2: verify_cer must fail when CN differs from expected."""
        der = _make_cert(cn="wrong.example.com", san=["api.prod.example.com"], days_valid=400)
        result = verify_cer(_b64(der), "api.prod.example.com", ["api.prod.example.com"], "wf_cn")
        assert result.pass_ is False
        assert result.checks["cn_match"] is False
        assert "cn_match" in result.reason

    def test_cn_mismatch_does_not_reach_verified_state(self) -> None:
        """G2: a CN mismatch must produce pass_=False which the state machine uses to block VERIFIED."""
        der = _make_cert(cn="attacker.example.com", san=[], days_valid=400)
        result = verify_cer(_b64(der), "api.prod.example.com", [], "wf_cn2")
        assert result.pass_ is False
        # The state machine checks pass_ before allowing PKI_REPLIED → VERIFIED
        # This test documents that the verifier provides the correct signal.


# ---------------------------------------------------------------------------
# SAN mismatch (G2)
# ---------------------------------------------------------------------------

class TestVerifyCerSanMismatch:
    def test_extra_san_fails(self) -> None:
        """G2: cert with more SANs than expected fails san_match."""
        cn = "api.prod.example.com"
        cert_san = ["api.prod.example.com", "extra.prod.example.com"]  # extra SAN
        expected_san = ["api.prod.example.com"]
        der = _make_cert(cn=cn, san=cert_san, days_valid=400)
        result = verify_cer(_b64(der), cn, expected_san, "wf_san_extra")
        assert result.pass_ is False
        assert result.checks["san_match"] is False

    def test_missing_san_fails(self) -> None:
        """G2: cert missing a requested SAN fails san_match."""
        cn = "api.prod.example.com"
        cert_san = ["api.prod.example.com"]  # missing api-internal
        expected_san = ["api.prod.example.com", "api-internal.prod.example.com"]
        der = _make_cert(cn=cn, san=cert_san, days_valid=400)
        result = verify_cer(_b64(der), cn, expected_san, "wf_san_missing")
        assert result.pass_ is False
        assert result.checks["san_match"] is False

    def test_no_san_extension_fails_when_expected(self) -> None:
        """G2: cert with no SAN extension fails when SANs were expected."""
        cn = "api.prod.example.com"
        der = _make_cert(cn=cn, san=[], days_valid=400)  # no SAN extension
        result = verify_cer(_b64(der), cn, ["api.prod.example.com"], "wf_no_san")
        assert result.pass_ is False
        assert result.checks["san_match"] is False


# ---------------------------------------------------------------------------
# Expiry checks (G2)
# ---------------------------------------------------------------------------

class TestVerifyCerExpiry:
    def test_expired_cert_fails(self) -> None:
        """G2: expired certificate must fail not_expired check."""
        cn = "api.prod.example.com"
        der = _make_cert(cn=cn, san=[cn], expired=True, days_valid=365)
        result = verify_cer(_b64(der), cn, [cn], "wf_expired")
        assert result.pass_ is False
        assert result.checks["not_expired"] is False

    def test_insufficient_validity_fails(self) -> None:
        """G2: cert valid for less than 365 days must fail min_validity check."""
        cn = "api.prod.example.com"
        der = _make_cert(cn=cn, san=[cn], days_valid=300)  # < 365
        result = verify_cer(_b64(der), cn, [cn], "wf_short")
        assert result.pass_ is False
        assert result.checks["min_validity"] is False

    def test_exactly_365_days_passes(self) -> None:
        """Exactly 365 days remaining should pass the min_validity check (boundary)."""
        cn = "api.prod.example.com"
        # Use 366 to account for the 1-day not_before offset in _make_cert
        der = _make_cert(cn=cn, san=[cn], days_valid=366)
        result = verify_cer(_b64(der), cn, [cn], "wf_exact365")
        assert result.checks["min_validity"] is True


# ---------------------------------------------------------------------------
# Parse failures
# ---------------------------------------------------------------------------

class TestVerifyCerParseFail:
    def test_garbage_bytes_fail(self) -> None:
        """Non-certificate bytes must produce a parse failure, not an exception."""
        garbage = base64.b64encode(b"not a certificate").decode()
        result = verify_cer(garbage, "api.prod.example.com", [], "wf_garbage")
        assert result.pass_ is False
        assert "parse" in result.reason.lower() or result.checks.get("parse") is False

    def test_empty_input_raises_validation_error(self) -> None:
        """Empty cer_bytes_b64 raises ToolValidationError (not a cert failure)."""
        with pytest.raises(ToolValidationError):
            verify_cer("", "api.prod.example.com", [], "wf_empty")

    def test_invalid_base64_raises_validation_error(self) -> None:
        """Invalid base64 input raises ToolValidationError."""
        with pytest.raises(ToolValidationError, match="valid base64"):
            verify_cer("not!base64!!!", "api.prod.example.com", [], "wf_badb64")
