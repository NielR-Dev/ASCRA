"""Query and display the current status of a renewal workflow.

Usage:
    python -m scripts.check_status --workflow-id wf_2026-07-28_api.prod.test-domain.com_7f3a
    python -m scripts.check_status --cn api.prod.test-domain.com
    python -m scripts.check_status --cn api.prod.test-domain.com --env prod
    python -m scripts.check_status --health

Environment variables:
    FUNC_HOST   Base URL (overrides --env)
    FUNC_KEY    Function App host key
"""
from __future__ import annotations

import argparse
import json
import sys

from scripts._client import abort, make_client


def _print_status(body: dict) -> None:
    print(f"workflow_id : {body.get('workflow_id', '-')}")
    print(f"state       : {body.get('state', '-')}")
    print(f"cn          : {body.get('cn', '-')}")
    print(f"san         : {', '.join(body.get('san') or [])}")
    print(f"app         : {body.get('owning_application', '-')}")
    print(f"jira_ticket : {body.get('jira_ticket', '-')}")
    print(f"chg_number  : {body.get('chg_number', '-')}")
    print(f"updated_at  : {body.get('updated_at', '-')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check ASCRA workflow status.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workflow-id", dest="workflow_id")
    group.add_argument("--cn", help="Certificate common name")
    group.add_argument("--health", action="store_true", help="Health check only (no params)")
    parser.add_argument("--env", choices=["local", "dev", "uat", "prod"], default=None)
    parser.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")
    args = parser.parse_args()

    with make_client(args.env) as client:
        if args.health:
            resp = client.get("/api/status")
        elif args.workflow_id:
            resp = client.get("/api/status", params={"workflow_id": args.workflow_id})
        else:
            resp = client.get("/api/status", params={"cn": args.cn})

    if resp.status_code == 200:
        body = resp.json()
        if args.output_json:
            print(json.dumps(body, indent=2))
        else:
            _print_status(body)
    elif resp.status_code == 404:
        print("Not found.")
        sys.exit(1)
    else:
        abort(f"HTTP {resp.status_code}: {resp.text}")


if __name__ == "__main__":
    main()
    sys.exit(0)
