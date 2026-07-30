"""Manually trigger an SSL certificate renewal workflow.

Usage:
    python -m scripts.trigger_renewal --cn api.prod.test-domain.com
    python -m scripts.trigger_renewal --cn api.prod.test-domain.com --san api.prod.test-domain.com api-int.prod.test-domain.com
    python -m scripts.trigger_renewal --cn api.prod.test-domain.com --env uat
    python -m scripts.trigger_renewal --fixture tests/fixtures/payloads/orchestrate_request.json

Environment variables:
    FUNC_HOST   Base URL (overrides --env)
    FUNC_KEY    Function App host key
"""
from __future__ import annotations

import argparse
import json
import sys

from scripts._client import abort, make_client


def _build_alert(args: argparse.Namespace) -> dict:
    san = args.san or [args.cn]
    return {
        "cn": args.cn,
        "san": san,
        "owning_application": args.app or "",
        "source": args.source,
        "problem_id": args.problem_id or "MANUAL-001",
        "received_at": "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger an ASCRA renewal workflow.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cn", help="Certificate common name")
    source.add_argument("--fixture", help="Path to an orchestrate_request.json fixture file")
    parser.add_argument("--san", nargs="+", help="Subject Alternative Names (default: [cn])")
    parser.add_argument("--app", help="Owning application name")
    parser.add_argument("--problem-id", dest="problem_id", help="Problem ID (default: MANUAL-001)")
    parser.add_argument("--source", default="manual", help="Alert source (default: manual)")
    parser.add_argument("--env", choices=["local", "dev", "uat", "prod"], default=None)
    args = parser.parse_args()

    if args.fixture:
        with open(args.fixture, encoding="utf-8") as f:
            body = json.load(f)
    else:
        body = {"alert": _build_alert(args)}

    with make_client(args.env) as client:
        resp = client.post("/api/orchestrate", json=body)

    if resp.status_code == 200:
        data = resp.json()
        print(f"Started  workflow_id={data.get('workflow_id')}  state={data.get('state')}")
        print(f"         correlation_id={data.get('correlation_id')}")
    else:
        abort(f"HTTP {resp.status_code}: {resp.text}")


if __name__ == "__main__":
    main()
    sys.exit(0)
