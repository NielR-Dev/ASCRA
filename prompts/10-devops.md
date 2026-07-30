# Phase 10 — DevOps

> **Pre-read:** [00-context.md](00-context.md) · depends on P8, P9
> **Deliverable:** GitHub Actions CI/CD pipelines, branch config, environment setup
> **Task IDs:** T16
> **Effort estimate:** ~3–4 person-days

---

## Your Task

Set up the full CI/CD pipeline: lint/test/audit gate, Bicep what-if gate, deployment pipeline with environment reviewers, and the IBM Bob PR review gate. No stored cloud credentials — OIDC only.

---

## What to Produce

1. **`.github/workflows/deploy.yml`** — validate → what-if → deploy pipeline
2. **`.github/workflows/bob-review.yml`** — IBM Bob security + validation PR gate
3. **`docs/environments.md`** — dev/uat/prod environment config notes
4. **`infra/dev.bicepparam`**, **`infra/uat.bicepparam`**, **`infra/prod.bicepparam`** — env params

---

## `deploy.yml` — Canonical CI/CD Pipeline

```yaml
name: deploy
on:
  push:
    branches: [main]
  workflow_dispatch: {}

permissions:
  id-token: write   # OIDC federation — no stored cloud credentials
  contents: read

env:
  RG: ssl-renewal-rg-prod
  FUNC_APP: ssl-renewal-func-prod

jobs:
  validate:
    name: Lint · Test · Audit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - name: Install dependencies
        run: pip install -r requirements.txt -r requirements-dev.txt
      - name: Lint + type-check + format
        run: ruff check . && mypy src && black --check .
      - name: Unit + security tests (coverage gate)
        run: pytest --cov=src --cov-fail-under=80 --ignore=tests/test_e2e -v
      - name: Dependency vulnerability scan
        run: pip-audit

  whatif:
    name: Bicep What-If
    needs: validate
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - name: Bicep what-if (no destructive changes without review)
        run: >
          az deployment group what-if
          -g ${{ env.RG }}
          --template-file infra/main.bicep
          --parameters @infra/prod.bicepparam

  deploy:
    name: Deploy to Prod
    needs: whatif
    runs-on: ubuntu-latest
    environment: prod   # requires human reviewer approval in GitHub Environments
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - name: Deploy Bicep
        run: >
          az deployment group create
          -g ${{ env.RG }}
          --template-file infra/main.bicep
          --parameters @infra/prod.bicepparam
      - name: Deploy Function App
        run: func azure functionapp publish ${{ env.FUNC_APP }} --python
      - name: Import Logic Apps
        run: python -m scripts.import_logic_apps
      - name: Run PromptFlow evals (fail-fast gate)
        run: python -m scripts.run_promptflow_evals --fail-under 0.9
      - name: Run E2E synthetic renewals
        run: pytest tests/test_e2e/ -v -m e2e
```

---

## `bob-review.yml` — IBM Bob PR Gate

```yaml
name: bob-review
on:
  pull_request:
    types: [opened, synchronize]

permissions:
  contents: read
  pull-requests: write   # Bob can comment

jobs:
  bob-security-review:
    name: IBM Bob — Security Review
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Invoke Bob Security Review agent
        # Bob has READ-ONLY repo scope + dev-plane MCP only.
        # Bob CANNOT reach Key Vault, approval endpoints, or any run-plane MCP.
        # Results are posted as PR comments. Merge blocked on High/Critical findings.
        run: |
          # TODO: replace with the actual Bob CLI invocation
          # bob review security --pr ${{ github.event.pull_request.number }} --fail-on HIGH
          echo "Bob security review invoked"
        env:
          BOB_TOKEN: ${{ secrets.BOB_DEV_PLANE_TOKEN }}

  bob-validation:
    name: IBM Bob — Acceptance Criteria Validation
    runs-on: ubuntu-latest
    needs: bob-security-review
    steps:
      - uses: actions/checkout@v4
      - name: Invoke Bob Validation agent
        run: |
          # bob validate --pr ${{ github.event.pull_request.number }} --coverage-gate 80
          echo "Bob validation invoked"
        env:
          BOB_TOKEN: ${{ secrets.BOB_DEV_PLANE_TOKEN }}
```

**Key constraints on Bob's token:**
- `BOB_DEV_PLANE_TOKEN` has read-only repo access + dev-plane MCP scopes only
- APIM policy explicitly rejects this token for run-plane ops (Key Vault, graph_mail, approval, ServiceNow)
- Test: `test_bob_denied_run_plane` asserts this at APIM level

---

## Branching Strategy

- **Trunk-based development** with short-lived feature branches
- PRs require: ✅ green CI (`validate` job) + ✅ IBM Bob review (no High/Critical) + ✅ one human approver
- `main` branch is **protected**: no direct pushes; all changes via PR
- Tags follow **SemVer** (`v1.0.0`, `v1.1.0`, etc.)
- Conventional commits (`feat:`, `fix:`, `chore:`, `security:`, etc.)

---

## Environments

| Environment | Purpose | PKI | Approver | Kill-switch |
|-------------|---------|-----|----------|-------------|
| `dev` | Ephemeral, CI-driven | Mocked (no real PKI calls) | Auto-approved (test only) | On by default |
| `uat` | Sandbox, manual testing | Sandbox PKI mailbox; synthetic certs | Real approver (team lead) | Off (enabled) |
| `prod` | Production | Real PKI mailbox | Real PD | Off (enabled); manual toggle available |

Config differences managed via `*.bicepparam` files + Key Vault references (no env-specific secrets in code).

---

## Packaging Notes

- Azure Functions: Python isolated-process; deployed via `func azure functionapp publish`
- Container Apps image (`Dockerfile`) provided as fallback for self-hosted MAF
- Logic App Standard definitions in `logicapps/` — deployed via `import_logic_apps.py` script
- Copilot topics exported to `copilot/` — imported manually into the Copilot Studio environment

---

## Acceptance Criteria

- No long-lived cloud credentials stored in GitHub (OIDC only)
- `main` branch is protected; all deployments gated by human reviewer in GitHub Environments
- `deploy` job only runs after `validate` and `what-if` succeed
- Bob review gate blocks merge on High/Critical findings
- Bob's token cannot access run-plane MCP endpoints (APIM rejects it)

---

## Verification

- A dry-run PR shows Bob review comments + green CI checks before merge
- `az deployment group what-if` runs clean with no unexpected destructive changes
- A tagged release deploys end-to-end to `uat` with E2E synthetic renewals passing
- CI logs show OIDC token exchange (no `az login --service-principal --password`)
