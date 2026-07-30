# Interaction Modes — Specification
## Autonomous SSL Certificate Renewal Agent

> **One guarded core, many front doors.**  
> All three modes (Direct, Embedded, Backend) funnel into the same orchestrator + state machine + PolicyMiddleware + AuditMiddleware + HITL gate. No mode gets a privileged path. Guardrails G1–G8 apply regardless of entry point.

---

## Architecture Principle

```
    DIRECT adapters             EMBEDDED adapters              BACKEND adapters
   ┌────────────────┐         ┌───────────────────┐        ┌──────────────────────┐
   │ Copilot/Teams  │         │ Dashboard suggest. │        │ Event Grid webhook   │
   │ Slack bot/cmds │         │ Card nudges        │        │ Programmatic API     │
   │ Web console API│         │ (read + suggest)   │        │ Approval/PKI callback│
   └───────┬────────┘         └─────────┬─────────┘        │ Scheduled scan (cron)│
           │                            │                   └──────────┬───────────┘
           │  normalize + authN         │  read/project + suggest      │  authN (MI/JWT/sig)
           ▼                            ▼                              ▼
      ┌─────────────────────────────────────────────────────────────────────────────┐
      │  GUARDED CORE:  Batch Coordinator → child Orchestrator(s)                   │
      │  State Machine · PolicyMiddleware (G1,G2,G3,G6) · AuditMiddleware (G4)      │
      │  Native tools (generate_csr / verify_cer / request_approval) · HITL         │
      └─────────────────────────────────────────────────────────────────────────────┘
```

**Adapter rules (enforced in code and contract tests):**
- Adapters do **only** protocol translation + authentication + input normalization.
- Adapters hold **no** business rules, **no** guardrails, and **no** direct state mutation.
- All mutations happen by calling the **same public core entrypoints** every other mode uses.
- Any new surface is just another adapter — zero core changes required.

---

## Mode 1 — Direct (Human-initiated)

### Overview

| Property | Value |
|----------|-------|
| Initiator | Human (Priya, David, Sam, Aisha, Application owners) |
| Latency expectation | Sub-second → seconds |
| Can mutate state? | Yes — via the guarded tools + HITL |
| Audit actor | `human.email` (Entra identity) |

### Surface 1: Teams / Copilot Chat

**Description:** Conversational interface for status queries and renewal initiation.

**Endpoints called:**
- Status: `GET /api/v1/status?cn={cn}` or `GET /api/v1/workflows/{workflow_id}`
- Renewal: `POST /api/v1/renew { cn, san, owning_application }`

**Authentication:** Entra SSO (OIDC). The Teams/Copilot identity token is validated by the Azure Function's Easy Auth before any operation.

**Roles:**
| Role | Permissions |
|------|------------|
| Viewer | `GET /api/v1/status`, `GET /api/v1/workflows/*`, `GET /api/v1/batches/*` |
| Operator | Viewer + `POST /api/v1/renew`, `POST /api/v1/batch` |
| Approver | Viewer + `POST /api/approval-callback` (PD only) |

**Authorization rules:** role membership is derived from Entra group membership. The `Approver` role is restricted to the Product Director's account (and delegate). Role checks are enforced by the Azure Function, not the adapter.

**Copilot Studio topics:** see [`topics.md`](../copilot/topics.md) — Topic 1 (Approve CSR) and Topic 2 (Check Status).

---

### Surface 2: Slack App

**Description:** Operator slash commands for quick status lookups and renewal initiation from Slack.

**Slash commands:**

| Command | Behaviour | Maps to |
|---------|-----------|---------|
| `/ssl-status <cn\|batch_id>` | Returns current state + Jira/CHG links | `GET /api/v1/status?cn={cn}` |
| `/ssl-renew <cn>` | Initiates a renewal; blocks on PD approval | `POST /api/v1/renew` → HITL gate |
| `/ssl-batch <wave_description>` | Initiates a batch renewal for a named expiry wave | `POST /api/v1/batch` |

