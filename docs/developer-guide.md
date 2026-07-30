# Developer Guide — Autonomous SSL Certificate Renewal Agent

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| Azure CLI | Latest | `winget install Microsoft.AzureCLI` |
| Azure Functions Core Tools | v4 | `npm install -g azure-functions-core-tools@4` |
| Bicep CLI | Latest | `az bicep install` |
| Git | 2.40+ | [git-scm.com](https://git-scm.com/) |

Optional:
- Docker (for Cosmos DB emulator)
- VS Code + Azure Functions extension

---

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-org/ssl-renewal-agent.git
cd ssl-renewal-agent
```

### 2. Create a Python virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your dev values:

```dotenv
# Azure AI Foundry
FOUNDRY_PROJECT_ENDPOINT=https://your-dev-project.api.azureml.ms

# Azure OpenAI
AZURE_OPENAI_DEPLOYMENT=gpt-4o-2024-11-20

# Azure Key Vault (dev — standard vault, not HSM)
KEY_VAULT_URI=https://your-dev-kv.vault.azure.net

# Cosmos DB
COSMOS_ENDPOINT=https://localhost:8081  # local emulator
COSMOS_DATABASE=ssl_renewal_dev

# Blob Storage
BLOB_ACCOUNT_URL=https://your-dev-storage.blob.core.windows.net

# Optional — leave empty to disable in dev
APPLICATIONINSIGHTS_CONNECTION_STRING=
ORCHESTRATOR_ENABLED=true
LOG_LEVEL=DEBUG
```

### 5. Start the Cosmos DB emulator (optional — tests use in-memory fakes)

```bash
docker run -p 8081:8081 \
  -e AZURE_COSMOS_EMULATOR_PARTITION_COUNT=10 \
  mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator
```

### 6. Run the test suite

```bash
pytest tests/ --ignore=tests/test_e2e -v
```

All 185+ unit tests should pass. E2E tests require real Azure resources and are excluded from local runs.

---

## Running the Functions Locally

```bash
func start
```

This starts the local Functions host and exposes:
- `POST http://localhost:7071/api/orchestrate` — trigger a renewal
- `GET  http://localhost:7071/api/status?workflow_id=wf_001` — query status
- `POST http://localhost:7071/api/approval-callback` — Teams card callback
- `POST http://localhost:7071/api/pki-reply` — PKI reply webhook

---

## Architecture Overview

```
src/
├── config.py                    — All settings (pydantic-settings, env/KV refs)
├── telemetry.py                 — OTel setup + tool_span() helper
├── orchestrator/
│   ├── agent.py                 — build_orchestrator() entry point
│   ├── batch_coordinator.py     — Concurrent batch renewals (semaphore)
│   ├── drift_check.py           — MCP schema-drift fail-closed check (G5)
│   ├── mcp_tools.py             — MCP tool registration
│   ├── prompts.py               — ORCHESTRATOR_SYSTEM_PROMPT
│   ├── rate_limiter.py          — Per-system rate limiters
│   ├── retry_orchestration.py   — Diagnostic sub-orchestration loop
│   └── state_machine.py         — State enum + transition guard
├── tools/
│   ├── approval_tool.py         — HITL gate (G1)
│   ├── errors.py                — Tool error types
│   ├── generate_csr.py          — HSM CSR generation (G6, G7)
│   └── verify_cer.py            — Deterministic X.509 verifier (G2)
├── middleware/
│   ├── audit_middleware.py      — G4: one audit line per tool call
│   └── policy_middleware.py     — G1/G3/G6: guardrail enforcement
├── persistence/
│   ├── blob_repo.py             — WORM CER storage
│   └── cosmos_repo.py           — Workflow state + audit log
└── interfaces/
    ├── backend/                 — Webhook, scheduled scan, callbacks
    ├── direct/                  — Slack, web console
    └── embedded/                — Dashboard read model, suggestions
```

---

## Coding Standards

| Standard | Tool | Config |
|----------|------|--------|
| Formatting | `black` | 88-char line length |
| Linting | `ruff` | `pyproject.toml` |
| Type checking | `mypy` | strict mode, `src/` only |
| Imports | `isort` via ruff | |
| Tests | `pytest` + `pytest-asyncio` | `asyncio_mode=auto` |
| Coverage | `pytest-cov` | ≥ 80% required |

Run all checks:

```bash
ruff check . && mypy src && black --check . && pytest --cov=src --cov-fail-under=80
```

---

## The 8 Guardrails — What They Mean for Code

| Guardrail | Rule for developers |
|-----------|-------------------|
| G1 — No-skip approval | Never merge code that adds a path from `CSR_REQUESTED` to `APPROVED` without calling `request_approval()`. PolicyMiddleware checks this at runtime. |
| G2 — No cert mismatch | `verify_cer()` is deterministic code — never add an override path or `skip_verify` flag. |
| G3 — Halt on 2 errors | `PolicyMiddleware.consecutive_errors` tracks this. Don't catch and swallow exceptions. |
| G4 — Audit every call | `AuditMiddleware` wraps every tool. Never call tools outside the middleware chain. |
| G5 — MCP output is data | Never pass MCP output directly to a `run_python()` or `eval()` call. Treat as untrusted text. |
| G6 — Block wildcards | `_reject_wildcard()` in `generate_csr.py` + `PolicyMiddleware`. Both must stay in place. |
| G7 — Non-exportable keys | `CertificatePolicy(exportable=False, key_type=KeyType.rsa_hsm)`. Never change these. |
| G8 — No secrets in code | Use `settings.*` for all config. Key Vault references for secrets. Never `os.getenv("SECRET_VALUE")` inline. |

---

## Adding a New Native Tool

1. Create `src/tools/your_tool.py` with a function decorated `@tool` (MAF convention).
2. Import and add to `NATIVE_TOOLS` list in `src/orchestrator/agent.py`.
3. Add a new guardrail check in `PolicyMiddleware` if the tool has security-relevant inputs.
4. Add audit summary logic in `AuditMiddleware._summarize()` if the tool returns sensitive data.
5. Write `tests/test_your_tool.py` covering:
   - Happy path
   - Input validation (invalid inputs raise `ToolValidationError`)
   - Transient failure (raises `ToolTransientError`)
   - No private key material in outputs (G7/G8)
6. Add idempotency key support if the tool has external side effects.

---

## Adding a New MCP Server

1. Add the URL to `src/config.py` as a new optional `str | None` field.
2. Instantiate `HostedMcpTool` (Foundry-hosted) or `MCPTool` (APIM-fronted) in `src/orchestrator/mcp_tools.py`.
3. Pin the schema hash in `src/orchestrator/drift_check.py` (`_EXPECTED_SCHEMAS` dict).
4. Add the APIM API definition in `infra/modules/apim.bicep` (if APIM-fronted).
5. Write an integration stub test in `tests/test_orchestrator_wiring.py`.
6. Update `docs/architecture.md` to include the new MCP server in the tool inventory.

---

## Git Workflow

1. Create a feature branch: `git checkout -b feat/your-feature`
2. Write code + tests; ensure `pytest` green and `ruff`/`mypy`/`black` clean.
3. Open a PR against `main`.
4. IBM Bob reviews the PR (security + AC validation).
5. One human approver reviews.
6. Merge — deploy pipeline triggers automatically.

Commit message convention: `feat: add X`, `fix: Y`, `chore: update deps`, `security: harden Z`.

---

## Troubleshooting Local Setup

| Problem | Fix |
|---------|-----|
| `pydantic_core.ValidationError: 4 validation errors for Settings` | Set the 4 required env vars: `FOUNDRY_PROJECT_ENDPOINT`, `KEY_VAULT_URI`, `COSMOS_ENDPOINT`, `BLOB_ACCOUNT_URL` |
| `ModuleNotFoundError: azure.cosmos` | Run `pip install -r requirements.txt` |
| Tests fail with `asyncio fixture` errors | Use `python -m pytest --override-ini="asyncio_mode=auto"` |
| `func: command not found` | `npm install -g azure-functions-core-tools@4` |
| `az login` fails behind corp proxy | Set `HTTPS_PROXY` env var or use device code flow: `az login --use-device-code` |
