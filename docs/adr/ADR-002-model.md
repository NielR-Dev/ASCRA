# ADR-002 — LLM: Azure OpenAI GPT-4o (`gpt-4o-2024-11-20`)

> **Status:** Accepted  
> **Date:** 2026-07-28  
> **Decision makers:** Architecture team, IBM Bob

---

## Context

The SSL Renewal Orchestrator uses an LLM for two distinct purposes:

1. **Orchestration planning:** deciding which tool to call next given the current workflow state and tool registry. Requires reliable multi-step tool-calling, adherence to the system prompt guardrails, and structured output.
2. **Retry/diagnosis reasoning (FR-10):** the Diagnostic and Escalation agents in the magentic sub-orchestration classify CER verification failures and select a remediation path (RESEND / ESCALATE_PD / FAIL_OPEN). This requires nuanced reasoning about PKI error messages.

Additionally, the status query topic (Copilot Studio Topic 2) needs lighter-weight text generation for response formatting.

Key constraints:
- **Data residency:** all model invocations must remain within the Azure Singapore / Southeast Asia region (Client compliance).
- **Tool-calling reliability:** the model must consistently select the correct tool with correctly-typed arguments; a tool-calling miss blocks a renewal step.
- **No LLM in the verifier:** `verify_cer` is pure deterministic Python code — the model cannot influence its verdict (G2).
- **No LLM for key generation:** `generate_csr` calls the Azure Key Vault SDK directly — the model only invokes the tool, never sees key material (G7, G8).

---

## Decision

**Primary: Azure OpenAI GPT-4o (`gpt-4o-2024-11-20`)** for orchestration and retry reasoning.

- **Strong tool-calling fidelity:** GPT-4o's function-calling is among the most reliable for multi-step, structured-output scenarios — directly relevant to the six-step renewal workflow.
- **Azure data-residency:** deployed in Azure OpenAI Service (East Asia / Southeast Asia region); data does not leave the Azure boundary.
- **Content Safety + Prompt Shield:** available as an Azure add-on for LLM01/LLM06 risk mitigation (G5).
- **Reasoning depth:** the retry/diagnosis branch needs genuine analysis of PKI error messages — GPT-4o's reasoning quality is materially better than mini variants.

**Secondary (status summaries only): Azure OpenAI GPT-4o-mini** — optionally used for the Copilot Studio "Check Status" topic response generation, where the task is formatting a pre-fetched structured response into natural language, not tool-calling or reasoning.

---

## Alternatives Considered

### Alternative 1: GPT-4o-mini everywhere

**Rejected for orchestration.** GPT-4o-mini is cheaper and lower-latency but shows measurably weaker multi-step tool-calling accuracy in multi-turn scenarios. For a healthcare-grade system where a missed tool call blocks a renewal step (and incorrect tool args could violate guardrails), the reliability degradation is unacceptable at the orchestration layer.

Accepted for status summarization (lightweight text generation with no tool calls).

### Alternative 2: Azure AI Studio fine-tuned model

**Rejected.** No fine-tuning is performed (ADR mitigates LLM03 — training-data poisoning is N/A). The base GPT-4o model with a well-structured system prompt and deterministic middleware achieves the required behaviour without the complexity of a fine-tuning pipeline.

### Alternative 3: Open-source LLM (Llama 3, Mistral) on Container Apps

**Rejected.** No Azure-managed equivalent of GPT-4o's tool-calling reliability is available in the open-source space as of this writing that meets both the data-residency and reliability requirements. Self-hosting adds significant MLOps overhead for a security-critical system.

### Alternative 4: Azure AI Foundry — other hosted models (Claude 3.5, Gemini)

**Rejected.** Non-OpenAI models in the Azure AI Foundry catalog do not yet have the same native MAF 1.0 FoundryChatClient integration, making the framework swap riskier. Re-evaluate for v1.1+ if GPT-4o pricing becomes a concern.

---

## Consequences

| Consequence | Mitigation |
|------------|-----------|
| GPT-4o cost is higher than mini | Mini used for status summarization; no model calls in verifier or CSR generation (pure code) |
| Model behaviour can change across AOAI deployments | Pin `gpt-4o-2024-11-20`; PromptFlow nightly evals (P9.8) detect regressions before reaching production |
| Single model deployment is a soft SPOF | Foundry managed runtime handles deployment health; retry + backoff on transient 5xx |
| LLM cannot be prevented from hallucinating (it's probabilistic) | Deterministic state machine + PolicyMiddleware are the authoritative controls; the model only chooses *among* legal, guardrailed options |

**Confidence:** High. GPT-4o is the best-available balance of tool-calling reliability, reasoning depth, Azure data-residency, and operational maturity for this use case.
