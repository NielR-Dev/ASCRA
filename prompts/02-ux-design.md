# Phase 2 — Product & UX Design

> **Pre-read:** [00-context.md](00-context.md) · depends on P1 output
> **Deliverable:** Adaptive Cards, Copilot Studio topics, 3-mode surface specs
> **Effort estimate:** ~2–3 person-days

---

## Your Task

Design all human-facing surfaces: the PD approval card, the batch approval card, the completion card, and the Copilot Studio topics. Also define the full spec for the three interaction modes (Direct/Embedded/Backend) as thin adapters over one guarded core.

---

## What to Produce

1. **`copilot/approval-card.json`** — Adaptive Card 1.5 spec (PD approval)
2. **`copilot/batch-approval-card.json`** — Batch approval card for expiry waves
3. **`copilot/completion-card.json`** — Completion card with deep links
4. **`copilot/topics.md`** — Copilot Studio topic specs (Approve CSR + Check Status)
5. **`docs/interaction-modes.md`** — Spec for Direct/Embedded/Backend surfaces

---

## Key Design Principles

1. **One guarded core, many front doors.** All three modes call the same orchestrator + state machine + PolicyMiddleware + AuditMiddleware + HITL gate. No mode gets a privileged path.
2. **Embedded is read + suggest only.** Dashboard suggestions and card nudges cannot mint a cert or approve. An "accepted" suggestion emits a normal Direct or Backend request.
3. **Identity is preserved per mode** in the audit log (`actor` = human email for Direct, service principal for Backend, host+user for Embedded).
4. **Graceful degradation.** If Slack is down, backend event-driven renewal is unaffected. If embedded suggestions are unavailable, nothing blocks.

---

## Approval Card Spec (Adaptive Card 1.5)

Build `copilot/approval-card.json` with exactly these elements:

- **Header:** "SSL Renewal — CSR Approval Required" (Bolder, Large)
- **FactSet:** Hostname (CN), SAN list (comma-joined), Owning application, Jira link (linked), Requested-at timestamp
- **Optional Input.Text:** `reasoning` (multiline, label "Reason (required for reject)")
- **Actions:**
  - `Action.Submit` labelled **Approve** — `data.decision = "APPROVED"`
  - `Action.Submit` labelled **Reject** — `data.decision = "REJECTED"`
- **Callback body shape:** `{ thread_id, decision, approver (user.email), reasoning }`

**Accessibility (WCAG 2.2 AA):**
- Every input/action has a text label
- Color is not the sole signal (Approve/Reject also text-labelled, not just colored)
- Contrast ≥ 4.5:1 using the IBM Carbon g10 tokens below
- Tab order top-to-bottom

---

## Batch Approval Card Spec

Build `copilot/batch-approval-card.json` for expiry waves (N certificates):

- **Header:** `"SSL Renewal — Batch Approval (batch_id, N certificates)"`
- **Summary FactSet:** total certs, distinct owning applications, any flagged anomalies, requested-at
- **Per-certificate rows** (Container/ColumnSet, paginated ≥ 15): CN, SAN count, owner, Jira link, per-row Approve/Reject toggle (`Input.Toggle` bound to `decision[workflow_id]`)
- **Bulk actions:** `Action.Submit` **Approve All**, **Reject All**, **Submit Selections** — each carries optional `reasoning`
- **Callback body:** `{ batch_id, decisions: [{ workflow_id, decision, reasoning }], approver }`
- **Guardrail:** a child not explicitly approved defaults to **pending**, never auto-approved. Anomalous rows visually flagged with a text label.
- **Fallback:** same batch decision set available via Power Automate approval

---

## Completion Card Spec

Build `copilot/completion-card.json`:

- FactSet: final state, CN, CHG number, completed-at
- `Action.OpenUrl` buttons: Jira ticket, PKI email thread, CER Blob (SAS-scoped short TTL), ServiceNow CHG
- Posted proactively to the requester's Teams channel

---

## Copilot Studio Topics

Document in `copilot/topics.md`:

**Topic 1 — Approve CSR** (event-triggered by `approval.request`):
- Triggers when the orchestrator fires `request_approval`
- Sends the Adaptive Card 1.5 to the PD
- Receives callback at `/api/approval-callback`
- Power Automate fallback for when PD prefers email approval

