"""Canonical fingerprinting for approved remediation actions."""

from __future__ import annotations

import hashlib
import json

from services.agent_runtime.app.action.models import ActionPlan


def action_fingerprint(action: ActionPlan) -> str:
    """Return a stable SHA-256 fingerprint for the complete ActionPlan."""
    payload = action.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
