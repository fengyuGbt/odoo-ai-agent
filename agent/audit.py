"""Audit logging for every agent action.

Every action the AI agent submits — whether executed, rejected, or still
pending human approval — is appended to a JSONL audit trail. This is the
"全程留痕" (full traceability) requirement of the project: whoever reviews
the system later can reconstruct exactly what the AI tried to do, why, and
what happened to it.

In a later iteration the audit trail can also be written back into Odoo
(an ``agent.audit.log`` model) so it lives inside the ERP itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditLog:
    """Append-only JSONL audit log."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        """Append one event to the audit trail."""
        payload = {"ts": _now_iso(), **event}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_all(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the last *limit* events, most recent first."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:]][::-1]