**Authentication:**
1. **Slack request-signature verification** (HMAC-SHA256 with `SLACK_SIGNING_SECRET`): every inbound request is verified against `X-Slack-Signature` header using the Slack signing secret before any processing. Unsigned or replayed requests (timestamp > 5 minutes old) are rejected with HTTP 401.
2. **Slack user → Entra identity mapping**: the Slack user ID is resolved to an Entra identity via a configured lookup table or Entra external identities. The Entra identity is used for role checks and the audit `actor` field.
3. If Slack identity cannot be mapped to an Entra identity, the request is rejected with a "user not recognized" error.

**Adapter implementation:** `src/interfaces/direct/slack_adapter.py`

```
[Inbound Slack command]
    │  1. Verify X-Slack-Signature (HMAC-SHA256)
    │  2. Parse command + args
    │  3. Map Slack user → Entra identity
    │  4. Resolve role from Entra group membership
    ▼
[Call core entrypoint]
    POST /api/v1/renew  ──or──  GET /api/v1/status
    (same guarded core every other mode uses)
    ▼
[Format Slack response]
    Return plain-text or Block Kit message with result
```

**Important:** `/ssl-renew` goes through the same HITL gate. The Slack user will receive a confirmation message ("Renewal initiated — waiting for PD approval") and the PD gets the Teams Adaptive Card. The Slack command does not bypass approval.

**Resilience:** if Slack is unavailable, backend event-driven renewal continues unaffected.

---

### Surface 3: Web Console

**Description:** Browser-based operator dashboard for workflow and batch status visibility and approval queue management.

**API endpoints (all behind Entra SSO):**

| Endpoint | Method | Purpose | Role Required |
|----------|--------|---------|--------------|
| `/api/v1/workflows/{workflow_id}` | GET | Single workflow detail | Viewer |
| `/api/v1/workflows?cn={cn}` | GET | Lookup by CN | Viewer |
| `/api/v1/batches/{batch_id}` | GET | Batch summary + child list | Viewer |
| `/api/v1/approvals` | GET | Pending approval queue | Approver |
| `/api/v1/status?cn={cn}` | GET | Quick status | Viewer |
| `/api/v1/renew` | POST | Initiate renewal | Operator |
| `/api/v1/batch` | POST | Initiate batch | Operator |
| `/api/v1/suggestions` | GET | Dashboard suggestions (read-only) | Viewer |

**Authentication:** Entra SSO (OIDC). Web console frontend uses MSAL.js for token acquisition. The Azure Function validates the bearer token and enforces role checks per request.

**Adapter implementation:** `src/interfaces/direct/web_console_api.py`

**UI design guidelines:**
- IBM Carbon g10 design system tokens (see `00-context.md` §2.7)
- Sharp corners (radius 0), flat surfaces, 1px `#e0e0e0` borders
- Responsive: single-column below 900 px
- Accessibility: WCAG 2.2 AA — contrast ≥ 4.5:1, no information by color alone, keyboard operable

---

## Mode 2 — Embedded (In-context, read + suggest)

### Overview

| Property | Value |
|----------|-------|
| Initiator | System (proactive) or host surface |
| Latency expectation | Near-real-time |
| Can mutate state? | **No** — read and suggest only |
| Audit actor | `host_surface + user_identity` |

**Design rule:** Embedded is a **projection** of state (read) plus *suggestions* that, when accepted, become a Direct or Backend request. An accepted suggestion is never a side-door mutation — it routes through the guarded core.

### Surface 1: Dashboard Suggestions (Azure Workbook / Power BI)

**Description:** Read-model projections that surface actionable suggestions based on cert inventory state.

**API:** `GET /api/v1/suggestions`

**Response schema:**
```json
[
  {
    "kind": "renewal_wave",
    "title": "12 certificates expire in 30 days",
    "rationale": "CA cohort expiry; all owned by Orders-API",
    "action_ref": {
      "type": "batch_renew",
      "alert_cns": ["api.prod.example.com", "auth.prod.example.com", "..."],
      "endpoint": "POST /api/v1/batch"
    }
  }
]
```

