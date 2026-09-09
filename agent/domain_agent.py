"""Generic domain agent for the AI operation layer.

A domain agent watches one business document queue (e.g. draft sale
orders, draft purchase orders), runs the tier-rule pre-check, and submits
the confirmation as a gated action. It never confirms anything on its own:
every confirmation is a pending request that waits for a human decision.

Specialized agents (sales, purchase, ...) only define the model, the
draft-state filter and the reason; the gated flow lives here once.
"""

from __future__ import annotations

from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient


class DomainAgent:
    """Watch a draft document queue and propose confirmations through the gate."""

    model: str = ""
    state_field: str = "state"
    draft_state: str = "draft"
    confirm_method: str = "action_confirm"
    read_fields: list[str] = ["id", "name"]
    reason_template: str = "AI proposes confirming this {model} after intake; human decides whether it meets the agreed conditions."

    def __init__(self, client: OdooClient, gate: ActionGate, limit: int = 10) -> None:
        self.client = client
        self.gate = gate
        self.limit = limit

    # -- discovery (read-only) ---------------------------------------------

    def find_pending(self, fields: list[str] | None = None) -> list[dict[str, Any]]:
        """List draft documents the agent believes are ready for intake."""
        ids = self.client.env[self.model].search(
            [(self.state_field, "=", self.draft_state)], limit=self.limit
        )
        if not ids:
            return []
        return self.client.env[self.model].read(ids, fields or self.read_fields)

    # -- planning -----------------------------------------------------------

    def plan_confirm(self, record: dict[str, Any]) -> AgentAction:
        """Plan the confirmation of one document as a gated action."""
        return AgentAction(
            model=self.model,
            method="call",
            args={"ids": [record["id"]], "method": self.confirm_method},
            reason=self.reason_template.format(model=self.model),
        )

    # -- execution through the gate ------------------------------------------

    def run_once(
        self, auto_approve: bool = False, only_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Submit confirmations for pending documents through the gate.

        Only processes the documents found by :meth:`find_pending`; when
        *only_ids* is given, restricts to those records (so a demo run never
        touches real business documents). Pending requests are left unresolved
        unless *auto_approve* is True (demo convenience — in real use, a
        human resolves them out-of-band).
        """
        outcomes: list[dict[str, Any]] = []
        for record in self.find_pending():
            if only_ids is not None and record["id"] not in only_ids:
                continue
            action = self.plan_confirm(record)
            request_id, decision = self.gate.submit(action)
            outcome: dict[str, Any] = {
                "document": record["name"],
                "record_id": record["id"],
                "request_id": request_id,
                "decision": decision.value,
            }
            # pass through any extra fields the agent reads (partner, total, ...)
            for key, value in record.items():
                if key not in ("id", "name"):
                    outcome[key] = value
            if decision is Decision.PENDING and auto_approve:
                self.gate.decide(request_id, True, comment="domain agent demo approval")
                outcome["decision"] = Decision.APPROVED.value
            outcomes.append(outcome)
        return outcomes
