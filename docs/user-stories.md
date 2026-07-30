# User Stories — Autonomous SSL Certificate Renewal Agent

> All user stories are derived from the functional requirements FR-1 through FR-15.  
> Acceptance criteria are testable and map directly to the Phase 9 test suite.  
> Five personas: **Priya** (SSL/Platform), **David** (PD/Approver), **Mei** (PKI Operator), **Sam** (SRE), **Aisha** (Compliance Auditor).

---

## Epic 1: Autonomous Alert Ingestion and CSR Generation

### US-1 — Automated alert pickup (FR-1, FR-2)

**Persona:** Priya (SSL/Platform Engineer)

> *As Priya, when a certificate nears expiry, the system automatically starts a renewal so I don't have to.*

**Acceptance Criteria:**

- **AC-1.1:** Given a Dynatrace SSL-expiry webhook containing a hostname, when ingested via Event Grid → Service Bus → Orchestrator, then a `RenewalRequest` with extracted `cn`, `san` list, and `owning_application` is created and state = `PARSED`.
- **AC-1.2:** Non-SSL Dynatrace alerts are silently ignored; no workflow is created.
- **AC-1.3:** If CMDB cannot resolve the owning application, the workflow transitions to `FAILED` and PD/on-call is notified — the system never guesses.
- **AC-1.4:** Duplicate alerts for the same CN within an active workflow are de-duplicated; no second workflow is created.
- **AC-1.5:** The alert ingestion event is recorded in the `audit_log` with actor = "system", action = "alert_ingest", state_before = `ALERT_RECEIVED`, state_after = `PARSED`.

---

### US-2 — Secure CSR generation and Jira tracking (FR-3, FR-4)

**Persona:** Priya

> *As Priya, the CSR is generated safely and tracked in Jira so I always know where the renewal stands.*

**Acceptance Criteria:**

- **AC-2.1:** The private key is generated inside Azure Key Vault (Managed HSM) with `exportable=False` and key type `rsa_hsm` — confirmed by the Key Vault key policy.
- **AC-2.2:** The PKCS#10 CSR is produced by Key Vault; only the CSR PEM (not the private key) is stored or transmitted.
- **AC-2.3:** A Jira ticket is created in the SSL project with the CSR attached, the ticket ID is recorded in `workflow_state.csr.jira_ticket`, and the SG counterpart is notified.
- **AC-2.4:** State transitions to `CSR_REQUESTED`; the `audit_log` records the tool call with input/output summaries (no private key bytes).
- **AC-2.5:** `generate_csr` is idempotent on `workflow_id` — a second call returns the existing key/CSR without creating a new Key Vault key or Jira ticket.
- **AC-2.6:** A CSR request containing a wildcard CN or SAN (`*.example.com`) is rejected immediately with a `PolicyViolation` error; no key is created; the request is routed to CAB (G6).

---

## Epic 2: Human-in-the-Loop Approval

### US-3 — PD approval via Teams Adaptive Card (FR-5)

**Persona:** David (Product Director)

> *As David, I can approve or reject a CSR renewal with full context in one Teams card, without needing to open any other system.*

**Acceptance Criteria:**

- **AC-3.1:** An Adaptive Card (v1.5) is posted to David's Teams channel containing: hostname (CN), full SAN list, owning application, Jira ticket link, and requested-at timestamp.
- **AC-3.2:** The card offers two clearly labelled actions: **Approve** and **Reject** (with optional reasoning text field for rejection).
- **AC-3.3:** On Approve: `workflow_state.approval.decision` = `APPROVED`; state = `APPROVED`; audit record written with `approver` (Entra email), `reasoning`, and `decided_at`.
- **AC-3.4:** On Reject: state = `REJECTED` (terminal); requester is notified; Jira ticket is transitioned to Closed; audit record written. Workflow ends here.
- **AC-3.5:** The approval callback validates the Entra identity of the approver, the `thread_id` binding, and MFA — a mismatched `thread_id` or unauthenticated callback is rejected (401).
- **AC-3.6:** The system never auto-approves. After 48 hours with no response, an escalation notification is sent to the PD's configured delegate — this does not auto-approve; it is a reminder.
- **AC-3.7:** The workflow is blocked at `CSR_REQUESTED` until a decision is recorded. No PKI email is sent before an `APPROVED` decision exists in `workflow_state`.

---

### US-3b — Batch approval for expiry waves (FR-14)

**Persona:** David

> *As David, when 50 certs expire together I get one batch summary card, not 50 individual cards.*

**Acceptance Criteria:**

- **AC-3b.1:** A batch summary Adaptive Card (v1.5) is posted containing: batch ID, total cert count, distinct owning applications, any flagged anomalies (with text labels, not color alone).
- **AC-3b.2:** The card allows per-certificate Approve/Reject toggles and Approve All / Reject All bulk actions.
- **AC-3b.3:** Each per-certificate decision is independently recorded in that child's `audit_log` (G1 preserved per child).
- **AC-3b.4:** A certificate not explicitly acted on defaults to **pending** — it is never auto-approved.
- **AC-3b.5:** The batch approval callback carries `{ batch_id, decisions: [{workflow_id, decision, reasoning}], approver }`.

