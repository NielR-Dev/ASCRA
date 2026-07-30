# Phase 4 — Technology Selection

> **Pre-read:** [00-context.md](00-context.md) · depends on P3 output
> **Deliverable:** ADR files, pinned `pyproject.toml` / `requirements.txt`
> **Effort estimate:** ~2–3 person-days

---

## Your Task

Document the technology decisions with rationale and pin all dependency versions. This gives every developer and Bob a definitive, reproducible baseline.

---

## What to Produce

1. **`pyproject.toml`** — Python 3.11+ project; pinned dependencies
2. **`requirements.txt`** + **`requirements-dev.txt`** — locked versions
3. **`docs/adr/ADR-001-framework.md`** through **`ADR-004-state-store.md`** (if not already created in P3)

---

## Technology Decisions (document each in an ADR)

### Agent Framework — MAF 1.0

**Chosen:** Microsoft Agent Framework (MAF) 1.0 (GA Apr 2026)

- Unifies Semantic Kernel + AutoGen
- First-class MCP, middleware, workflows
- Microsoft-supported; 1-yr support window

**Rejected alternatives:**
- LangGraph — weaker Azure/Entra integration
- Raw AutoGen — research-grade, no GA support

### LLM — Azure OpenAI GPT-4o

**Chosen:** `gpt-4o-2024-11-20` for orchestration/retry reasoning

- Strong tool-calling + reasoning for the retry branch
- Azure data-residency + content safety

**Cost optimization note:** consider `gpt-4o-mini` for status summaries only — never for the verifier (verifier is deterministic code, not the model).

**Rejected:** gpt-4o-mini everywhere — weaker tool reliability for orchestration.

### MCP Hosting — Foundry-hosted + APIM-fronted

**Chosen:** Hybrid approach
- `HostedMcpTool` for `graph_mail`, `servicenow`, `azure` — no self-hosted infra, no VNet plumbing
- `MCPTool` via APIM for `dynatrace`, `jira` — JWT validation, throttling, full logging

**Rejected:** bespoke SDK calls — no uniform governance, harder to add tools.

**Risk:** schema-drift on hosted MCPs → fail-closed drift check at start-up (G5/P6).

### State Store — Cosmos DB (NoSQL)

**Chosen:** Azure Cosmos DB NoSQL

- Low-latency autoscale for bursty expiry waves
- TTL support for idempotency container
- PITR (continuous backup) for recovery
- Schema flexibility for evolving audit payloads

**Rejected:** SQL — schema rigidity would fight the evolving audit payload; no built-in legal hold.

---

## Language & Version Baseline

```toml
# pyproject.toml (excerpt — pin exact versions)
[project]
name = "ssl-renewal-agent"
requires-python = ">=3.11"
dependencies = [
    "agent-framework==1.0.*",          # MAF 1.0
    "azure-identity>=1.16,<2",
    "azure-keyvault-certificates>=4.8,<5",
    "azure-keyvault-keys>=4.9,<5",
    "azure-cosmos>=4.7,<5",
    "azure-storage-blob>=12.21,<13",
    "azure-servicebus>=7.12,<8",
    "azure-functions>=1.20,<2",
    "cryptography>=43,<44",
    "pydantic>=2.7,<3",
    "opentelemetry-api>=1.25,<2",
    "opentelemetry-sdk>=1.25,<2",
    "opentelemetry-exporter-otlp>=1.25,<2",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2,<9",
    "pytest-asyncio>=0.23,<1",
    "pytest-cov>=5,<6",
    "respx>=0.21,<1",
    "moto[s3]>=5,<6",
    "ruff>=0.4,<1",
    "mypy>=1.10,<2",
    "black>=24,<25",
    "pip-audit>=2.7,<3",
]
```

---

## Critical Notes

1. **Pin exact versions in CI.** Use `pip-audit` in every CI run — fail the pipeline on known CVEs.
2. **MAF version risk.** MAF 1.0 GA'd Apr 2026. Pin `==1.0.*` and track minor releases via Bob's Modernisation agent.
3. **Vendor lock-in mitigation.** MCP + Clean Architecture keep worker logic portable. MAF can self-host on Container Apps if Foundry becomes unavailable.
4. **No `gpt-4o` in the verifier.** The `verify_cer` tool is pure Python + `cryptography` library — no LLM involved.

---

## Acceptance Criteria

- Every technology choice above has a written ADR with at least one rejected alternative
- `pyproject.toml` pins all versions; `pip install` resolves in CI without conflicts
- `pip-audit` runs clean on the pinned set

---

## Verification

```bash
# In CI (validate.yml step):
pip install -r requirements.txt -r requirements-dev.txt
pip-audit
python -c "import agent_framework; import azure.keyvault.certificates; import cryptography"
```

- All four ADR files exist and are peer-reviewed before P8 begins
- `pip-audit` reports no known CVEs; if there are, remediate or document waiver
