# Copilot Studio Topics — SSL Certificate Renewal Agent

> These topic specifications define the conversational flows built in Copilot Studio for the SSL Certificate Renewal Agent. Both topics run over the **one guarded core** — no topic has a privileged path or can bypass PolicyMiddleware, AuditMiddleware, or the HITL gate.

---

## Topic 1 — Approve CSR

**Type:** System / event-triggered (not user-invoked)  
**Trigger:** `approval.request` event emitted by `request_approval` native tool when an orchestrator workflow reaches `CSR_REQUESTED` state and is ready for PD review.  
**Primary actor:** Product Director (David)  
**Surface:** Microsoft Teams / Copilot Chat (DM to PD), with Power Automate Approvals as fallback

---

### Flow Specification

```
[TRIGGER] approval.request event
    ├── event payload: { workflow_id, thread_id, cn, san, owning_application, jira_ticket, jira_url, requested_at, has_anomaly, anomaly_message }
    ▼
[STEP 1] Build approval Adaptive Card 1.5
    ├── Populate template variables from event payload (see copilot/approval-card.json)
    ├── Set has_anomaly and anomaly_message from payload
    └── Set thread_id in card data binding for callback correlation
    ▼
[STEP 2] Post card to PD's Teams chat (DM)
    ├── Use Graph API: POST /v1.0/me/chats/{chatId}/messages with Adaptive Card attachment
    ├── Record card_correlation_id in workflow_state.approval.card_correlation_id
    └── Audit: action = "approval_card_sent", actor = "system", state_before = CSR_REQUESTED
    ▼
[STEP 3] Start 48-hour escalation timer
    ├── If no callback received within 48h: fire escalation notification to PD's delegate
    ├── Escalation is a reminder — it does NOT auto-approve or auto-reject
    └── Audit: action = "approval_escalation_sent", actor = "system"
    ▼
[STEP 4] Wait for POST /api/approval-callback
    ├── Callback body: { thread_id, decision, approver, reasoning }
    ├── Server validates: Entra identity of submitter, thread_id matches workflow, MFA asserted
    ├── Mismatched thread_id → 401 rejected; audit: action = "approval_callback_rejected"
    └── Valid callback → record_approval_decision called
    ▼
[STEP 5a — APPROVED]
    ├── workflow_state.approval = { decision: APPROVED, approver, reasoning, decided_at }
    ├── State transition: CSR_REQUESTED → APPROVED (state machine validates)
    ├── Audit: action = "approval_decision", state_after = APPROVED
    └── Workflow unblocks; PKI email step begins
    ▼
[STEP 5b — REJECTED]
    ├── workflow_state.approval = { decision: REJECTED, approver, reasoning, decided_at }
    ├── State transition: CSR_REQUESTED → REJECTED (terminal)
    ├── Audit: action = "approval_decision", state_after = REJECTED
    ├── Jira ticket transitioned to Closed with rejection comment
    ├── Requester (Priya) notified via Teams proactive message
    └── Completion card posted (state = REJECTED) with reasoning
```

---

### Power Automate Fallback

When Teams card delivery fails or PD prefers email-based approval:

1. Power Automate Approval workflow is triggered with the same facts (CN, SAN, owner, Jira link).
2. PD receives an approval email with Approve / Reject buttons.
3. The Power Automate flow calls the same `POST /api/approval-callback` endpoint with the same body shape.
4. **Identical records are written to `workflow_state` and `audit_log`** — no divergence between Teams and PA paths.
5. Both paths are tested in the integration test suite.

---

### Acceptance Criteria

| # | Criterion |
|---|-----------|
| AC-T1.1 | Card is posted to PD within 60s of `approval.request` event |
| AC-T1.2 | Card displays CN, full SAN list, owning application, Jira link, requested-at before any decision |
| AC-T1.3 | Approve action transitions state to APPROVED and records approver Entra email + reasoning |
| AC-T1.4 | Reject action transitions state to REJECTED, closes Jira, notifies requester, records reasoning |
| AC-T1.5 | Approval callback with mismatched thread_id is rejected with 401 |
| AC-T1.6 | After 48h with no response, escalation is sent to delegate — workflow remains in CSR_REQUESTED, not auto-approved |
| AC-T1.7 | Power Automate fallback writes the identical Cosmos record as the Teams path |