---

## Epic 3: PKI Submission and Certificate Verification

### US-4 — PKI email dispatch (FR-6)

**Persona:** Mei (PKI Operator)

> *As Mei, I receive a correctly formatted CSR Request Form email with the exact CN/SAN I need, so I can sign without clarification.*

**Acceptance Criteria:**

- **AC-4.1:** On `APPROVED`, the CSR Request Form email is sent to the PKI mailbox via Microsoft Graph with `Mail.Send` scope; the email includes the CSR PEM and all required fields.
- **AC-4.2:** The email is sent exactly once per workflow — re-triggers return the recorded `email_thread_id` (idempotent).
- **AC-4.3:** The reply subscription is created so that when Mei's team replies with the CER attachment, a Logic App triggers the verification step automatically.
- **AC-4.4:** If no reply is received within 24h, a reminder email is sent; at 72h, a second reminder; at 5 business days, the workflow escalates to PD/on-call.
- **AC-4.5:** The email body is not treated as instructions by the orchestrator — it is logged as data only (G5).

---

### US-4b — CER verification (FR-7, FR-10)

**Persona:** Mei's counterpart / Priya

> *As Priya, a returned certificate that doesn't match the request is never installed.*

**Acceptance Criteria:**

- **AC-4b.1:** `verify_cer` returns `pass=False` if any of the following are true: CER cannot be parsed as X.509, CN does not match expected, SAN set does not exactly equal expected, chain does not build to a trusted root, `notAfter - now < 365 days`, or `notAfter` is already past.
- **AC-4b.2:** A failing verification verdict cannot transition state to `VERIFIED` — the state machine blocks this transition.
- **AC-4b.3:** The verifier verdict is code, not LLM opinion — the model cannot argument its way past a `pass=False` result.
- **AC-4b.4:** On failure, the magentic retry sub-orchestrator runs (≤ 2 retry rounds + ≤ 2 escalations), then resolves to `RESEND`, `ESCALATE_PD`, or `FAIL_OPEN`.
- **AC-4b.5:** `FAIL_OPEN` transitions state to `FAILED` (terminal); PD and on-call are notified; the manual runbook is referenced.
- **AC-4b.6:** The received CER file is stored in immutable Blob storage (WORM, 7-year legal hold) regardless of verification outcome.

---

## Epic 4: Change Management and Completion

### US-5 — ServiceNow change ticket and completion (FR-8, FR-9)

**Persona:** CAB / Change Management

> *As the CAB, every renewal produces a compliant, pre-approved change record linked to the Jira ticket and the CER artifact.*

**Acceptance Criteria:**

- **AC-5.1:** On `VERIFIED`, a ServiceNow Pre-Approved HDC Install/Renew CHG is created from the approved template; the CER is attached and the Jira ticket is linked.
- **AC-5.2:** The CHG number is recorded in `workflow_state.change.chg_number`; state transitions to `COMPLETE`.
- **AC-5.3:** A completion Adaptive Card is posted to the requester's Teams channel containing: final state, CN, CHG number, and `Action.OpenUrl` buttons to Jira, PKI thread, CER Blob (short-TTL SAS), and ServiceNow CHG.
- **AC-5.4:** The `COMPLETE` state is terminal — no further state transitions are possible.
- **AC-5.5:** `servicenow.create` is idempotent on `workflow_id` — no duplicate CHG tickets are opened on retry.

---

## Epic 5: Audit and Compliance

### US-6 — End-to-end audit reconstruction (FR-11)

**Persona:** Aisha (Compliance Auditor)

> *As Aisha, I can reconstruct any renewal end-to-end — who did what, when, why — from a single `workflow_id`.*

**Acceptance Criteria:**

- **AC-6.1:** For any `workflow_id`, the `audit_log` container yields an ordered, complete chain from alert ingestion to `COMPLETE`/`REJECTED`/`FAILED`, with no gaps.
- **AC-6.2:** Every audit record contains: `seq`, `timestamp`, `actor`, `action`, `tool`, `input_summary`, `output_summary`, `state_before`, `state_after`, `correlation_id`, `hash_prev`, `hash_self`.
- **AC-6.3:** `hash_self = SHA256(canonical(record) + hash_prev)` — the chain is verifiable; any tampered record breaks the hash chain.
- **AC-6.4:** No record in `audit_log` contains private key material, full CSR bytes, full CER bytes, or bearer tokens.
- **AC-6.5:** CER files are stored in immutable Blob storage with a 7-year legal hold and versioning enabled.
- **AC-6.6:** The audit trail is reconstructable months after the fact using only `workflow_id` as the lookup key.

---

## Epic 6: Fleet-Scale Batch Processing

