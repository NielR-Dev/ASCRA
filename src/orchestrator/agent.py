"""SSL Renewal Orchestrator — supervisor ChatAgent wiring (T09).

build_orchestrator() creates the MAF 1.0 ChatAgent that drives the six-step renewal workflow.
This is the single entry point for all workflow executions, regardless of mode
(Backend event, Direct API, etc.).

Architecture:
  - Native tools (generate_csr, verify_cer, request_approval, record_approval_decision):
    run in-process; handle private keys, HITL, and deterministic verification.
  - Hybrid MCP tools (3 Foundry-hosted + 2 APIM-fronted): see mcp_tools.py.
  - Middleware: [PolicyMiddleware, AuditMiddleware] — order matters:
    Policy runs first (blocks bad calls), then Audit records every call.
  - System prompt: states the workflow + non-negotiable rules (see prompts.py).

Kill-switch:
  Callers should check `settings.orchestrator_enabled` before calling build_orchestrator().
  The orchestrate Function endpoint does this check.
"""
from __future__ import annotations

import logging
from typing import Any

from src.config import settings
from src.middleware.audit_middleware import AuditMiddleware
from src.middleware.policy_middleware import PolicyMiddleware
from src.orchestrator.mcp_tools import build_mcp_tools
from src.orchestrator.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from src.tools.approval_tool import record_approval_decision, request_approval
from src.tools.generate_csr import generate_csr
from src.tools.verify_cer import verify_cer

logger = logging.getLogger("ssl_renewal.agent")

# Native tools that always run in-process (security-sensitive / deterministic)
NATIVE_TOOLS = [generate_csr, verify_cer, request_approval, record_approval_decision]


def build_chat_client() -> Any:
    """Create the Foundry chat client using Managed Identity.

    Lazy-imported so this module can be loaded in test environments without
    the azure-identity package connected to a live Foundry endpoint.

    Raises:
        RuntimeError: if FOUNDRY_PROJECT_ENDPOINT is not configured.
    """
    if not settings.foundry_project_endpoint:
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT is not configured. "
            "Set this environment variable before starting the Function App."
        )

    try:
        from agent_framework.foundry import FoundryChatClient
        from azure.identity.aio import DefaultAzureCredential
    except ImportError as exc:
        raise ImportError(
            "agent_framework.foundry or azure-identity is not installed: {exc}"
        ) from exc

    credential = (
        DefaultAzureCredential(managed_identity_client_id=settings.azure_client_id)
        if settings.azure_client_id
        else DefaultAzureCredential()
    )

    logger.info(
        "build_chat_client: endpoint=%s deployment=%s",
        settings.foundry_project_endpoint[:40], settings.azure_openai_deployment
    )

    return FoundryChatClient(
        project_endpoint=settings.foundry_project_endpoint,
        model_deployment_name=settings.azure_openai_deployment,
        credential=credential,
    )


def build_orchestrator(chat_client: Any | None = None) -> Any:
    """Build the supervisor ChatAgent with all tools and middleware wired.

    Args:
        chat_client: optional pre-built chat client (for testing). If None, a real
                     FoundryChatClient is created via build_chat_client().

    Returns:
        A MAF 1.0 ChatAgent ready to run the renewal workflow.

    Wiring:
        tools = [generate_csr, verify_cer, request_approval, record_approval_decision,
                 graph_mail, servicenow, azure, dynatrace, jira]
        middleware = [PolicyMiddleware(), AuditMiddleware()]   # Policy FIRST
    """
    try:
        from agent_framework import ChatAgent
    except ImportError as exc:
        raise ImportError("agent_framework package is required.") from exc

    client = chat_client or build_chat_client()

    # Build the tool registry: native first (deterministic guardrails) + MCP surface
    tools = [*NATIVE_TOOLS, *build_mcp_tools()]

    # Middleware: PolicyMiddleware is the OUTER wrapper (runs first),
    # AuditMiddleware is the INNER wrapper (runs after policy validates the call).
    # Both re-raise exceptions so the orchestrator's retry logic sees them.
    middleware = [PolicyMiddleware(), AuditMiddleware()]

    agent = ChatAgent(
        chat_client=client,
        name="ssl_renewal_orchestrator",
        instructions=ORCHESTRATOR_SYSTEM_PROMPT,
        tools=tools,
        middleware=middleware,
    )

    logger.info(
        "build_orchestrator: agent built with %d tools and %d middleware",
        len(tools), len(middleware)
    )
    return agent
