# Phase 8b — Development: Middleware

> **Pre-read:** [00-context.md](00-context.md) · depends on 08a
> **Deliverable:** `policy_middleware.py`, `audit_middleware.py` (canonical implementations)
> **Task IDs:** T03, T04
> **Effort estimate:** ~2 person-days

---

## Your Task

Implement `PolicyMiddleware` and `AuditMiddleware` — the enforcement layer for guardrails G1, G3, G4, G6. The canonical code is already specified in [06-security-engineering.md](06-security-engineering.md). This phase is about implementing them with full test coverage.

---

## What to Produce

1. **`src/middleware/policy_middleware.py`** — copy the canonical implementation from P6 and extend
2. **`src/middleware/audit_middleware.py`** — copy the canonical implementation from P6 and extend
3. **`tests/test_policy_middleware.py`** — full unit test suite
4. **`tests/test_audit_middleware.py`** — full unit test suite

---

## `PolicyMiddleware` Requirements

Refer to the canonical implementation in [06-security-engineering.md](06-security-engineering.md#policymiddleware--canonical-implementation).

Additional requirements beyond the canonical code:

- The consecutive-error counter **resets to 0** on any successful tool call
- `PolicyViolation` is a subclass of `RuntimeError`; callers can `except PolicyViolation`
- The middleware is stateful per-orchestrator-instance (not global)
- It does not persist any state to Cosmos — it's an in-memory guardrail only

---

## `AuditMiddleware` Requirements

Refer to the canonical implementation in [06-security-engineering.md](06-security-engineering.md#auditmiddleware--canonical-implementation).

Additional requirements:

- The `_summarize` function must **truncate** at 256 chars and **never** include full PEM/DER bytes
- Log records are emitted via `logging.getLogger("ssl_renewal.audit")` — not `print()`
- The persistence layer (`CosmosRepo`) writes audit records asynchronously; `AuditMiddleware` emits to the logger and optionally accepts a `cosmos_repo` dependency for structured Cosmos writes
- Errors in the audit write **must not** propagate to the tool call chain — log them but don't fail

---

## Test Requirements

### `tests/test_policy_middleware.py`

```python
# Tests you MUST write:

async def test_wildcard_cn_raises():
    """generate_csr with CN='*.example.com' raises PolicyViolation (G6)."""

async def test_wildcard_san_raises():
    """generate_csr with SAN=['api.ok', '*.example.com'] raises PolicyViolation (G6)."""

async def test_valid_cn_passes():
    """generate_csr with CN='api.prod.example.com' calls through to next middleware."""

async def test_consecutive_errors_halt_at_threshold():
    """After max_consecutive_tool_errors failures, the next call raises PolicyViolation (G3)."""

async def test_consecutive_errors_reset_on_success():
    """A successful call resets the consecutive-error counter."""

async def test_non_generate_csr_tool_not_wildcard_checked():
    """Wildcard check only applies to generate_csr, not other tools."""
```

### `tests/test_audit_middleware.py`

```python
# Tests you MUST write:

async def test_emits_start_and_end_on_success():
    """One tool_call.start + one tool_call.end record emitted per tool call (G4)."""

async def test_emits_start_and_error_on_failure():
    """On exception: tool_call.start + tool_call.end(status=error) emitted; exception re-raised."""

async def test_no_pem_bytes_in_output():
    """Audit output summary never contains '-----BEGIN' or '-----END' strings (G8)."""

async def test_no_private_key_material():
    """Audit records never contain 'PRIVATE KEY' or raw base64 key blobs (G8)."""

async def test_summarize_truncates_at_256():
    """_summarize(long_string) returns at most 256 characters."""
```

---

## Acceptance Criteria

- Wildcard CN/SAN blocked before the next middleware or tool is called
- 2 consecutive tool errors (the default) halt + raise `PolicyViolation`
- Every tool call produces exactly 1 start + 1 end log record (success) or 1 start + 1 error (failure)
- No PEM/DER/private-key content appears in any audit record
- All tests pass; coverage ≥ 80% for both files

---

## Verification

```bash
pytest tests/test_policy_middleware.py tests/test_audit_middleware.py -v --cov=src/middleware
```
