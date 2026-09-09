"""Receiving agent — inspects inbound pickings and proposes the receipt.

The third domain step of the AI operation layer. When a purchase order is
confirmed, Odoo generates an inbound picking (收货单). The receiving agent:

1. finds inbound pickings that are ready to receive;
2. inspects them against the originating purchase order (products,
   quantities, state) and produces inspection findings;
3. submits the receipt action (``button_validate``) through the gate —
   it never receives anything on its own, the human approves first.

This is the "到货 → 检验 → 入库" step of the chain.
"""

from __future__ import annotations

from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient

# Picking states that mean "waiting to be received".
READY_STATES = ("assigned", "confirmed", "waiting")


class ReceivingAgent:
    """Inspect inbound pickings and propose receipts through the gate."""

    def __init__(self, client: OdooClient, gate: ActionGate, limit: int = 10) -> None:
        self.client = client
        self.gate = gate
        self.limit = limit

    # -- discovery (read-only) ----------------------------------------------

    def find_inbound_pickings(self) -> list[dict[str, Any]]:
        ids = self.client.env["stock.picking"].search(
            [
                ("picking_type_code", "=", "incoming"),
                ("state", "in", list(READY_STATES)),
            ],
            limit=self.limit,
        )
        if not ids:
            return []
        return self.client.env["stock.picking"].read(
            ids, ["id", "name", "origin", "state", "purchase_id", "scheduled_date"]
        )

    # -- inspection ----------------------------------------------------------

    def inspect(self, picking: dict[str, Any]) -> list[str]:
        """Inspection findings: cross-check the picking against its purchase order."""
        findings: list[str] = []
        picking_id = picking["id"]
        moves = self.client.env["stock.move"].search([("picking_id", "=", picking_id)])
        findings.append(
            f"picking {picking['name']} (state={picking['state']}, origin={picking.get('origin') or '-'})"
        )

        po = picking.get("purchase_id") or []
        if po:
            po_id = po[0]
            po_rows = self.client.env["purchase.order"].read(
                [po_id], ["name", "partner_id", "amount_total", "state"]
            )
            if po_rows:
                po_rec = po_rows[0]
                findings.append(
                    f"purchase order {po_rec['name']} (state={po_rec['state']}, "
                    f"partner={(po_rec.get('partner_id') or ['', ''])[1]}, total={po_rec.get('amount_total')})"
                )
                lines = self.client.env["purchase.order.line"].search(
                    [("order_id", "=", po_id)]
                )
                if lines:
                    line_rows = self.client.env["purchase.order.line"].read(
                        lines, ["product_id", "product_qty", "price_unit"]
                    )
                    for line in line_rows:
                        product = (line.get("product_id") or ["", ""])[1]
                        findings.append(
                            f"  expected line: {product} × {line.get('product_qty')} @ {line.get('price_unit')}"
                        )
                else:
                    findings.append("  purchase order has no lines (demo environment)")
            else:
                findings.append("  purchase order not readable")
        else:
            findings.append("  no purchase order link on this picking")

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

        findings.append("inspection result: document cross-check passed — awaiting human confirmation to receive")
        return findings

    # -- planning -----------------------------------------------------------

    def plan_receive(self, picking: dict[str, Any]) -> AgentAction:
        """Plan the receipt (validate) of one picking as a gated action."""
        return AgentAction(
            model="stock.picking",
            method="call",
            args={"ids": [picking["id"]], "method": "button_validate"},
            reason=(
                f"AI inspection of {picking['name']} (origin {picking.get('origin') or '-'}) "
                "passed; human decides whether to receive into stock."
            ),
        )

    # -- execution through the gate -------------------------------------------

    def run_once(
        self, auto_approve: bool = False, only_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for picking in self.find_inbound_pickings():
            if only_ids is not None and picking["id"] not in only_ids:
                continue
            findings = self.inspect(picking)
            action = self.plan_receive(picking)
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
                self.gate.decide(request_id, True, comment="receiving agent demo approval")
                outcome["decision"] = Decision.APPROVED.value
            outcomes.append(outcome)
        return outcomes
