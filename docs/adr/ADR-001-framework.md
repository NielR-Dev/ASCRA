# ADR-001 — Agent Framework: Microsoft Agent Framework (MAF) 1.0

> **Status:** Accepted  
> **Date:** 2026-07-28  
> **Decision makers:** Architecture team, IBM Bob  
> **Supersedes:** N/A (greenfield)

---

## Context

The SSL Certificate Renewal Agent requires an agent framework that can:

1. Support a **Supervisor–Worker** pattern (one orchestrator directing specialist tool workers).
2. Provide **first-class MCP (Model Context Protocol) integration** for both hosted and external SaaS tools.
3. Support **middleware injection** at the tool-call level — required for PolicyMiddleware (G1–G3, G6) and AuditMiddleware (G4).
4. Work natively with **Azure AI Foundry Agent Service** for managed thread management and tracing.
5. Be **GA-stable** (not research-grade) for a healthcare-grade production system.
6. Support **managed identity + Azure SDK** patterns for Key Vault, Cosmos DB, and Azure Functions.

The framework choice drives the entire P8 development effort and sets the pattern for sibling agents (code-signing, mTLS, wildcard).

---

## Decision

**Chosen: Microsoft Agent Framework (MAF) 1.0** (GA: April 2026).

MAF 1.0 unifies Semantic Kernel (SK) and AutoGen under a single, Microsoft-supported API. It provides:

- `ChatAgent` with `tools` + `middleware` constructor parameters — exactly the pattern needed for `[PolicyMiddleware, AuditMiddleware]` wired in order.
- `HostedMcpTool` and `MCPTool` for the hybrid MCP surface.
- `@tool` decorator for native tools (`generate_csr`, `verify_cer`, `request_approval`).
- First-party integration with Azure AI Foundry Agent Service (managed threads, tracing, hosted MCP servers).
- **1-year Microsoft support window** from GA (April 2026 → April 2027), with Semantic Kernel as the fallback path during that window.

---

## Alternatives Considered

### Alternative 1: LangGraph (LangChain)

**Rejected.** LangGraph provides strong graph-based workflow control, but:
- **Weak Azure/Entra integration** — connecting to Azure AI Foundry, Key Vault, and Cosmos requires additional custom adapters; no native HostedMcpTool equivalent.
- **No GA enterprise support** from Microsoft — support complexity for a healthcare system is unacceptable.
- No native middleware injection at the tool-call level (would require monkey-patching or wrapping).

### Alternative 2: Raw AutoGen (Microsoft Research)

**Rejected.** AutoGen is the research-grade precursor to MAF. Specifically:
- AutoGen 0.x is **research-grade** — no GA SLA, API instability between releases.
- MAF 1.0 is the production successor; using raw AutoGen would require a future migration anyway.
- No native `HostedMcpTool` integration with Azure AI Foundry.

### Alternative 3: Semantic Kernel (SK) alone

**Conditionally acceptable as fallback.** SK is the more mature codebase subsumed into MAF 1.0. If MAF 1.0 encounters critical issues in the first support year:
- SK 1.x remains supported in parallel and uses the same middleware + tool patterns.
- The code is structured with **lazy imports** (`from agent_framework import ...`) so the import path is the only change needed for an SK fallback.
- **This is the documented fallback** if MAF 1.0 GA date slips or introduces critical regressions.

### Alternative 4: Durable Functions (deterministic orchestration only)

**Partially adopted, not as the primary framework.** Durable Functions are used for the **Batch Coordinator** (durability + resume-on-restart) but not as the primary agent framework. The main orchestrator needs LLM reasoning for the retry/diagnosis branch (FR-10) — something a pure Durable Functions approach cannot provide without separate LLM calls and no tool-registry abstraction.

---

## Consequences

| Consequence | Mitigation |
|------------|-----------|
| MAF 1.0 is a new framework (GA Apr 2026); API surface may have edge cases | Pin `==1.0.*`; Bob's Modernisation agent tracks minor releases; lazy imports isolate the framework surface |
| Vendor lock-in to Microsoft's framework evolution | MCP + Clean Architecture keep worker logic (tools) portable; a framework swap touches only `agent.py` and `mcp_tools.py` |
| First-mover risk: limited community examples at GA | Internal patterns documented in ADRs + developer guide; Bob Code-Gen provides code templates |
| SK fallback window is 1 year | Decision to migrate or stay is revisited at April 2027 |

**Confidence:** High. MAF 1.0 is the natural evolution of the SK/AutoGen line Microsoft has invested in for enterprise Azure scenarios; the middleware and MCP integration story is uniquely fit for this guardrail-heavy design.
