# Dev Plane — IBM Bob Agent Architecture

**Classification:** Internal — Platform  
**Scope:** IBM Bob dev-plane agents and their separation from the Azure run plane  

---

## What Bob Is (and Is Not)

Bob is the **IBM multi-agent SDLC platform** that builds and maintains the SSL Renewal Agent system. Bob operates exclusively on the **dev plane** — it generates code, reviews PRs, runs tests, and validates acceptance criteria.

Bob **never** operates on the **run plane**:

| Capability | Run plane (Azure) | Dev plane (Bob) |
|------------|------------------|-----------------|
| Execute certificate renewals | ✅ | ❌ Never |
| Access Key Vault / HSM | ✅ (Managed Identity) | ❌ Denied at APIM |
| Trigger HITL approval | ✅ (Teams card) | ❌ Never |
| Generate CSR | ✅ | ❌ Never |
| Write to Cosmos DB | ✅ | ❌ Never |
| Post to PKI mailbox | ✅ | ❌ Never |
| Read repository (PR diff) | ❌ (no access needed) | ✅ Read-only |
| Comment on PRs | ❌ | ✅ |
| Run test suite | ❌ | ✅ |

---

## Bob's Agents

| Agent | Trigger | Responsibility |
|-------|---------|---------------|
| **Planner** | New work item / sprint | Decompose tasks into subtasks; map to phase plan; produce backlog |
| **Code-Gen** | Sprint task assigned | Generate/modify code against canonical patterns; open PRs |
| **Security Review** | PR opened / updated | Scan for OWASP/LLM Top 10, secret leakage, guardrail regressions; block High/Critical |
| **Validation** | PR check required | Run `pytest --cov=src --cov-fail-under=80` + eval gate; post coverage report |
| **Modernisation** | Weekly scheduled | Dependency upgrades, security patches, MAF minor-release upgrades |
| **Bobalytics** | Daily | Compute dev-plane KPIs; trend charts; alert on regressions |

---

## APIM Denial Policy

Bob's Entra app registration (object ID stored as APIM named value `bobDevPlaneAppId`) is **explicitly denied** on all run-plane APIM products.

The denial is implemented in `infra/modules/apim.bicep` global policy:

```xml
<choose>
  <when condition="@(context.Request.Headers.GetValueOrDefault('Authorization','')
                     .Contains('{{bobDevPlaneAppId}}'))">
    <return-response>
      <set-status code="403" reason="Forbidden" />
      <set-body>{"error":{"code":"dev_plane_forbidden",
                          "message":"Dev-plane identities cannot access run-plane operations."}}</set-body>
    </return-response>
  </when>
</choose>
```

This policy is evaluated **before** any backend call — Bob's token is rejected at the gateway, not inside the agent logic.

> **Testing:** `tests/test_security.py::TestBobDeniedRunPlane` verifies the APIM policy
> logic unit-test path; integration test in `tests/test_e2e/` verifies against real APIM in UAT.

---

## Bob's Token Scopes (`BOB_DEV_PLANE_TOKEN`)

The GitHub Actions secret `BOB_DEV_PLANE_TOKEN` has the following scopes ONLY:

| Scope | Allowed |
|-------|---------|
| Repository: `contents: read` | ✅ |
| Repository: `pull-requests: write` (comment only) | ✅ |
| Dev-plane MCP: PR read, eval run, code scan | ✅ |
| Azure resource management | ❌ |
| Key Vault operations | ❌ |
| Cosmos DB data plane | ❌ |
| Service Bus send/receive | ❌ |
| graph_mail (PKI email) | ❌ |
| approval_tool | ❌ |
| ServiceNow MCP | ❌ |

---

## PR Gate Workflow

Every PR to `main` triggers the Bob Review workflow (`.github/workflows/bob-review.yml`):

```
PR opened/updated
      │
      ▼
Bob Security Review
  ├── G6: no wildcard patterns outside rejection tests
  ├── G7: no exportable=True
  ├── G8: no hard-coded secrets
  ├── G1: approval_tool HITL guard present
  └── Run full unit + security test suite
      │
      ▼ (only if security passes)
Bob Validation
  ├── Coverage gate: ≥ 80%
  ├── T01–T20 structural checks
  └── Post coverage report as PR comment
      │
      ▼
Human reviewer (1 required)
      │
      ▼
Merge to main → deploy pipeline
```

**Merge is blocked** if:
- Any High/Critical security finding
- Coverage < 80%
- Any guardrail test failure
- Bob's own Validation run fails

---

## Dev-Plane KPIs (Bobalytics)

| KPI | Target | Measurement |
|-----|--------|-------------|
| PR cycle time (open → merged) | < 2 business days | GitHub API: merged_at - created_at |
| % PRs with Security findings | < 10% | Bob Security Review output |
| Defect escape rate to UAT | < 5% of PRs | Post-merge UAT failures / total PRs |
| Test coverage trend | Monotonically non-decreasing | `coverage.xml` per PR |
| MTTR High/Critical findings | < 1 business day | Jira resolution time |

---

## Modernisation Process

Bob's Modernisation agent runs weekly:

1. `pip-audit` scan — flags new CVEs in dependencies
2. `dependabot` alerts aggregation — open upgrade PRs for security patches
3. MAF minor release check — if a new MAF patch is available, open a test PR
4. All modernisation PRs go through the same Bob review + human review gate
5. Production never receives dependency upgrades without full test suite green + human approval

---

## Configuration

Bob agents are configured via the IBM Bob SDLC platform (not documented here — managed separately).  
The only Bob-facing configuration in this repo is:

| File | Bob-relevant content |
|------|---------------------|
| `.github/workflows/bob-review.yml` | Bob security + validation job steps |
| `infra/modules/apim.bicep` | `bobDevPlaneAppId` named value + denial policy |
| `pyproject.toml` | `ruff`, `mypy`, `pytest` settings Bob's Validation agent runs |