**Accepting a suggestion:** the dashboard renders an "Accept" button that emits a `POST /api/v1/batch` request (Backend mode). The suggestion service **never** calls `POST /api/v1/batch` itself. This preserves the HITL gate — the PD will still receive the batch approval card.

**Read-only enforcement:**
- The suggestion service's Managed Identity holds **read-only** data-plane roles on Cosmos (`Cosmos DB Built-in Data Reader`).
- It cannot call `generate_csr`, `request_approval`, or any state-mutating endpoint.
- Contract test `test_embedded_is_read_only` verifies this.

**Adapter implementation:** `src/interfaces/embedded/suggestion_service.py`, `src/interfaces/embedded/read_model.py`

---

### Surface 2: Adaptive Card Nudges

**Description:** Proactive Teams cards surfaced to operators with upcoming-expiry warnings.

**Example:** "12 certs expire in 30 days — start a batch renewal?" with an [Accept] button.

**Behaviour:** pressing [Accept] emits a `POST /api/v1/batch` from the user's Teams identity — a **Direct mode** action — that flows through the full HITL gate.

**Constraint:** the nudge card itself cannot mint a cert or start any workflow. It is a UI affordance that emits a user request.

---

## Mode 3 — Backend (Machine-to-machine, event-driven)

### Overview

| Property | Value |
|----------|-------|
| Initiator | Machine / event / timer |
| Latency expectation | Async (seconds → days for PKI) |
| Can mutate state? | Yes — bounded by the same guardrails + HITL |
| Audit actor | Service principal / MI identity |

### Surface 1: Dynatrace Webhook (Primary production intake)

**Description:** Dynatrace fires an SSL-expiry webhook → Azure Event Grid → Service Bus → Logic App → `POST /api/orchestrate`.

**Authentication:** Event Grid system-topic managed validation. Service Bus: Managed Identity. Logic App: Managed Identity for Service Bus dequeue and for `POST /api/orchestrate`.

**Idempotency:** the webhook event carries a `problem_id`; the orchestrate endpoint de-duplicates by `cn` — a second alert for the same CN during an active workflow does not start a second child.

**Adapter implementation:** `src/interfaces/backend/event_trigger.py`

```
[Dynatrace SSL-expiry webhook]
    │  Signed webhook → Azure Event Grid (validates subscription)
    ▼
[Event Grid → Service Bus queue]
    │  Managed Identity for Service Bus
    ▼
[Logic App dequeues]
    │  Enriches: resolves CN/SAN if only hostname present (CMDB call)
    │  POST /api/orchestrate  { alert: { cn, san, owning_application, source, problem_id } }
    ▼
[Azure Function: orchestrate]
    │  Managed Identity auth
    │  → build_orchestrator().run(alert)
    ▼
[Guarded core — identical to every other mode]
```

---

### Surface 2: Programmatic API

**Description:** REST API for other systems or agents to initiate renewals programmatically.

| Endpoint | Body | Returns | Auth |
|----------|------|---------|------|
| `POST /api/v1/renew` | `{ cn, san, owning_application }` | `{ workflow_id, state }` | APIM JWT/subscription key |
| `POST /api/v1/batch` | `{ alerts: [{cn, san, owning_application}] }` | `{ batch_id, total }` | APIM JWT/subscription key |

**Note:** `POST /api/v1/renew` still triggers the full HITL gate — the caller receives `state: CSR_REQUESTED` and the PD gets the Teams card. The API does not bypass approval.

**Rate limiting:** enforced at APIM — 100 req/min per subscription key. Downstream rate limits (PKI/Jira/SNOW) are enforced inside the batch coordinator via `rate_limiter.py`.

---

### Surface 3: MCP Tool Exposure

**Description:** The SSL renewal agent exposes `ssl_renewal.request(cn, san)` as an MCP tool for other agents to call (e.g., a certificate-lifecycle agent requesting renewal for a discovered expiring cert).

