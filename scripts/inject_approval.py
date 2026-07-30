"""Simulate a PD approval or rejection for a running workflow.

Usage:
    python -m scripts.inject_approval --workflow-id wf_2026-07-28_api.prod.test-domain.com_7f3a
    python -m scripts.inject_approval --workflow-id wf_... --decision REJECTED --reason "CN mismatch"
    python -m scripts.inject_approval --workflow-id wf_... --env uat

Environment variables:
    FUNC_HOST   Base URL (overrides --env)
    FUNC_KEY    Function App host key
    APPROVER    Approver email (default: test-pd@test-domain.com)
"""
from __future__ import annotations

import argparse
import os
import sys

from scripts._client import abort, make_client


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject a PD approval decision.")
    parser.add_argument("--workflow-id", required=True, dest="workflow_id")
    parser.add_argument("--thread-id", dest="thread_id", help="Defaults to workflow-id")
    parser.add_argument("--decision", choices=["APPROVED", "REJECTED"], default="APPROVED")
    parser.add_argument("--reason", default="Injected by scripts.inject_approval for testing.")
    parser.add_argument("--approver", default=os.environ.get("APPROVER", "test-pd@test-domain.com"))
    parser.add_argument("--env", choices=["local", "dev", "uat", "prod"], default=None)
    args = parser.parse_args()

    payload = {
        "thread_id": args.thread_id or args.workflow_id,
        "workflow_id": args.workflow_id,
        "decision": args.decision,
        "approver": args.approver,
        "reasoning": args.reason,
    }

    with make_client(args.env) as client:
        resp = client.post("/api/approval-callback", json=payload)

    if resp.status_code == 202:
        print(f"Accepted  decision={args.decision}  workflow_id={args.workflow_id}")
    else:
        abort(f"HTTP {resp.status_code}: {resp.text}")


if __name__ == "__main__":
    main()
    sys.exit(0)
