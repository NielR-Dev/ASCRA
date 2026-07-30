# Environment Configuration

Three GitHub Environments gate deployment: `dev`, `uat`, and `prod`.
Each environment uses OIDC-federated credentials (no stored passwords or service-principal secrets — G8).

## Environment Matrix

| Environment | Purpose | Trigger | PKI Mailbox | Approver | Kill-switch default | Notes |
|-------------|---------|---------|-------------|----------|---------------------|-------|
| `dev` | Ephemeral CI-driven sandbox | Every push to a feature branch | Mocked (no real PKI calls) | Auto-approved | Enabled | Bicep deploys a minimal stack; MCP endpoints point to stubs |
| `uat` | Stable sandbox for manual QA and pre-release validation | PR merge to `release/*`; manual `workflow_dispatch` | Sandbox PKI mailbox (`pki-sandbox@example.com`) | Team-lead approval | Enabled | Full stack; synthetic certs only; Dynatrace alerts mocked |
| `prod` | Production | Push to `main` (gated by human reviewer) | Real PKI mailbox (`pki@example.com`) | Product Director | Enabled (can be toggled to `false` via `ORCHESTRATOR_ENABLED` env var in Key Vault) | All guardrails fully enforced |

## GitHub Secrets Required Per Environment

All secrets are set in the GitHub repository → Settings → Environments.  
**No secrets appear in workflow YAML.** All cloud credentials use OIDC federation.

| Secret name | Description | Environment(s) |
|-------------|-------------|----------------|
| `AZURE_CLIENT_ID` | Managed Identity / app registration client ID for OIDC | dev, uat, prod |
| `AZURE_TENANT_ID` | Azure AD tenant ID | dev, uat, prod |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID | dev, uat, prod |
| `FOUNDRY_PROJECT_ENDPOINT_PROD` | AI Foundry project endpoint (production) | prod |
| `FOUNDRY_PROJECT_ENDPOINT_UAT` | AI Foundry project endpoint (UAT) | uat |
| `KEY_VAULT_URI_PROD` | Key Vault managed HSM URI (production) | prod |
| `COSMOS_ENDPOINT_PROD` | Cosmos DB account endpoint (production) | prod |
| `BLOB_ACCOUNT_URL_PROD` | Blob Storage account URL (production) | prod |
| `FUNC_KEY_PROD` | Function App host key for smoke-test | prod |
| `BOB_DEV_PLANE_TOKEN` | IBM Bob dev-plane token (read-only repo + dev MCP only) | dev, uat, prod |

> **Important:** `BOB_DEV_PLANE_TOKEN` has **read-only** repository access and dev-plane MCP scopes only.
> APIM denies this token for all run-plane operations (Key Vault, graph_mail, approval, ServiceNow).
> This is enforced by APIM JWT policy — Bob cannot reach production HSM or approval endpoints even if code were compromised.

## Configuration Separation

All environment-specific values live in `infra/*.bicepparam` files and Key Vault references — never in source code.  
The `Settings` class in `src/config.py` reads from environment variables + Key Vault references at runtime.

| Config path | What it controls |
|-------------|-----------------|
| `infra/dev.bicepparam` | Resource names/SKUs for dev (smaller SKUs, no redundancy) |
| `infra/uat.bicepparam` | Resource names/SKUs for UAT (production-like but reduced capacity) |
| `infra/prod.bicepparam` | Resource names/SKUs for production (zone-redundant, full capacity) |
| Key Vault secrets (prod) | `pki_mailbox`, `pd_approver_email`, `slack_signing_secret`, `slack_bot_token`, AOAI key |

## Kill-Switch Procedure

To halt all new renewals without taking down infrastructure:

```bash
# Set kill-switch via Key Vault (no code change, no redeployment)
az keyvault secret set \
  --vault-name kv-ssl-renewal-prod \
  --name orchestrator-enabled \
  --value false

# The Functions runtime reads ORCHESTRATOR_ENABLED from a KV reference.
# New webhook events are rejected with HTTP 503.
# In-flight workflows continue to their current state.
```

See `docs/runbook.md` for the full incident response procedure.