**Topic 2 — Check Status** (user-invoked, generative):
- User asks: "Where is renewal for api.prod.example.com?"
- Orchestrator maps to `get_status(cn|workflow_id)` action
- Returns current state + timeline + deep links

---

## Three Interaction Modes — Surface Specs

Document in `docs/interaction-modes.md`:

### Direct Mode

| Surface | Contract | Auth |
|---------|----------|------|
| Teams / Copilot chat | Conversational → `get_status` / `request_renewal` actions | Entra SSO |
| **Slack app** | `/ssl-status <cn\|batch_id>`, `/ssl-renew <cn>`, `/ssl-batch <wave>` | Slack OAuth + request-signature verification |
| **Web console** | `GET /api/v1/workflows/{id}`, `GET /api/v1/batches/{id}`, `GET /api/v1/approvals` | Entra SSO; role-scoped |

A Slack `/ssl-renew` maps to the same guarded `run_child` entrypoint an event would trigger — and still blocks on PD approval.

### Embedded Mode

| Surface | Contract | Constraint |
|---------|----------|-----------|
| Dashboard suggestions (Azure Workbook, Power BI) | `GET /api/v1/suggestions` → `[{kind, cn, rationale, action_ref}]` | **Read + suggest only** |
| Adaptive Card nudges | Proactive card: "12 certs expire in 30 days — start a batch?" | Accept → Direct or Backend request, never a side-door mutation |

### Backend Mode

| Surface | Contract | Auth |
|---------|----------|------|
| Dynatrace webhook | Event Grid event → `POST /api/orchestrate` | Signed webhook (Event Grid validation) |
| Programmatic API | `POST /api/v1/renew {cn,san,owning_application}` → `{workflow_id,state}` | APIM JWT/key, throttled |
| Batch API | `POST /api/v1/batch {alerts[]}` → `{batch_id}` | APIM JWT/key |
| MCP tool | `ssl_renewal.request(cn, san)` for other agents | Least-privilege; audited |
| Approval callback | `POST /api/approval-callback` | Entra token + `thread_id` binding |
| PKI reply callback | `POST /api/pki-reply` | MI / Logic App identity |
| Scheduled scan | Timer → query cert inventory → enqueue expiring certs as a batch | MI |

---

## Design System Tokens (IBM Carbon g10)

Apply these on any HTML/dashboard/report surface:

| Token | Value | Use |
|-------|-------|-----|
| `--ibm-blue` | `#0f62fe` | Primary / links / accents |
| `--text` | `#161616` | Primary text |
| `--text-2` | `#393939` | Secondary text |
| `--bg-alt` | `#f4f4f4` | Background |
| `--ok` | `#24a148` | Pass / success (with text label) |
| `--warn` | `#f1c21b` | Caution (with text label) |
| `--danger` | `#da1e28` | Fail / block (with text label) |
| Font | IBM Plex Sans / Mono / Serif | UI / code / headings |

Sharp corners (radius 0), flat surfaces, 1px `#e0e0e0` borders. Responsive: single-column below 900px.

---

## Acceptance Criteria

- Approval card shows CN + full SAN list + owner + Jira link before any decision
- Approve/Reject write the same record via Teams **and** the Power Automate fallback
- Contrast ≥ 4.5:1; no information conveyed by color alone
- A rejection captures `reasoning` and is stored in the audit log
- Status query returns current state + timeline + links for a known `workflow_id`
- All three modes reach the one guarded core; Embedded is read/suggest-only
- Batch card anomalous rows are text-labelled (not color-only); unapproved certs default to pending

---

## Verification

- Render the approval card in Teams and confirm all facts + AT labels are present
- Run `test_all_modes_hit_guarded_core` — Direct (Slack/web), accepted Embedded suggestion, and Backend (event/API) all invoke the same guarded entrypoint and block on PD approval
- Run `test_embedded_is_read_only` — Embedded cannot mint a cert, approve, or transition state
- Run `test_slack_signature_required` — unsigned/replayed Slack requests rejected
- A rejection in the approval card closes Jira and writes an audit event
