"""Backend: approval and PKI reply callbacks (thin adapters).

These adapters handle inbound HTTP callbacks from Teams/Power Automate (approval)
and from Logic Apps (PKI CER attachment notification). They:
  1. Validate the inbound token / correlation binding.
  2. Route to the guarded core entrypoint (record_approval_decision or pki_reply Function).
  3. Return the appropriate HTTP response.

No business logic lives here. Security validation (Entra token, thread_id binding)
is handled by the underlying tool and Function.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ssl_renewal.interfaces.backend.callbacks")

# This module is intentionally thin — see src/functions/approval_callback/__init__.py
# and src/functions/pki_reply/__init__.py for the actual Function implementations.
# This file documents the adapter concept for the interaction-modes layer.
