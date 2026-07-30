"""Environment-driven settings for the SSL Renewal Agent (T01).

All configuration is read from environment variables or Key Vault references.
NO hard-coded endpoints, hostnames, credentials, or secrets (G8).

Required variables raise a clear error if absent at startup — fail fast.
All optional variables have documented defaults that match the specification.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent settings — env-driven, never hard-coded.

    Variable names are upper-cased by pydantic_settings automatically.
    Add a `.env` file for local development (listed in .gitignore).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----------------------------------------------------------------
    # Azure AI Foundry / OpenAI (required)
    # ----------------------------------------------------------------
    foundry_project_endpoint: str = Field(
        ...,
        description="Azure AI Foundry project endpoint URL (e.g. https://project.api.azureml.ms)",
    )
    azure_openai_deployment: str = Field(
        default="gpt-4o-2024-11-20",
        description="Azure OpenAI chat deployment name.",
    )

    # ----------------------------------------------------------------
    # Azure Managed Identity (optional — falls back to DefaultAzureCredential chain)
    # ----------------------------------------------------------------
    azure_client_id: str | None = Field(
        default=None,
        description="Managed Identity client ID for DefaultAzureCredential. "
                    "Leave unset to use the system-assigned MI.",
    )

    # ----------------------------------------------------------------
    # Azure Key Vault (required)
    # ----------------------------------------------------------------
    key_vault_uri: str = Field(
        ...,
        description="Azure Key Vault URI (Managed HSM), e.g. https://kv-ssl-hsm.vault.azure.net",
    )

    # ----------------------------------------------------------------
    # Azure Cosmos DB (required)
    # ----------------------------------------------------------------
    cosmos_endpoint: str = Field(
        ...,
        description="Cosmos DB account endpoint, e.g. https://acct.documents.azure.com:443/",
    )
    cosmos_database: str = Field(
        default="ssl_renewal",
        description="Cosmos DB database name.",
    )

    # ----------------------------------------------------------------
    # Azure Blob Storage (required for CER WORM)
    # ----------------------------------------------------------------
    blob_account_url: str = Field(
        ...,
        description="Blob account URL, e.g. https://acct.blob.core.windows.net",
    )

    # ----------------------------------------------------------------
    # MCP tool URLs (required for production; optional for tests with stubs)
    # ----------------------------------------------------------------
    mcp_graph_mail_url: str | None = Field(
        default=None,
        description="Foundry-hosted graph_mail MCP URL (defaults to foundry_project_endpoint).",
    )
    mcp_servicenow_url: str | None = Field(
        default=None,
        description="Foundry-hosted servicenow MCP URL.",
    )
    mcp_azure_url: str | None = Field(
        default=None,
        description="Foundry-hosted azure MCP URL.",
    )
    mcp_dynatrace_url: str = Field(
        default="",
        description="APIM-fronted Dynatrace MCP URL (required in production).",
    )
    mcp_jira_url: str = Field(
        default="",
        description="APIM-fronted Jira MCP URL (required in production).",
    )

    # ----------------------------------------------------------------
    # HITL / Approval (G1)
    # ----------------------------------------------------------------
    approval_timeout_hours: int = Field(
        default=48,
        ge=1,
        description="Hours before unanswered approval auto-escalates to PD delegate.",
    )
    pd_approver_email: str = Field(
        default="",
        description="Email address of the Product Director (primary approver).",
    )
    pd_delegate_email: str = Field(
        default="",
        description="Email address of the PD delegate (escalation target after timeout).",
    )

    # ----------------------------------------------------------------
    # Certificate verification (G2)
    # ----------------------------------------------------------------
    cert_min_valid_days: int = Field(
        default=365,
        ge=1,
        description="Minimum days of validity remaining for verify_cer to pass.",
    )

    # ----------------------------------------------------------------
    # Guardrail counters (G3)
    # ----------------------------------------------------------------
    max_consecutive_tool_errors: int = Field(
        default=2,
        ge=1,
        description="Number of consecutive tool errors before PolicyMiddleware halts and escalates (G3).",
    )

    # ----------------------------------------------------------------
    # Magentic retry sub-orchestration caps
    # ----------------------------------------------------------------
    magentic_max_rounds: int = Field(
        default=6,
        ge=1,
        description="Maximum retry rounds in the magentic diagnostic loop.",
    )
    magentic_max_escalations: int = Field(
        default=2,
        ge=1,
        description="Maximum ESCALATE_PD decisions before forcing FAIL_OPEN.",
    )

    # ----------------------------------------------------------------
    # Fleet-scale batch (FR-12, FR-13)
    # ----------------------------------------------------------------
    max_concurrent_renewals: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Maximum number of child renewal workflows running concurrently (semaphore).",
    )
    pki_rate_per_min: int = Field(
        default=10,
        ge=1,
        description="Maximum PKI emails per minute (rate limiter).",
    )
    jira_rate_per_min: int = Field(
        default=60,
        ge=1,
        description="Maximum Jira API calls per minute.",
    )
    snow_rate_per_min: int = Field(
        default=30,
        ge=1,
        description="Maximum ServiceNow API calls per minute.",
    )

    # ----------------------------------------------------------------
    # Slack integration (Direct mode — T20)
    # ----------------------------------------------------------------
    slack_signing_secret: str | None = Field(
        default=None,
        description="Slack app signing secret for request-signature verification (HMAC-SHA256). "
                    "Must be set in production; loaded from Key Vault reference (G8).",
    )
    slack_bot_token: str | None = Field(
        default=None,
        description="Slack bot OAuth token (for posting messages). KV reference (G8).",
    )

    # ----------------------------------------------------------------
    # PKI (required in production)
    # ----------------------------------------------------------------
    pki_mailbox: str = Field(
        default="",
        description="PKI team mailbox address to send CSR Request Forms to.",
    )
    pki_reply_wait_days: int = Field(
        default=5,
        ge=1,
        description="Business days to wait for PKI reply before escalation.",
    )

    # ----------------------------------------------------------------
    # Observability
    # ----------------------------------------------------------------
    applicationinsights_connection_string: str | None = Field(
        default=None,
        description="App Insights connection string for OpenTelemetry export.",
    )
    log_level: str = Field(
        default="INFO",
        description="Python logging level (DEBUG, INFO, WARNING, ERROR).",
    )

    # ----------------------------------------------------------------
    # Kill-switch (G3, RUNBOOK)
    # ----------------------------------------------------------------
    orchestrator_enabled: bool = Field(
        default=True,
        description="Feature flag: False disables the orchestrator trigger. "
                    "Set to False via environment/config for the kill-switch procedure.",
    )


# Module-level singleton — the sole entry point for all config access.
# Never instantiate Settings directly elsewhere; use `from src.config import settings`.
settings = Settings()
