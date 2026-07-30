# Phase 16 — Dev Plane: IBM Bob

> **Pre-read:** [00-context.md](00-context.md) · depends on P10 (CI/CD)
> **Deliverable:** Bob agent configuration, APIM denial policy, PR gate
> **Task IDs:** T18
> **Effort estimate:** ~3 person-days

---

## Your Task

Configure the IBM Bob dev-plane agents, enforce their isolation from the run plane at APIM, and wire the PR review gate. Bob builds the system; Bob never runs it.

---

## What to Produce

1. **`.github/workflows/bob-review.yml`** — Bob PR gate (from P10; extend here)
2. **`infra/apim.bicep`** — includes Bob-denial policy (from P11; extend here)
3. **`docs/dev-plane.md`** — Bob agent roles, cross-vendor separation, guardrails
4. **`tests/test_security.py`** — `test_bob_denied_run_plane` test case

---

## Bob's Role in This Project

Bob is the **multi-agent SDLC platform** (IBM) that builds and maintains the run plane. Bob's agents are:

| Bob Agent | Responsibility |
|-----------|---------------|
| **Planner** | Decompose work items into tasks; map to phase plan; produce backlog entries |
| **Code-Gen** | Generate/modify code against the canonical patterns (P8); open PRs |
| **Security Review** | Scan PRs for OWASP/LLM Top 10 issues, secret leakage, guardrail regressions; block High/Critical |
| **Validation** | Run tests/evals; verify acceptance criteria; check coverage gate |
| **Modernisation** | Dependency upgrades (MAF minor releases, security patches); refactors; tech-debt paydown |
| **Bobalytics** | Dev-plane KPIs: PR cycle time, defect escape rate, review coverage, eval trends |

---

## Cross-Vendor Separation (non-negotiable)

| Aspect | Run plane (Microsoft) | Dev plane (IBM Bob) |
|--------|----------------------|---------------------|
| Purpose | Execute renewals | Build/maintain the system |
| Secrets/Key Vault | Yes (MI, HSM) | **Never** |
| HITL approvals | Fires them | **Never** |
| Cert minting | Yes | **Never** |
| Repo access | Via CI (OIDC) | Read-only PR scope |
| Shared surface | — | **Only** the MCP fabric |
| Enforcement | — | **Denied at APIM** for run-plane scopes |

---

## APIM Denial Policy (add to `infra/apim.bicep`)

Bob's Entra app registration must be **explicitly denied** from all run-plane APIM products. Implement this as an APIM `inbound` policy on every run-plane API:

```xml
<!-- APIM policy: deny Bob's app registration on all run-plane APIs -->
<inbound>
    <base />
    <choose>
        <when condition="@(context.Request.Headers.GetValueOrDefault("Authorization","").Contains("oid=<BOB_APP_OBJECT_ID>"))">
            <return-response>
                <set-status code="403" reason="Forbidden" />
                <set-body>{"error":{"code":"dev_plane_forbidden","message":"Dev-plane identities cannot access run-plane operations."}}</set-body>
            </return-response>
        </when>
    </choose>
</inbound>
```

Also implement via APIM named value / policy expression checking the token's `appid` or `oid` claim against `BOB_APP_CLIENT_ID` (stored as a named value, not hard-coded in the policy).

---

## Guardrails on Bob

1. **Bob's token** (`BOB_DEV_PLANE_TOKEN` in GitHub Actions) has only:
   - Read-only repo access
   - Dev-plane MCP scopes (PR comment, eval run, read code)
   - **No** Azure resource scopes

2. **Bob cannot:**
   - Call `generate_csr`, `verify_cer`, `request_approval`, or any native tool
   - Access Key Vault (HSM)
   - Trigger a HITL approval
   - Create Jira tickets or ServiceNow changes

3. **Bob-authored code** is gated by:
   - Bob's own Validation agent (runs the full guardrail/security test suite before approving)
   - A human reviewer (required before merge to `main`)

---

## `test_bob_denied_run_plane` Test

```python
# tests/test_security.py

async def test_bob_denied_run_plane():
    """
    Simulate a request to a run-plane APIM endpoint using Bob's dev-plane token.
    Assert the response is 403 Forbidden.
    
    In unit testing: mock the APIM policy check.
    In integration testing (UAT): make an actual HTTP call with a test Bob token.
    """
    # Unit test version:
    bob_token = "mock_bob_dev_plane_token_with_bob_oid"
    apim_policy = APIMPolicyEnforcer(bob_app_client_id=settings.bob_app_client_id)
    
    with pytest.raises(PermissionError, match="dev_plane_forbidden"):
        await apim_policy.check_token(bob_token, resource="generate_csr")
    
    # Integration test version (requires actual APIM + real Bob test token):
    # response = await http_client.post(
    #     f"{settings.apim_gateway_url}/api/v1/renew",
    #     headers={"Authorization": f"Bearer {bob_test_token}"},
    #     json={"cn": "test.example.com", "san": ["test.example.com"]}
    # )
    # assert response.status_code == 403
    # assert response.json()["error"]["code"] == "dev_plane_forbidden"
```

---

## Dev-Plane KPIs (Bobalytics)

Document targets in `docs/dev-plane.md`:

| KPI | Target |
|-----|--------|
| PR cycle time (open → merged) | < 2 business days |
| % PRs with Security findings | < 10% |
| Defect escape rate to UAT | < 5% of PRs |
| Test coverage trend | Monotonically non-decreasing |
| Mean time to remediate High/Critical finding | < 1 business day |

---

## Dev-Plane Workflows

### PR Review Gate (runs on every PR)
1. Bob Security Review agent scans the diff for OWASP/LLM Top 10 issues, secret leakage, guardrail regressions
2. Bob Validation agent runs: `pytest --cov=src --cov-fail-under=80` + eval gate
3. Results posted as PR comments
4. Merge **blocked** if: any High/Critical finding, OR coverage < 80%, OR evals < 0.90

### Modernisation (scheduled weekly)
1. Bob Modernisation agent checks for dependency updates (`pip-audit`, dependabot alerts)
2. Proposes upgrade PRs for security patches; proposes MAF minor-release upgrades on test branch
3. Same PR gate applies to modernisation PRs

---

## Acceptance Criteria

- Bob's token is refused on all run-plane APIM endpoints (verified by `test_bob_denied_run_plane`)
- Bob has read-only repo access; cannot push directly to `main`
- PR gate is active: every PR gets Bob Security Review + Validation comments before merge
- No `BOB_DEV_PLANE_TOKEN` has Azure resource management permissions

---

## Verification

```bash
pytest tests/test_security.py::test_bob_denied_run_plane -v

# In UAT (integration):
# curl -X POST https://<apim-gateway>/api/v1/renew \
#   -H "Authorization: Bearer <bob-test-token>" \
#   -H "Content-Type: application/json" \
#   -d '{"cn":"test.example.com","san":["test.example.com"]}' \
# → expect 403 {"error":{"code":"dev_plane_forbidden",...}}
```

A dry-run PR demonstrates Bob commenting security findings and the merge check blocking on a known issue.
