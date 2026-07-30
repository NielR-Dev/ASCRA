"""Tests for config.py (T01).

Covers:
  - Required variables raise a clear error when missing
  - Defaults match the specification (gpt-4o-2024-11-20, 48h approval timeout, 365d min validity)
  - Type validation works as expected
  - No hard-coded credentials in Settings
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch


class TestSettingsDefaults:
    def test_default_openai_deployment(self) -> None:
        """Default deployment must match spec: gpt-4o-2024-11-20."""
        env = {
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.com",
            "KEY_VAULT_URI": "https://kv.vault.azure.net",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from importlib import reload
            import src.config as cfg_module
            reload(cfg_module)
            settings = cfg_module.Settings()
            assert settings.azure_openai_deployment == "gpt-4o-2024-11-20"

    def test_default_approval_timeout_hours(self) -> None:
        """Default approval timeout must be 48 hours (spec)."""
        env = {
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.com",
            "KEY_VAULT_URI": "https://kv.vault.azure.net",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from src.config import Settings
            settings = Settings()
            assert settings.approval_timeout_hours == 48

    def test_default_cert_min_valid_days(self) -> None:
        """Default minimum validity must be 365 days (spec)."""
        env = {
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.com",
            "KEY_VAULT_URI": "https://kv.vault.azure.net",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from src.config import Settings
            settings = Settings()
            assert settings.cert_min_valid_days == 365

    def test_default_max_consecutive_tool_errors(self) -> None:
        """Default G3 halt threshold must be 2 (spec)."""
        env = {
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.com",
            "KEY_VAULT_URI": "https://kv.vault.azure.net",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from src.config import Settings
            settings = Settings()
            assert settings.max_consecutive_tool_errors == 2

    def test_default_cosmos_database(self) -> None:
        env = {
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.com",
            "KEY_VAULT_URI": "https://kv.vault.azure.net",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from src.config import Settings
            settings = Settings()
            assert settings.cosmos_database == "ssl_renewal"

    def test_orchestrator_enabled_default_true(self) -> None:
        """Kill-switch must be enabled by default."""
        env = {
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.com",
            "KEY_VAULT_URI": "https://kv.vault.azure.net",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from src.config import Settings
            settings = Settings()
            assert settings.orchestrator_enabled is True


class TestSettingsRequired:
    def test_missing_foundry_endpoint_raises(self) -> None:
        """Missing required var must raise a clear error (not silently use None)."""
        env = {
            "KEY_VAULT_URI": "https://kv.vault.azure.net",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from src.config import Settings
            import pydantic
            with pytest.raises((pydantic.ValidationError, Exception)):
                Settings()

    def test_missing_key_vault_uri_raises(self) -> None:
        env = {
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.com",
            "COSMOS_ENDPOINT": "https://acct.documents.azure.com:443/",
            "BLOB_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        }
        with patch.dict(os.environ, env, clear=True):
            from src.config import Settings
            import pydantic
            with pytest.raises((pydantic.ValidationError, Exception)):
                Settings()


class TestSettingsNoSecrets:
    def test_no_hard_coded_credentials_in_settings(self) -> None:
        """G8: verify Settings source does not contain any credential literals."""
        import inspect
        import src.config as cfg
        source = inspect.getsource(cfg)
        # Check for common secret patterns
        forbidden_patterns = [
            "password=",
            "secret=",
            "api_key=",
            "token=",
            "access_key=",
        ]
        # We allow these in docstrings/comments but not as assignments
        for pattern in forbidden_patterns:
            # Simple check: pattern followed by a non-empty quoted string
            import re
            matches = re.findall(rf'{pattern}["\'][^"\']+["\']', source, re.IGNORECASE)
            assert not matches, (
                f"Found potential hardcoded credential '{pattern}' in config.py: {matches}"
            )
