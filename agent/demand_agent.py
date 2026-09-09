"""Demand agent — turns stock analysis into a gated purchase request.

In the end-to-end chain, once the sales agent confirms an order, the demand
agent checks the stock position of the sold products and — when there is a
shortfall — proposes a purchase order. The proposal is an ordinary gated
action: it stops at the gate and waits for a human decision, exactly like
every other write action.

This is the "AI 判断需求、人拍板采购" step of the AI operation layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient


class DemandAgent:
    """Analyse stock and propose purchases through the gate."""

    def __init__(self, client: OdooClient, gate: ActionGate) -> None:
        self.client = client
        self.gate = gate

    # -- analysis (read-only) ----------------------------------------------

    def available_qty(self, product_id: int) -> float:
        rows = self.client.env["product.product"].read(
            [product_id], ["qty_available"]
        )
        return float(rows[0].get("qty_available") or 0.0) if rows else 0.0

    def analyze(self, product_id: int, needed_qty: float) -> dict[str, Any]:
        available = self.available_qty(product_id)
        shortfall = max(0.0, float(needed_qty) - available)
        return {
            "product_id": product_id,
            "needed": float(needed_qty),
            "available": available,
            "shortfall": shortfall,
        }

    # -- planning -----------------------------------------------------------

    def plan_purchase(
        self,
        product_id: int,
        qty: float,
        partner_id: int,
        price_unit: float,
        date_planned: str,
        note: str = "",
    ) -> AgentAction:
        """Plan a purchase order covering the shortfall as a gated action."""
        return AgentAction(
            model="purchase.order",
            method="create",
            args={
                "values": {
                    "partner_id": partner_id,
                    "note": note,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": product_id,
                                "product_qty": qty,
                                "price_unit": price_unit,
                                "date_planned": date_planned,
                            },
                        )
                    ],
                }
            },
            reason=(
                f"AI stock analysis: needed {qty:g}, available "
                f"{self.available_qty(product_id):g} — proposing a purchase "
                "order; human decides whether the supplier terms are acceptable."
            ),
        )

    # -- execution through the gate ------------------------------------------

    def run(
        self,
        product_id: int,
        qty: float,
        partner_id: int,
        price_unit: float,
        date_planned: str,
        auto_approve: bool = False,
        note: str = "",
    ) -> dict[str, Any]:
        """Analyse stock and submit a purchase proposal through the gate.

        Returns the analysis plus the gate outcome. When the stock already
        covers the need, no purchase is proposed.
        """
        analysis = self.analyze(product_id, qty)
        if analysis["shortfall"] <= 0:
            return {"decision": "no_purchase_needed", **analysis}

        action = self.plan_purchase(
            product_id, analysis["shortfall"], partner_id, price_unit, date_planned, note=note
        )
        request_id, decision = self.gate.submit(action)
        outcome: dict[str, Any] = {
            "decision": "purchase_requested",
            "request_id": request_id,
            "gate_decision": decision.value,
            **analysis,
        }
        if decision is Decision.PENDING and auto_approve:
            self.gate.decide(request_id, True, comment="e2e demo: approve purchase proposal")
            outcome["gate_decision"] = Decision.APPROVED.value
        return outcome


def default_date_planned(days_ahead: int = 7) -> str:
    """ISO-ish datetime string for purchase order line date_planned."""
    return (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d %H:%M:%S")
