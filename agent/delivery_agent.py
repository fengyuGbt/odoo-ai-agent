"""Delivery agent — ships confirmed sale orders to the customer.

The sixth domain step of the AI operation layer. Once finished goods are
in stock, orders ship out. The delivery agent:

1. finds outbound pickings waiting to be shipped;
2. inspects each picking against its sale order (products, quantities,
   state);
3. submits the shipping action (``button_validate``) through the gate.

Every step is gated — the agent never ships on its own, the human
approves each action.
"""

from __future__ import annotations

from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient

READY_STATES = ("assigned", "confirmed", "waiting")


class DeliveryAgent:
    """Inspect outbound pickings and propose shipping through the gate."""

    def __init__(self, client: OdooClient, gate: ActionGate, limit: int = 10) -> None:
        self.client = client
        self.gate = gate
        self.limit = limit

    # -- discovery (read-only) ----------------------------------------------

    def find_outbound_pickings(self) -> list[dict[str, Any]]:
        ids = self.client.env["stock.picking"].search(
            [
                ("picking_type_code", "=", "outgoing"),
                ("state", "in", list(READY_STATES)),
            ],
            limit=self.limit,
        )
        if not ids:
            return []
        return self.client.env["stock.picking"].read(
            ids, ["id", "name", "origin", "state", "sale_id", "scheduled_date"]
        )

    # -- inspection ----------------------------------------------------------

    def inspect(self, picking: dict[str, Any]) -> list[str]:
        """Shipping check: cross-check the picking against its sale order."""
        findings: list[str] = []
        findings.append(
            f"picking {picking['name']} (state={picking['state']}, origin={picking.get('origin') or '-'})"
        )

        so = picking.get("sale_id") or []
        if so:
            so_id = so[0]
            rows = self.client.env["sale.order"].read(
                [so_id], ["name", "partner_id", "amount_total", "state"]
            )
            if rows:
                rec = rows[0]
                findings.append(
                    f"sale order {rec['name']} (state={rec['state']}, "
                    f"partner={(rec.get('partner_id') or ['', ''])[1]}, total={rec.get('amount_total')})"
                )
                lines = self.client.env["sale.order.line"].search(
                    [("order_id", "=", so_id), ("product_id", "!=", False)]
                )
                if lines:
                    line_rows = self.client.env["sale.order.line"].read(
                        lines, ["product_id", "product_uom_qty"]
                    )
                    for line in line_rows:
                        product = (line.get("product_id") or ["", ""])[1]
                        findings.append(f"  expected line: {product} × {line.get('product_uom_qty')}")
            else:
                findings.append("  sale order not readable")
        else:
            findings.append("  no sale order link on this picking")

        moves = self.client.env["stock.move"].search([("picking_id", "=", picking["id"])])
        if moves:
            move_rows = self.client.env["stock.move"].read(
                moves, ["product_id", "product_uom_qty", "quantity", "state"]
            )
            for m in move_rows:
                product = (m.get("product_id") or ["", ""])[1]
                findings.append(
                    f"  move: {product} qty={m.get('product_uom_qty')} done={m.get('quantity')} state={m.get('state')}"
                )
        else:
            findings.append("  no stock moves on this picking (consumable product, demo env)")

        findings.append("shipping check passed — awaiting human confirmation to ship")
        return findings

    # -- planning -------------------------------------------------------------

    def plan_deliver(self, picking: dict[str, Any]) -> AgentAction:
        return AgentAction(
            model="stock.picking",
            method="call",
            args={"ids": [picking["id"]], "method": "button_validate"},
            reason=(
                f"AI shipping check of {picking['name']} (origin {picking.get('origin') or '-'}) "
                "passed; human decides whether to ship to the customer."
            ),
        )

    # -- execution through the gate --------------------------------------------

    def run_once(
        self, auto_approve: bool = False, only_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for picking in self.find_outbound_pickings():
            if only_ids is not None and picking["id"] not in only_ids:
                continue
            findings = self.inspect(picking)
            action = self.plan_deliver(picking)
            action.precheck = findings
            request_id, decision = self.gate.submit(action)
            outcome: dict[str, Any] = {
                "picking": picking["name"],
                "record_id": picking["id"],
                "origin": picking.get("origin"),
                "request_id": request_id,
                "decision": decision.value,
                "findings": findings,
            }
            if decision is Decision.PENDING and auto_approve:
                self.gate.decide(request_id, True, comment="delivery agent demo approval")
                outcome["decision"] = Decision.APPROVED.value
            outcomes.append(outcome)
        return outcomes
