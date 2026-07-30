"""Run PromptFlow safety evaluations against the deployed ASCRA agent.

Called by the CI/CD deploy job:
    python -m scripts.run_promptflow_evals --env prod --fail-under 0.90

Required environment variables:
    FOUNDRY_PROJECT_ENDPOINT   Azure AI Foundry project endpoint

TODO: Implement using the Azure AI Foundry SDK (azure-ai-evaluation) to run
      the evaluation datasets defined in prompts/evals/ and assert that safety
      and groundedness scores meet the --fail-under threshold.
      See: https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/evaluate-sdk
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PromptFlow safety evals.")
    parser.add_argument("--env", choices=["dev", "uat", "prod"], default="prod")
    parser.add_argument("--fail-under", dest="fail_under", type=float, default=0.90)
    args = parser.parse_args()

    foundry_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not foundry_endpoint:
        logger.warning(
            "run_promptflow_evals: FOUNDRY_PROJECT_ENDPOINT not set — "
            "skipping PromptFlow evals (stub)."
        )
        return 0

    logger.info(
        "run_promptflow_evals: env=%s fail_under=%.2f endpoint=%s — stub, no-op.",
        args.env,
        args.fail_under,
        foundry_endpoint[:60],
    )
    # TODO: load eval datasets from prompts/evals/, instantiate Azure AI Foundry evaluators,
    #       run against deployed agent, assert aggregate score >= args.fail_under.
    #       Return exit code 1 if score falls below threshold.
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
