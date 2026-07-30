# Deployment Guide — Autonomous SSL Certificate Renewal Agent

## Prerequisites

- Azure subscription with Contributor + User Access Administrator role
- Azure CLI (`az login`) with OIDC federated credential configured in GitHub
- Bicep CLI: `az bicep install`
- Python 3.11+ + Azure Functions Core Tools v4

---

## First-Time Setup

### 1. Create Resource Groups

```bash
# Create one resource group per environment
az group create --name ssl-renewal-rg-dev  --location eastus
az group create --name ssl-renewal-rg-uat  --location eastus
az group create --name ssl-renewal-rg-prod --location eastus
```

### 2. Configure OIDC Federated Credentials (GitHub → Azure)

```bash
# Create app registration for GitHub Actions OIDC
APP_ID=$(az ad app create --display-name "ssl-renewal-github-actions" --query appId -o tsv)
OBJECT_ID=$(az ad app show --id $APP_ID --query id -o tsv)

# Create service principal
az ad sp create --id $APP_ID

# Add federated credential for prod environment
az ad app federated-credential create \
  --id $OBJECT_ID \
  --parameters '{
    "name": "github-prod",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:your-org/ssl-renewal-agent:environment:prod",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# Grant Contributor role on prod resource group
az role assignment create \
  --assignee $APP_ID \
  --role Contributor \
  --scope /subscriptions/$(az account show --query id -o tsv)/resourceGroups/ssl-renewal-rg-prod
```

### 3. Add GitHub Secrets

In GitHub → Settings → Environments → `prod`, add:

| Secret | Value |
|--------|-------|
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |

---

## Bicep Module Deployment Order

Deploy modules in the order listed (Bicep's `dependsOn` handles this automatically in `main.bicep`).

```
1. Network + Identity         → infra/modules/network.bicep, identity.bicep
2. Data Plane                 → infra/cosmos.bicep, storage.bicep, modules/keyvault.bicep
3. AI Runtime                 → infra/modules/openai.bicep, foundry.bicep
4. APIM + MCP Proxy           → infra/modules/apim.bicep
5. Messaging                  → infra/modules/servicebus.bicep, eventgrid.bicep
6. Compute                    → infra/modules/functionapp.bicep, logicapp.bicep
7. Observability              → infra/modules/appinsights.bicep
```

### Deploy Dev Environment

```bash
az deployment group create \
  --resource-group ssl-renewal-rg-dev \
  --template-file infra/main.bicep \
  --parameters @infra/dev.bicepparam \
  --name "deploy-dev-$(date +%Y%m%d%H%M%S)"
```

### Deploy UAT Environment

```bash
az deployment group create \
  --resource-group ssl-renewal-rg-uat \
  --template-file infra/main.bicep \
  --parameters @infra/uat.bicepparam \
  --name "deploy-uat-$(date +%Y%m%d%H%M%S)"
```

### Deploy Prod Environment (via CI/CD only)

Production deployments are **only** triggered via the GitHub Actions `deploy.yml` workflow.
Direct portal deployments to prod are blocked by Azure Policy.

```bash
# Prod deploy via GitHub Actions:
# 1. Push to main (or manually trigger workflow_dispatch)
# 2. GitHub Actions: validate → whatif → human reviewer approves → deploy
```

---

## Post-Deployment Steps

### 1. Deploy Function App Code

```bash
func azure functionapp publish ssl-prod-func --python --build remote
```

### 2. Import Logic App Definitions

```bash
python -m scripts.import_logic_apps \
  --resource-group ssl-renewal-rg-prod \
  --logic-app-name ssl-prod-la
```

### 3. Configure Copilot Studio (manual)

1. Open Copilot Studio → Environment → Import
2. Import `copilot/topics.md` topic definitions
3. Configure the approval callback URL: `https://ssl-prod-func.azurewebsites.net/api/approval-callback`
4. Test the approval flow with a synthetic renewal

### 4. Verify Key Vault Secrets

The following secrets must be set in Key Vault before the Function App can start:

```bash
az keyvault secret set --vault-name ssl-prod-hsm --name pki-mailbox        --value "pki@example.com"
az keyvault secret set --vault-name ssl-prod-hsm --name pd-approver-email  --value "pd@example.com"
az keyvault secret set --vault-name ssl-prod-hsm --name slack-signing-secret --value "$SLACK_SECRET"
az keyvault secret set --vault-name ssl-prod-hsm --name orchestrator-enabled --value "true"
```

### 5. Smoke Test

```bash
# Verify Function App is healthy
curl https://ssl-prod-func.azurewebsites.net/api/status \
  -H "x-functions-key: $FUNC_KEY"
# Expected: {"status":"healthy","orchestrator_enabled":true}
```

---

## Rollback Procedure

### Function App Code Rollback

```bash
# List deployment slots
az functionapp deployment list --name ssl-prod-func --resource-group ssl-renewal-rg-prod

# Rollback to a previous deployment
az functionapp deployment source config-zip \
  --name ssl-prod-func \
  --resource-group ssl-renewal-rg-prod \
  --src <path-to-previous-zip>
```

### Bicep Rollback

```bash
# Get the previous deployment name
az deployment group list \
  --resource-group ssl-renewal-rg-prod \
  --query "[].name" -o table

# Re-deploy a previous template (export it first)
az deployment group export \
  --resource-group ssl-renewal-rg-prod \
  --name <previous-deployment-name> > previous-template.json

az deployment group create \
  --resource-group ssl-renewal-rg-prod \
  --template-file previous-template.json \
  --name "rollback-$(date +%Y%m%d%H%M%S)"
```

---

## Environment-Specific Config Summary

| Setting | Dev | UAT | Prod |
|---------|-----|-----|------|
| Key Vault | Standard vault | Managed HSM | Managed HSM |
| Cosmos mode | Serverless | Provisioned | Provisioned |
| Private endpoints | No | Yes | Yes |
| Azure Firewall | No | No | Yes |
| Functions SKU | Consumption (Y1) | Elastic Premium EP1 | Elastic Premium EP2 |
| Log retention | 30d | 60d | 7 years (2555d) |
| PKI mailbox | `pki-dev@example.com` | `pki-sandbox@example.com` | `pki@example.com` |
