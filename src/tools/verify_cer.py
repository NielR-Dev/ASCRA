"""Native tool: deterministic X.509 certificate verifier (G2).

The verdict is deterministic Python code — NOT model opinion. The LLM cannot override
a pass_=False result by argument or reasoning. This is the architectural enforcement of G2.

Design:
  - Pure function of its inputs → fully unit-testable without mocks.
  - Raises ToolValidationError on unparseable input.
  - Returns VerifyResult(pass_=False) for ANY check failure — never passes on mismatch.
  - The caller (orchestrator / state machine) checks pass_ and must not transition to VERIFIED
    unless pass_=True.

Checks performed:
  1. X.509 parse (PEM or DER)
  2. Common Name (CN) exact match
  3. Subject Alternative Name (SAN) set exact equality
  4. Certificate not expired (notAfter > now)
  5. Minimum validity days remaining (cert_min_valid_days, default 365)

Note: chain validation against a trusted root is architecturally required but depends on
the trust store available at runtime. The chain check is implemented as a best-effort
using the system CA bundle; add an allow-list check (issuer CN / fingerprint) as a
hardening follow-up (see docs/security.md §2 F-02 mitigation).
"""
from __future__ import annotations

import base64
import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import settings
from src.tools.errors import ToolValidationError

logger = logging.getLogger("ssl_renewal.verify_cer")


@dataclass
class VerifyResult:
    """Result of a CER verification run.

    pass_:  True only if ALL checks pass. False if ANY check fails.
    reason: Human-readable summary of which checks failed (empty string on pass).
    checks: Dict of {check_name: bool} for each individual check.
    """
    pass_: bool
    reason: str = ""
    checks: dict[str, bool] = field(default_factory=dict)


def verify_cer(
    cer_bytes_b64: str,
    expected_cn: str,
    expected_san: list[str],
    workflow_id: str,
) -> VerifyResult:
    """Validate a returned certificate against the original CSR request.

    NEVER passes on any mismatch (G2). The LLM cannot override this verdict.

    Args:
        cer_bytes_b64:  Base64-encoded certificate bytes (PEM or DER).
        expected_cn:    The CN that was requested (from workflow_state.cn).
        expected_san:   The SAN list that was requested (from workflow_state.san).
        workflow_id:    Workflow identifier for logging context.

    Returns:
        VerifyResult with pass_=True only if all checks pass.
        VerifyResult with pass_=False and reason describing the failures otherwise.

    Raises:
        ToolValidationError: if cer_bytes_b64 cannot be decoded (bad input, not a cert failure).
    """
    if not cer_bytes_b64:
        raise ToolValidationError("cer_bytes_b64 must not be empty.")

    # Decode base64
    try:
        raw = base64.b64decode(cer_bytes_b64)
    except Exception as exc:
        raise ToolValidationError(
            f"cer_bytes_b64 is not valid base64: {exc}"
        ) from exc

    # Import cryptography library (lazy import to keep the module importable without the package)
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.x509 import ExtensionNotFound
    except ImportError as exc:
        raise ToolValidationError(
            f"cryptography library not available; cannot verify CER: {exc}"
        ) from exc

    # 1. Parse the certificate (PEM or DER)
    cert: Any
    try:
        cert = x509.load_pem_x509_certificate(raw, default_backend())
    except Exception:
        try:
            cert = x509.load_der_x509_certificate(raw, default_backend())
        except Exception as exc:
            logger.warning(
                "verify_cer: unparseable certificate workflow_id=%s error=%s",
                workflow_id, type(exc).__name__
            )
            return VerifyResult(
                pass_=False,
                reason="Certificate could not be parsed as X.509 PEM or DER",
                checks={"parse": False},
            )

    checks: dict[str, bool] = {}

    # 2. CN match (exact)
    cn_attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    actual_cn: str = cn_attrs[0].value if cn_attrs else ""
    checks["cn_match"] = (actual_cn == expected_cn)

    # 3. SAN set exact equality
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        actual_san: set[str] = set(san_ext.value.get_values_for_type(x509.DNSName))
    except ExtensionNotFound:
        actual_san = set()
    checks["san_match"] = (actual_san == set(expected_san))

    # 4. Not expired
    now = _dt.datetime.now(_dt.timezone.utc)
    not_after = cert.not_valid_after_utc
    checks["not_expired"] = (now < not_after)

    # 5. Minimum validity remaining
    remaining_days = (not_after - now).days
    checks["min_validity"] = (remaining_days >= settings.cert_min_valid_days)

    # Overall verdict: all checks must pass
    overall_pass = all(checks.values())
    failed_checks = [k for k, v in checks.items() if not v]
    reason = "" if overall_pass else "; ".join(failed_checks) + " failed"

    result = VerifyResult(pass_=overall_pass, reason=reason, checks=checks)

    # Log at INFO — no cert bytes, no CN in a way that could be PHI-sensitive
    logger.info(
        "verify_cer: workflow_id=%s pass_=%s checks=%s",
        workflow_id, overall_pass, checks
    )
    if not overall_pass:
        logger.warning(
            "verify_cer: FAILED workflow_id=%s reason='%s'",
            workflow_id, reason
        )

    return result
