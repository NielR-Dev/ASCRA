"""Deploy Logic Apps workflow definitions to Azure.

Called by the CI/CD deploy job:
    python -m scripts.import_logic_apps

Required environment variables:
    AZURE_SUBSCRIPTION_ID
    AZURE_RESOURCE_GROUP

TODO: Implement using the Azure Management SDK (azure-mgmt-logic) to upload
      the Logic App Standard workflow JSON files from infra/logic-apps/.
      See: https://learn.microsoft.com/en-us/rest/api/logic/workflows/create-or-update
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def main() -> int:
    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "")

    if not subscription_id or not resource_group:
        logger.warning(
            "import_logic_apps: AZURE_SUBSCRIPTION_ID or AZURE_RESOURCE_GROUP not set — "
            "skipping Logic Apps import (stub)."
        )
        return 0

    logger.info(
        "import_logic_apps: subscription=%s rg=%s — stub, no-op.",
        subscription_id,
        resource_group,
    )
    # TODO: iterate infra/logic-apps/*.json and call azure-mgmt-logic to deploy each workflow
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
