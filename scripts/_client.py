"""Shared HTTP client and endpoint resolution for ASCRA live testing scripts."""
from __future__ import annotations

import os
import sys

import httpx

_ENV_HOSTS = {
    "local": "http://localhost:7071",
    "dev": "https://ssl-renewal-func-dev.azurewebsites.net",
    "uat": "https://ssl-renewal-func-uat.azurewebsites.net",
    "prod": "https://ssl-renewal-func-prod.azurewebsites.net",
}


def get_base_url(env: str | None = None) -> str:
    """Resolve Function App base URL from --env flag or FUNC_HOST env var."""
    if env and env in _ENV_HOSTS:
        return _ENV_HOSTS[env]
    host = os.environ.get("FUNC_HOST", "")
    if host:
        return host.rstrip("/")
    return _ENV_HOSTS["local"]


def make_client(env: str | None = None, timeout: float = 30.0) -> httpx.Client:
    """Return a synchronous httpx.Client pointed at the correct environment."""
    base_url = get_base_url(env)
    func_key = os.environ.get("FUNC_KEY", "")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if func_key:
        headers["x-functions-key"] = func_key
    return httpx.Client(base_url=base_url, headers=headers, timeout=timeout)


def abort(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)
