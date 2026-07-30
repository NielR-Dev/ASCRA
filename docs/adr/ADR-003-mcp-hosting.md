# ADR-003 — MCP Hosting: Foundry-Hosted + APIM-Fronted Hybrid

> **Status:** Accepted  
> **Date:** 2026-07-28  
> **Decision makers:** Architecture team, IBM Bob

---

## Context

The Orchestrator needs to call five external systems via tools: Microsoft Graph (email), ServiceNow (change tickets), Azure (resource operations), Jira (ticket management), and Dynatrace (alert details). These are reached via the **Model Context Protocol (MCP)**.

Hosting decisions for MCP servers involve:
1. **Where the MCP server runs** (Foundry-managed vs. self-hosted vs. SaaS-hosted).
2. **How authentication is handled** (Managed Identity, API key, OAuth).
3. **Who is responsible for security controls** (throttling, JWT validation, request/response logging).
4. **Schema drift risk** — MCP server schemas evolving without coordinated updates to the orchestrator.

Additionally, three tools — `generate_csr`, `verify_cer`, `request_approval` — handle private keys, HITL approval, and deterministic verification. These must **not** be MCP surfaces; they must run as native in-process tools.

---

## Decision

**Hybrid approach:**

| Category | Tools | Hosting | Auth | Justification |
|----------|-------|---------|------|--------------|
| **Foundry-hosted** (`HostedMcpTool`) | `graph_mail`, `servicenow`, `azure` | Azure AI Foundry managed runtime | Managed Identity + platform-level scopes | No self-hosted infra, no VNet complexity, no separate SLA to manage; Foundry handles deployment + health |
| **External / APIM-fronted** (`MCPTool`) | `atlassian` (Jira), `dynatrace` | SaaS; reached through Azure API Management (MCP mode) | Entra JWT validated at APIM | APIM adds JWT validation, per-subscription throttling, full request/response audit logging, and FQDN allow-list enforcement for SaaS egress |
| **Native MAF tools** (`@tool`) | `generate_csr`, `verify_cer`, `request_approval`, `record_approval_decision` | In-process (Azure Functions / MAF runtime) | N/A — runs in the same process as the orchestrator | Private-key handling (G7), HITL gate (G1), deterministic verdict (G2) must not cross a network boundary or MCP trust surface |

**Schema-drift control (G5):** both Foundry-hosted and external MCP servers have their schemas pinned at deploy time. A **fail-closed start-up drift check** compares the live MCP schema against the pinned version; a mismatch halts the orchestrator instead of silently proceeding with a wrong schema.

---

## Alternatives Considered

### Alternative 1: Self-hosted MCP servers (on Azure Container Apps)

**Rejected.** Self-hosting all five MCP servers would require:
- Dedicated Container Apps for each server (infra, scaling, patching, SLA).
- Custom auth/throttling logic per server.
- Duplicates what Foundry already provides for the Microsoft-stack tools.

Self-hosting makes sense for a corporate Jira/Dynatrace server that is not reachable via SaaS APIM — but in this architecture, APIM (MCP mode) already provides the security envelope for SaaS tools.

### Alternative 2: Bespoke SDK calls (no MCP)

**Rejected.** Direct Azure SDK / REST calls to each system would work but:
- No uniform tool contract — each call has its own auth, retry, and error-handling logic.
- No schema the orchestrator can reason about.
- Cannot leverage MAF's `HostedMcpTool` / `MCPTool` abstraction, which allows new tools to be added without changing the orchestrator.
- No APIM governance layer for external SaaS.

### Alternative 3: All tools as MCP (including `generate_csr`, `verify_cer`, `request_approval`)

**Rejected.** Moving security-sensitive tools to MCP would introduce:
- A network hop for private-key operations — violates G7 (keys should never leave the HSM context to cross a network boundary before the CSR is returned).
- The approval callback could be intercepted or replayed over MCP — the `record_approval_decision` tool must validate Entra identity and `thread_id` in-process.
- The `verify_cer` verdict would be an MCP response — a piece of untrusted data the orchestrator might be manipulated to override (G2, G5). As a native tool, the verdict is a typed Python return value, not a text string.

**The separation of native vs. MCP tools is a security control, not just architecture tidiness.**

### Alternative 4: Foundry-hosted MCPs for all five tools (no APIM)

**Considered but rejected for Jira and Dynatrace.** Jira and Dynatrace are external SaaS systems not natively integrated in the Foundry MCP catalog as first-party servers. Using APIM (MCP mode) for these provides:
- Entra JWT validation (no API keys in code — G8).
- Request/response logging at the API layer (independent audit channel).
- Throttling at APIM before hitting Jira/Dynatrace quotas.
- FQDN allow-list enforcement via the spoke network + Azure Firewall egress.

---

## Consequences

| Consequence | Mitigation |
|------------|-----------|
| Schema drift on Foundry-hosted MCPs can silently break tool calls | Fail-closed start-up drift check; pinned schemas in `infra/`; integration contract tests per server (P9.3) |
| APIM becomes a dependency for Jira and Dynatrace calls | APIM Premium SKU with zone redundancy; bounded retry in PolicyMiddleware (G3) on 5xx; degrade to manual for those steps if APIM is down |
| Five different tools = five integration test surface areas | Per-server integration test suite (P9.3); `test_mcp_schema_{server}` tests run in CI |
| Foundry MCP server availability tied to Foundry SLA | Foundry managed runtime; zone-redundant; same SPOF mitigations as Orchestrator |

**Confidence:** High. The hybrid model (Foundry-hosted for Microsoft SaaS + APIM-fronted for third-party SaaS + native for security-sensitive) maps cleanly to each tool's trust and operational requirements.
