# Phase 14 — Documentation

> **Pre-read:** [00-context.md](00-context.md) · depends on all phases
> **Deliverable:** All docs/ files — architecture, developer guide, deployment, RUNBOOK, DR, compliance
> **Task IDs:** T17
> **Effort estimate:** ~4 person-days

---

## Your Task

Write all required documentation so that a new engineer can set up, test, deploy, and operate the system from docs alone — and an auditor can reconstruct any renewal months later.

---

## What to Produce

```
docs/
├── architecture.md          # System design, component diagram, ADR references
├── developer-guide.md       # Local setup, coding standards, how to add tools/MCP
├── deployment-guide.md      # Bicep modules, rollout order, OIDC setup, env config
├── RUNBOOK.md               # Operate, monitor, kill-switch, alert responses, escalation contacts
├── dr-guide.md              # RPO/RTO, DR region, Cosmos PITR restore, Key Vault recovery
├── troubleshooting.md       # Stuck workflow, verifier failures, MCP drift, PKI delays
├── compliance.md            # HIPAA/ISO mapping, audit reconstruction procedure, 7-yr WORM
└── adr/
    ├── ADR-001-framework.md
    ├── ADR-002-model.md
    ├── ADR-003-mcp-hosting.md
    └── ADR-004-state-store.md
```

---

## `architecture.md` — Required Contents

- System overview (2–3 paragraphs)
- Component diagram (reuse the textual one from P3)
- End-to-end sequence T0–T8
- Batch topology diagram
- Adapter layer diagram (three modes → one guarded core)
- Run plane vs dev plane boundary
- Links to all four ADRs
- Single Points of Failure table with mitigations
- Trust boundary diagram

---

## `developer-guide.md` — Required Contents

```markdown
## Prerequisites
- Python 3.11+
- Azure CLI + `az login`
- Azure Functions Core Tools v4
- Access to dev Key Vault, Cosmos emulator

## Local Setup
1. Clone the repo
2. `pip install -r requirements.txt -r requirements-dev.txt`
3. Copy `.env.example` → `.env`; fill in dev values
4. Run the Cosmos emulator: `docker run -p 8081:8081 mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator`
5. `pytest tests/ --ignore=tests/test_e2e -v` — all tests should pass

## Running the Functions Locally
`func start` from the project root

## Adding a New Native Tool
1. Create `src/tools/your_tool.py` with `@tool` decorator
2. Add to `NATIVE_TOOLS` in `agent.py`
3. Write tests in `tests/test_your_tool.py`
4. If the tool has a side effect, add an idempotency key

## Adding a New MCP Server
1. Add URL to `src/config.py`
2. Instantiate `HostedMcpTool` or `MCPTool` in `mcp_tools.py`
3. Pin the schema hash in `drift_check.py`
4. Write an integration test in `tests/test_integration/`

## Coding Standards
[link to 00-context.md coding standards section]
```

---

## `RUNBOOK.md` — Required Sections

This is the document SRE reads when something goes wrong. Be specific.

```markdown
## Kill-Switch Procedure
To disable the agent without stopping the Function App:
1. Set `AGENT_ENABLED=false` in the Function App configuration (Portal or CLI):
   `az functionapp config appsettings set --name ssl-renewal-func-prod -g ssl-renewal-rg-prod --settings AGENT_ENABLED=false`
2. In-flight workflows continue; new triggers return 503.
3. Monitor Cosmos `workflow_state` for stuck workflows.
4. To re-enable: set `AGENT_ENABLED=true`.

## Alert: Stuck Workflow (> 24h)
1. Look up workflow_id in App Insights: search `workflow_id = "wf_..."` 
2. Check Cosmos `workflow_state`: what state is it stuck in?
3. State-specific recovery:
   - `CSR_REQUESTED`: Check Jira for the ticket. Verify PD received the approval card.
   - `APPROVED` (PKI wait): Check PKI mailbox. Send a reminder if 24h has passed.
   - `PKI_REPLIED` (verification): Check `verification.pass` and `cer_blob_url` in Cosmos.

## Alert: Verifier Failure
1. Check `verify_cer` output in App Insights: which check failed (cn_match/san_match/expiry)?
2. If CN/SAN mismatch: contact PKI team (Mei) with original CSR + received CER.
3. If chain issue: check if the issuing CA cert changed.
4. Magentic retry will fire automatically. If it escalates to PD, notify PD to review.

## Manual Fallback Runbook
[If the agent is unavailable, follow these manual steps to renew a certificate...]
```

---

## `dr-guide.md` — Required Contents

- **RPO:** < 1 hour (Cosmos PITR, 30-day restore window; Blob versioning)
- **RTO:** < 4 hours (to restore state; in-flight workflows require manual completion)
- DR region: [specify secondary Azure region]
- Cosmos PITR restore procedure: step-by-step CLI commands
- Key Vault recovery: `az keyvault hsm recover` after soft-delete
- Function App re-deployment: `func azure functionapp publish` from last known-good tag
- 30-day manual fallback: the manual runbook stays authoritative for 30 days post-cutover

---

## `compliance.md` — Required Contents

- HIPAA/HITECH mapping: which controls this system satisfies
- ISO 27001 mapping: relevant controls
- Audit reconstruction procedure: step-by-step query to reconstruct any renewal from `audit_log`
- Evidence that the hash chain is unbroken (how to verify `hash_self` for any workflow)
- 7-year WORM retention: how to demonstrate it (Azure Blob immutability policy)
- HITL gate evidence: how to show PD approval was recorded (Entra identity + MFA claim)
- Data residency: all data stays in [specific Azure region]

### Audit Reconstruction Query (include in compliance.md)

```python
# Reconstruct a complete renewal for auditor review
async def reconstruct_renewal(workflow_id: str) -> dict:
    """Return the ordered sequence of audit events + verify hash chain integrity."""
    events = await cosmos_repo.get_audit_events(workflow_id, order_by="seq")
    verified = verify_hash_chain(events)
    state_doc = await cosmos_repo.get_workflow_state(workflow_id)
    return {
        "workflow_id": workflow_id,
        "final_state": state_doc["state"],
        "events": events,
        "hash_chain_valid": verified,
    }
```

---

## Acceptance Criteria

- A new engineer can follow `developer-guide.md` and run all tests locally within 30 minutes
- `RUNBOOK.md` kill-switch procedure is tested in UAT before go-live
- An auditor can reconstruct any renewal using `compliance.md` + the reconstruction query
- All ADRs exist, are peer-reviewed, and linked from `architecture.md`

---

## Verification

- Dry-run onboarding: a team member who didn't write the code follows `developer-guide.md` and gets a passing test run
- Kill-switch: set `AGENT_ENABLED=false` in UAT; confirm 503 response; reset
- Compliance audit reconstruction: run the reconstruction query on a known UAT `workflow_id` and verify the output matches the actual renewal sequence