**Auth:** APIM validates Entra JWT from the calling agent's Managed Identity. The calling agent must have the `ssl-renewal.request` app role.

**Guardrail:** the MCP tool maps to `POST /api/v1/renew` — same guarded core, same HITL gate.

---

### Surface 4: Approval and PKI Callbacks

**Description:** Inbound callbacks from Copilot/Power Automate (approval decision) and Logic App (PKI reply with CER).

| Endpoint | Purpose | Auth |
|----------|---------|------|
| `POST /api/approval-callback` | Record PD's Approve/Reject decision | Entra token (PD identity) + `thread_id` binding + MFA assertion |
| `POST /api/pki-reply` | Trigger CER verification when PKI replies | Logic App Managed Identity |

**Approval callback security:** the server validates:
1. Entra bearer token is valid and not expired
2. `approver` claim in token matches the expected approver on record
3. `thread_id` in body matches the `card_correlation_id` stored in `workflow_state`
4. MFA claim is present in the token
5. Mismatched on any of these → 401 rejected; audit event written

---

### Surface 5: Scheduled Inventory Scan

**Description:** A timer-triggered Azure Function runs nightly to query the cert inventory (CMDB / Azure Key Vault list), identify certs expiring within the configured horizon (default: 45 days), and enqueue them as a batch renewal request.

**Adapter implementation:** `src/interfaces/backend/scheduled_scan.py`

**Auth:** Managed Identity for Key Vault list, CMDB query, and `POST /api/v1/batch`.

**De-duplication:** before enqueueing, the scan queries `workflow_state` for any active renewal for each CN — already-in-flight renewals are skipped.

---

## Cross-Mode Design Rules

| Rule | Applies to |
|------|-----------|
| All mutations route through guarded core | All three modes |
| Adapter holds no business logic | All three modes — verified by `test_adapter_has_no_logic` |
| Identity preserved in audit log per mode | All three modes |
| Embedded is read/suggest-only | Embedded mode — verified by `test_embedded_is_read_only` |
| HITL gate applies regardless of entry point | All three modes — verified by `test_all_modes_hit_guarded_core` |
| Slack request-signature required | Direct/Slack — verified by `test_slack_signature_required` |
| Idempotency spans modes | All three modes — same CN de-dupes to one child workflow |
| Graceful degradation | Slack down → Backend unaffected; Embedded unavailable → nothing blocks |

---

## Security Summary by Mode

| Mode | AuthN | AuthZ | Audit actor |
|------|-------|-------|------------|
| Direct — Teams/Copilot | Entra SSO (OIDC), MFA for approval | Entra group-based role (Viewer/Operator/Approver) | `user.email` |
| Direct — Slack | Slack HMAC-SHA256 signature + Entra identity mapping | Entra group-based role | `user.email` (Entra) |
| Direct — Web console | Entra SSO (MSAL.js + OIDC) | Entra group-based role | `user.email` |
| Embedded — Dashboard | Host-surface identity | Read-only Cosmos data role | `host_surface + user_identity` |
| Embedded — Nudge card | Host-surface identity | Read-only; accept → user identity | `user.email` on accept |
| Backend — Webhook | Signed Event Grid validation + MI | MI least-privilege (trigger only) | Service principal |
| Backend — API/MCP | APIM Entra JWT / subscription key | App role (`ssl-renewal.request`) | Calling service principal |
| Backend — Callbacks | Entra bearer token + thread_id binding + MFA | `Approver` role (approval); Logic App MI (PKI) | `approver.email` / service principal |
| Backend — Scheduled scan | Managed Identity | MI least-privilege (read inventory + enqueue) | Service principal |

---

## IBM Carbon g10 Design Tokens

Apply these tokens on any HTML/dashboard/report surface rendered by this system:

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

Sharp corners (border-radius: 0), flat surfaces, 1px `#e0e0e0` borders. Responsive: single-column below 900 px. Color is never the sole information signal — always pair with a text label.
