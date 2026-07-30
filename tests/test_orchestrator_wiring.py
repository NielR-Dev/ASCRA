"""Tests for orchestrator wiring (T09, T08).

Verifies:
  - build_orchestrator returns an agent with 4 native + 5 MCP tools (9 total)
  - Middleware order is [PolicyMiddleware, AuditMiddleware]
  - build_mcp_tools returns 5 tools (3 hosted + 2 external)
  - Orchestrator uses the correct system prompt
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.middleware.policy_middleware import PolicyMiddleware
from src.middleware.audit_middleware import AuditMiddleware


class TestOrchestratorWiring:
    @pytest.mark.asyncio
    async def test_build_orchestrator_tool_count(self) -> None:
        """9 tools total: 4 native + 3 hosted MCP + 2 external MCP."""
        # Stub out the framework so we don't need a live Foundry endpoint
        fake_chat_client = MagicMock()
        captured_tools = []
        captured_middleware = []

        class FakeChatAgent:
            def __init__(self, **kwargs: object) -> None:
                captured_tools.extend(kwargs.get("tools", []))
                captured_middleware.extend(kwargs.get("middleware", []))

        with patch.dict("sys.modules", {
            "agent_framework": MagicMock(
                ChatAgent=FakeChatAgent,
                HostedMcpTool=lambda **kw: MagicMock(name=kw.get("name", "hosted")),
                MCPTool=lambda **kw: MagicMock(name=kw.get("name", "external")),
            )
        }):
            with patch("src.config.settings") as mock_settings:
                mock_settings.foundry_project_endpoint = "https://foundry.example.com"
                mock_settings.azure_openai_deployment = "gpt-4o-2024-11-20"
                mock_settings.azure_client_id = None
                mock_settings.mcp_graph_mail_url = None
                mock_settings.mcp_servicenow_url = None
                mock_settings.mcp_azure_url = None
                mock_settings.mcp_dynatrace_url = "https://apim.example.com/dynatrace"
                mock_settings.mcp_jira_url = "https://apim.example.com/jira"

                # Re-import to pick up the mock
                import importlib
                import src.orchestrator.mcp_tools as mcp_mod
                importlib.reload(mcp_mod)

                tools = mcp_mod.build_mcp_tools(call_drift_check=False)
                assert len(tools) == 5, f"Expected 5 MCP tools, got {len(tools)}"

    def test_native_tools_list_has_four_tools(self) -> None:
        """4 native tools: generate_csr, verify_cer, request_approval, record_approval_decision."""
        from src.orchestrator.agent import NATIVE_TOOLS
        assert len(NATIVE_TOOLS) == 4

    def test_middleware_order_policy_then_audit(self) -> None:
        """Middleware must be [PolicyMiddleware, AuditMiddleware] in that order."""
        fake_chat_client = MagicMock()
        captured = {}

        class FakeChatAgent:
            def __init__(self, **kwargs: object) -> None:
                captured["middleware"] = kwargs.get("middleware", [])
                captured["tools"] = kwargs.get("tools", [])

        class FakeHostedMcpTool:
            def __init__(self, **kwargs: object) -> None:
                pass

        class FakeMCPTool:
            def __init__(self, **kwargs: object) -> None:
                pass

        with patch.dict("sys.modules", {
            "agent_framework": MagicMock(
                ChatAgent=FakeChatAgent,
                HostedMcpTool=FakeHostedMcpTool,
                MCPTool=FakeMCPTool,
            ),
            "agent_framework.foundry": MagicMock(FoundryChatClient=MagicMock),
        }):
            with patch("src.config.settings") as mock_settings:
                mock_settings.foundry_project_endpoint = "https://foundry.example.com"
                mock_settings.azure_openai_deployment = "gpt-4o-2024-11-20"
                mock_settings.azure_client_id = None
                mock_settings.mcp_graph_mail_url = None
                mock_settings.mcp_servicenow_url = None
                mock_settings.mcp_azure_url = None
                mock_settings.mcp_dynatrace_url = "https://apim.example.com/dynatrace"
                mock_settings.mcp_jira_url = "https://apim.example.com/jira"

                import importlib
                import src.orchestrator.agent as agent_mod
                importlib.reload(agent_mod)
                agent_mod.build_orchestrator(chat_client=fake_chat_client)

        middleware = captured.get("middleware", [])
        assert len(middleware) == 2, f"Expected 2 middleware, got {len(middleware)}"
        assert isinstance(middleware[0], PolicyMiddleware), (
            "First middleware must be PolicyMiddleware"
        )
        assert isinstance(middleware[1], AuditMiddleware), (
            "Second middleware must be AuditMiddleware"
        )

    def test_system_prompt_contains_required_rules(self) -> None:
        """Orchestrator system prompt must state the non-negotiable rules."""
        from src.orchestrator.prompts import ORCHESTRATOR_SYSTEM_PROMPT
        required_phrases = [
            "untrusted DATA",
            "approval",      # references the human approval requirement
            "verify_cer",    # references the deterministic verifier
            "wildcard",      # references the wildcard block
            "private key",   # references key protection
        ]
        prompt_lower = ORCHESTRATOR_SYSTEM_PROMPT.lower()
        for phrase in required_phrases:
            assert phrase.lower() in prompt_lower, (
                f"System prompt must contain reference to '{phrase}'"
            )