### US-7 — Expiry-wave batch processing (FR-12, FR-13, FR-15)

**Persona:** Priya

> *As Priya, when 100 certs expire together, the system processes them all concurrently without flooding Jira or the PKI mailbox, and a single child failure never stalls the others.*

**Acceptance Criteria:**

- **AC-7.1:** A batch of N expiry alerts is de-duplicated by CN (first occurrence per CN wins); one isolated child workflow is created per unique CN.
- **AC-7.2:** Child workflows run concurrently under the `MAX_CONCURRENT_RENEWALS` semaphore (default 20); at no point do more than `MAX_CONCURRENT_RENEWALS` children hold the semaphore simultaneously.
- **AC-7.3:** PKI emails are rate-limited to `PKI_RATE_PER_MIN` (default 10/min); Jira calls to `JIRA_RATE_PER_MIN` (default 60/min); ServiceNow calls to `SNOW_RATE_PER_MIN` (default 30/min).
- **AC-7.4:** A `RateLimiter` back-pressure on one downstream lane (e.g. PKI throttle) does not stall calls in a different lane (Jira, SNOW).
- **AC-7.5:** If one child raises an unhandled exception, its state is recorded as `FAILED` in the batch aggregate; all other children continue unaffected (FR-15).
- **AC-7.6:** A batch record (`batch_id`) aggregates counts by state across all children and is visible in dashboards.
- **AC-7.7:** A 100-cert wave drains to all-submitted in < 1 business day with zero duplicate Jira tickets, zero duplicate ServiceNow CHGs, and zero PKI quota breaches.
- **AC-7.8:** Restarting the batch coordinator mid-wave re-attaches to existing child workflows (by `workflow_id`) instead of creating duplicates.

---

## Epic 7: Multi-Mode Interaction

### US-8 — Backend event-driven renewal (FR-12, FR-13)

**Persona:** Priya / System

> *As Priya, renewals start automatically when Dynatrace fires a webhook — no human initiation required.*

**Acceptance Criteria:**

- **AC-8.1:** A Dynatrace SSL-expiry webhook delivered to Event Grid → Service Bus → Logic App triggers the Orchestrator via `POST /api/orchestrate` within 60 seconds of receipt.
- **AC-8.2:** The webhook signature is validated; unsigned or replayed webhooks are rejected (401).
- **AC-8.3:** The orchestrator runs the full six-step workflow including the HITL approval gate — backend entry does not bypass approval.

---

### US-9 — Direct mode: status query (FR-11)

**Persona:** Priya / Sam

> *As Priya, I can ask "where is the renewal for api.prod.example.com?" in Teams and get a real-time status with links.*

**Acceptance Criteria:**

- **AC-9.1:** A natural-language status query in Teams/Copilot chat resolves to `GET /api/status?cn=...` and returns current state, last updated, and deep links (Jira, CHG if available).
- **AC-9.2:** The Copilot status topic returns results within 5 seconds.

---

### US-10 — Embedded mode: dashboard suggestion (read-only)

**Persona:** Priya

> *As Priya, the renewal dashboard shows me proactive suggestions like "12 certs expire in 30 days — start a batch?" — but the dashboard cannot mint a cert on its own.*

**Acceptance Criteria:**

- **AC-10.1:** The embedded suggestion service returns suggestion objects with `{kind, cn|batch, rationale, action_ref}` — it never directly calls `generate_csr`, `request_approval`, or any state-mutating tool.
- **AC-10.2:** Accepting a suggestion routes to a Direct or Backend call through the guarded core — including the HITL gate.
- **AC-10.3:** The Embedded mode holds only read-scoped data-plane roles; it cannot transition state.

---

## Traceability Matrix

| User Story | FRs Covered | Personas | Key Tests (P9) |
|-----------|------------|---------|----------------|
| US-1 | FR-1, FR-2 | Priya | `test_alert_ingest`, `test_cmdb_enrichment` |
| US-2 | FR-3, FR-4 | Priya | `test_generate_csr`, `test_wildcard_blocked` |
| US-3 | FR-5 | David | `test_approval_card`, `test_approval_callback` |
| US-3b | FR-14 | David | `test_batch_approval_card` |
| US-4 | FR-6 | Mei | `test_pki_email_sent` |
| US-4b | FR-7, FR-10 | Mei/Priya | `test_verify_cer`, `test_retry_orchestration` |
| US-5 | FR-8, FR-9 | CAB | `test_servicenow_chg`, `test_completion_card` |
| US-6 | FR-11 | Aisha | `test_audit_chain`, `test_hash_chain` |
| US-7 | FR-12, FR-13, FR-15 | Priya | `test_batch_coordinator`, `test_rate_limiter` |
| US-8 | FR-12 | System | `test_all_modes_hit_guarded_core` |
| US-9 | FR-11 | Priya/Sam | `test_status_query` |
| US-10 | FR-12 | Priya | `test_embedded_is_read_only` |
