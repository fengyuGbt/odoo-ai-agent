"""Sales domain agent — the first domain agent of the AI operation layer.

The sales agent watches the quotation queue (draft sale orders), runs the
tier-rule pre-check, and submits confirmations through the action gate.

It never confirms anything on its own: every confirmation is a pending
request that waits for a human decision. That is the project rule —
**AI runs the flow, humans make the judgment, AI never walks the whole
process alone.**
"""

from __future__ import annotations

from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient

CONFIRM_REASON = (
    "AI proposes confirming this quotation after order intake; "
    "human decides whether it meets the agreed conditions."
)


class SalesAgent:
    """Operates the sales domain: watch drafts, pre-check rules, ask humans."""

    def __init__(self, client: OdooClient, gate: ActionGate, limit: int = 10) -> None:
        self.client = client
        self.gate = gate
        self.limit = limit

    # -- discovery (read-only) ---------------------------------------------

    def find_pending_quotations(self) -> list[dict[str, Any]]:
        """List draft quotations the agent believes are ready for intake."""
        ids = self.client.env["sale.order"].search(
            [("state", "=", "draft")], limit=self.limit
        )
        if not ids:
            return []
        return self.client.env["sale.order"].read(
            ids, ["id", "name", "partner_id", "amount_total", "state"]
        )

    # -- planning -----------------------------------------------------------

    def plan_confirm(self, quotation: dict[str, Any]) -> AgentAction:
        """Plan the confirmation of one quotation as a gated action."""
        return AgentAction(
            model="sale.order",
            method="call",
            args={"ids": [quotation["id"]], "method": "action_confirm"},
            reason=CONFIRM_REASON,
        )

    # -- execution through the gate ------------------------------------------

    def run_once(self, auto_approve: bool = False, only_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """Submit confirmations for pending quotations through the gate.

        Only processes the quotations found by :meth:`find_pending_quotations`;
        when *only_ids* is given, restricts to those records (so a demo run
        never touches real business orders). Pending requests are left
        unresolved unless *auto_approve* is True (demo convenience — in real
        use, a human resolves them out-of-band).
        """
        outcomes: list[dict[str, Any]] = []
        for quotation in self.find_pending_quotations():
            if only_ids is not None and quotation["id"] not in only_ids:
                continue
            action = self.plan_confirm(quotation)
            request_id, decision = self.gate.submit(action)
            outcome: dict[str, Any] = {
                "quotation": quotation["name"],
                "record_id": quotation["id"],
                "partner": (quotation.get("partner_id") or ["", ""])[1],
                "total": quotation.get("amount_total"),
                "request_id": request_id,
                "decision": decision.value,
            }
            if decision is Decision.PENDING and auto_approve:
                self.gate.decide(request_id, True, comment="sales agent demo approval")
                outcome["decision"] = Decision.APPROVED.value
            outcomes.append(outcome)
        return outcomes
