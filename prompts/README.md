# SSL Renewal Agent — Layered Prompt System for Bob

This folder contains the **layered build prompts** for the Autonomous SSL Certificate Renewal Agent. The master specification lives in [main-prompt.md](main-prompt.md); these files decompose it into digestible, actionable units.

---

## How to Use These Prompts

1. **Start with [00-bob-briefing.md](00-bob-briefing.md).** This explains who Bob is, what the system does, the golden rules, and how to navigate the phases. Give this to Bob first.

2. **Then read [00-context.md](00-context.md).** It contains the 8 guardrails, state machine, repo layout, and coding standards that apply to every phase. Do not skip it.

3. **Execute phases in order.** Each phase builds on the previous. The critical path is:

   ```
   01 → 02 → 03 → 04 → 05 → 06 → 07 → 08a → 08b → 08c → 08d → 08e
   → 09 → 10 → 11 → 12 → 13 → 14 → 15
   (16 runs in parallel with 10 onwards — dev plane setup)
   ```

4. **Each prompt is self-contained.** It tells you exactly what to produce, what "done" looks like (AC), and how to verify it. You do not need to re-read main-prompt.md for that phase.

5. **Never violate the 8 guardrails** from `00-context.md`. They apply everywhere, even if a phase prompt doesn't repeat them.

---

## Phase Index

| File | Phase | What you build | Task IDs |
|------|-------|----------------|----------|
| [00-context.md](00-context.md) | Reference | Guardrails, state machine, stack, coding standards | — |
| [01-business-analysis.md](01-business-analysis.md) | P1 | BRD, FRs/NFRs, personas, KPIs, user stories | — |
| [02-ux-design.md](02-ux-design.md) | P2 | Adaptive Cards, Copilot topics, 3-mode UX | — |
| [03-system-architecture.md](03-system-architecture.md) | P3 | Component/sequence/deployment diagrams, ADRs | — |
| [04-technology-selection.md](04-technology-selection.md) | P4 | ADRs, version pinning, tech mapping | — |
| [05-data-design.md](05-data-design.md) | P5 | Cosmos schemas, hash chain, idempotency, Blob WORM | T11 |
| [06-security-engineering.md](06-security-engineering.md) | P6 | STRIDE, OWASP, middleware, trust boundary | T03, T04, T14 |
| [07-api-tool-design.md](07-api-tool-design.md) | P7 | Native tool contracts, MCP inventory, OpenAPI | T05, T06, T07, T08 |
| [08a-development-scaffold.md](08a-development-scaffold.md) | P8 | `config.py`, `agent.py`, `mcp_tools.py`, `prompts.py` | T01, T08, T09 |
| [08b-development-middleware.md](08b-development-middleware.md) | P8 | `policy_middleware.py`, `audit_middleware.py` | T03, T04 |
| [08c-development-tools.md](08c-development-tools.md) | P8 | `generate_csr.py`, `verify_cer.py`, `approval_tool.py` | T05, T06, T07 |
| [08d-development-batch.md](08d-development-batch.md) | P8 | `batch_coordinator.py`, `rate_limiter.py`, `state_machine.py` | T02, T19 |
| [08e-development-interfaces.md](08e-development-interfaces.md) | P8 | `interfaces/{direct,embedded,backend}/` + Function hosts | T12, T20 |
| [09-testing.md](09-testing.md) | P9 | Full test suite, PromptFlow evals, E2E synthetic | T15 |
| [10-devops.md](10-devops.md) | P10 | `deploy.yml`, `bob-review.yml`, branch/env config | T16 |
| [11-cloud-deployment.md](11-cloud-deployment.md) | P11 | Bicep modules, rollout order, networking | T16 |
| [12-observability.md](12-observability.md) | P12 | OTel tracing, dashboards, alerting, Purview | T17 |
| [13-performance.md](13-performance.md) | P13 | Idempotency, SLOs, load test, timers | T13, T19 |
| [14-documentation.md](14-documentation.md) | P14 | All `docs/` files, ADRs, RUNBOOK, DR | T17 |
| [15-go-live.md](15-go-live.md) | P15 | Readiness checklists, Go/No-Go, canary cutover | T17 |
| [16-dev-plane-bob.md](16-dev-plane-bob.md) | P-Bob | Bob agents, APIM denial, PR gate | T18 |

---

## Definition of Done (whole system)

- All tasks T01–T20 pass their acceptance criteria and verification steps.
- Part VI Critical Review yields **Approved** or **Approved with conditions**.
- Appendix A deliverables checklist is 100% complete.
- All 8 guardrails enforced in code (not just comments).
- ≥ 80% test coverage; all security tests green; E2E 20/20 correct terminal states.