---

## Topic 2 — Check Status

**Type:** User-invoked, generative  
**Trigger:** User asks a natural-language question about a renewal status in Teams/Copilot chat  
**Primary actors:** Priya (SSL/Platform), Sam (SRE/on-call), any authenticated user with View role  
**Surface:** Microsoft Teams / Copilot Chat

---

### Trigger Phrases (representative — not exhaustive; NLU handles variations)

- "Where is the renewal for api.prod.example.com?"
- "What's the status of SSL-4821?"
- "Check renewal wf_2026-07-28_api.prod.example.com_7f3a"
- "Is the cert for auth.prod.example.com done?"
- "Show me batch batch_2026-07-28_wave_ca-rotation_4a1c"

---

### Flow Specification

```
[TRIGGER] User message in Teams/Copilot chat
    ▼
[STEP 1] Intent recognition (NLU / generative orchestration)
    ├── Extract: lookup_key (cn | workflow_id | batch_id | jira_ticket)
    ├── Classify: single workflow OR batch
    └── If no lookup key found: prompt user "Please provide a CN, workflow ID, or Jira ticket number"
    ▼
[STEP 2] Map to GET /api/status action
    ├── Single: GET /api/v1/status?cn={cn} OR GET /api/v1/workflows/{workflow_id}
    └── Batch:  GET /api/v1/batches/{batch_id}
    ▼
[STEP 3] Fetch workflow_state (read-only, no state mutation)
    ├── Auth: Entra SSO of querying user; View role check
    ├── Returns: { state, cn, san, owning_application, jira_ticket, chg_number, updated_at, timeline }
    └── Sensitive fields (CSR SHA256, CER Blob URL without SAS) are redacted if user lacks Operator role
    ▼
[STEP 4] Format and return response (generative, factual)
    ├── Single workflow: "Renewal for api.prod.example.com is currently APPROVED (updated 5 minutes ago). 
    │   PKI email sent; waiting for PKI reply. Jira: SSL-4821. Workflow ID: wf_…"
    ├── Batch: "Batch batch_2026-07-28… has 100 certs: 87 COMPLETE, 8 APPROVED (awaiting PKI), 
    │   3 FAILED, 2 REJECTED."
    └── Terminal + complete: include link to CHG and CER Blob (SAS-scoped short TTL)
    ▼
[STEP 5] Post response as Teams message (text + optional Adaptive Card summary for batches)
```

---

### Generative Orchestration Notes

- The orchestrator's `get_status` action is a **read-only** operation; it calls `GET /api/status` and formats the result.
- The model is **not** allowed to take action from a status query (no tool calls to `generate_csr`, `request_approval`, etc.).
- All output is factual and sourced from `workflow_state` — the model does not invent timeline entries.
- PHI and secrets are never included in the response; the model's redaction instruction is reinforced by the API's field-level access control.

---

### Acceptance Criteria

| # | Criterion |
|---|-----------|
| AC-T2.1 | A valid CN query returns state, last-updated, and deep links within 5 seconds |
| AC-T2.2 | An invalid CN or unknown workflow_id returns a clear "not found" message, not an error stack |
| AC-T2.3 | The response includes no private key material, bearer tokens, or CSR/CER bytes |
| AC-T2.4 | A batch query returns counts by state (COMPLETE, FAILED, in-flight) and the batch_id |
| AC-T2.5 | A user without View role receives a 403 — the bot reports "you do not have permission to view this renewal" |
| AC-T2.6 | The status query does not transition any workflow state |

---

## Common Design Rules (both topics)

1. **One guarded core.** Both topics call the same API endpoints that every other mode uses. Copilot Studio has no special permissions or direct Cosmos access.
2. **Identity in audit.** Every action taken via Copilot is recorded with `actor = user.email` (Teams SSO identity).
3. **Fail closed.** If the status API or approval callback is unavailable, the bot returns "Unable to fetch status — please try again or contact the SSL team" — it never returns stale/cached state as current.
4. **No secrets in bot output.** System prompt explicitly forbids placing Key Vault IDs, SAS URLs (except the short-TTL completion card links), or any credential in the response.
