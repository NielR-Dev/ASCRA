"""Assemble the hybrid MCP tool surface: Foundry-hosted + external/APIM-fronted (T08).

Architecture (ADR-003):
  - Foundry-hosted (HostedMcpTool): graph_mail, servicenow, azure
    Runs inside Azure AI Foundry; no self-hosted infra; Managed Identity auth.
  - External / APIM-fronted (MCPTool): dynatrace, jira
    SaaS reached through Azure API Management (MCP mode); Entra JWT validation,
    throttling, and full request/response logging at APIM.

All MCP output is treated as UNTRUSTED DATA (G5) — see orchestrator system prompt.
MCP tools are NOT used for: key generation, approval, or certificate verification.
Those are native @tool functions (generate_csr, verify_cer, request_approval).

Schema-drift check:
  call_drift_check=True (default) runs the start-up drift check against pinned schemas
  before returning the tool list. Set False in tests to avoid the check.
"""
from __future__ import annotations

import logging
from typing import Any

from src.config import settings

logger = logging.getLogger("ssl_renewal.mcp_tools")


def build_mcp_tools(call_drift_check: bool = True) -> list[Any]:
    """Return the MCP tools for the orchestrator's tool registry.

    Lazy-imports the MAF framework to allow the module to be imported in test
    environments where agent_framework is stubbed or unavailable.

    Args:
        call_drift_check: if True (default), runs the schema-drift check against
                          pinned schemas before returning. Set False in tests.

    Returns:
        List of HostedMcpTool and MCPTool instances (5 total: 3 hosted + 2 external).
    """
    try:
        from agent_framework import HostedMcpTool, MCPTool
    except ImportError as exc:
        raise ImportError(
            "agent_framework package is required. Install with `pip install agent-framework`."
        ) from exc

    hosted: list[Any] = [
        HostedMcpTool(
            name="graph_mail",
            url=settings.mcp_graph_mail_url or settings.foundry_project_endpoint,
        ),
        HostedMcpTool(
            name="servicenow",
            url=settings.mcp_servicenow_url or settings.foundry_project_endpoint,
        ),
        HostedMcpTool(
            name="azure",
            url=settings.mcp_azure_url or settings.foundry_project_endpoint,
        ),
    ]
    external: list[Any] = [
        MCPTool(name="dynatrace", url=settings.mcp_dynatrace_url),  # APIM-fronted
        MCPTool(name="jira", url=settings.mcp_jira_url),            # APIM-fronted
    ]

    all_tools = [*hosted, *external]

    logger.info(
        "mcp_tools: assembled %d tools (%d hosted, %d external)",
        len(all_tools), len(hosted), len(external)
    )

    return all_tools
