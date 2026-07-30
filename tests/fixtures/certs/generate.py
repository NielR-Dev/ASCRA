"""Generate test X.509 certificates for ASCRA test fixtures.

Run once to (re)generate the PEM files in this directory:
    python tests/fixtures/certs/generate.py

Also importable by E2E tests to generate certificates for dynamic CNs at test time:
    from tests.fixtures.certs.generate import make_cert_pem
"""
from __future__ import annotations

import datetime
import pathlib
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_HERE = pathlib.Path(__file__).parent


def make_cert_pem(
    cn: str,
    san: list[str] | None = None,
    days_valid: int = 400,
    expired: bool = False,
) -> bytes:
    """Return a self-signed PEM certificate as bytes.

    Args:
        cn: Common Name for the certificate subject.
        san: List of DNS SANs. Defaults to [cn].
        days_valid: Days the certificate should be valid from now.
        expired: If True, set validity window entirely in the past.
    """
    if san is None:
        san = [cn]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

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
    return cert.public_bytes(serialization.Encoding.PEM)


def _write(filename: str, pem: bytes) -> None:
    path = _HERE / filename
    path.write_bytes(pem)
    print(f"  wrote {path}")


def main() -> None:
    print("Generating test certificates in", _HERE)

    _write(
        "test_cert_valid.pem",
        make_cert_pem(cn="e2e-test.test-domain.com", days_valid=400),
    )
    _write(
        "test_cert_valid_san.pem",
        make_cert_pem(
            cn="e2e-test.test-domain.com",
            san=["e2e-test.test-domain.com", "e2e-int.test-domain.com"],
            days_valid=400,
        ),
    )
    _write(
        "test_cert_expired.pem",
        make_cert_pem(cn="e2e-test.test-domain.com", days_valid=365, expired=True),
    )
    _write(
        "test_cert_short_validity.pem",
        make_cert_pem(cn="e2e-test.test-domain.com", days_valid=300),
    )
    _write(
        "test_cert_cn_mismatch.pem",
        make_cert_pem(cn="wrong-host.test-domain.com", days_valid=400),
    )

    print("Done.")


if __name__ == "__main__":
    main()
    sys.exit(0)
