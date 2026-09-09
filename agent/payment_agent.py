"""Payment agent — turns a received purchase order into a paid supplier bill.

The fourth domain step of the AI operation layer. Once goods are received
("到货完成后通知财务给供应商付款"), the payment agent:

1. finds purchase orders that are confirmed and fully received;
2. creates the vendor bill from the purchase order (``action_create_invoice``);
3. posts the bill (``action_post``);
4. creates an outbound payment for the bill (``account.payment``, draft);
5. posts the payment (``action_post``).

Every step is submitted through the gate — the agent never moves money on
its own, the human approves each action.
"""

from __future__ import annotations

from typing import Any

from agent.gate import ActionGate, AgentAction
from agent.odoo_client import OdooClient

# Marker put in the demo purchase order note so cleanup can find it.
DEMO_REF_PREFIX = "AI-PAYDEMO"


class PaymentAgent:
    """Create and post vendor bills and supplier payments through the gate."""

    def __init__(self, client: OdooClient, gate: ActionGate, limit: int = 10) -> None:
        self.client = client
        self.gate = gate
        self.limit = limit

    # -- discovery (read-only) ----------------------------------------------

    def find_received_pos(self) -> list[dict[str, Any]]:
        """Purchase orders confirmed and fully received (a done inbound picking)."""
        ids = self.client.env["purchase.order"].search(
            [("state", "=", "purchase")], limit=self.limit
        )
        if not ids:
            return []
        rows = self.client.env["purchase.order"].read(
            ids, ["id", "name", "partner_id", "amount_total", "invoice_status", "note"]
        )
        ready: list[dict[str, Any]] = []
        for po in rows:
            pickings = self.client.env["stock.picking"].search(
                [("origin", "=", po["name"]), ("state", "=", "done")]
            )
            if pickings:
                ready.append(po)
        return ready

    # -- bill creation & posting ----------------------------------------------

    def create_bill(self, po_id: int) -> AgentAction:
        return AgentAction(
            model="purchase.order",
            method="call",
            args={"ids": [po_id], "method": "action_create_invoice"},
            reason=f"Goods received for purchase order — propose creating the vendor bill.",
        )

    def find_bill(self, po_name: str) -> int | None:
        ids = self.client.env["account.move"].search(
            [
                ("invoice_origin", "=", po_name),
                ("move_type", "=", "in_invoice"),
            ]
        )
        return ids[0] if ids else None

    def post_bill(self, bill_id: int, bill_name: str) -> AgentAction:
        return AgentAction(
            model="account.move",
            method="call",
            args={"ids": [bill_id], "method": "action_post"},
            reason=f"Post vendor bill {bill_name} so it becomes payable.",
        )

    # -- payment creation & posting -------------------------------------------

    def _outbound_payment_method_line(self) -> int | None:
        journals = self.client.env["account.journal"].search(
            [("type", "=", "bank")], limit=1
        )
        if not journals:
            journals = self.client.env["account.journal"].search(
                [("type", "=", "cash")], limit=1
            )
        if not journals:
            return None
        journal = journals[0]
        lines = self.client.env["account.payment.method.line"].search(
            [("journal_id", "=", journal), ("payment_type", "=", "outbound")], limit=1
        )
        return lines[0] if lines else None

    def create_payment(self, bill: dict[str, Any]) -> AgentAction:
        """Plan an outbound payment for the bill as a gated creation."""
        method_line = self._outbound_payment_method_line()
        partner = bill.get("partner_id") or [False, ""]
        values: dict[str, Any] = {
            "payment_type": "outbound",
            "partner_type": "supplier",
            "partner_id": partner[0],
            "amount": bill.get("amount_total") or 0.0,
            "payment_reference": f"{DEMO_REF_PREFIX}-{bill['name']}",
        }
        if method_line:
            values["payment_method_line_id"] = method_line
        return AgentAction(
            model="account.payment",
            method="create",
            args={"values": values},
            reason=(
                f"Vendor bill {bill['name']} (total {bill.get('amount_total')}) "
                "is posted — propose the supplier payment."
            ),
        )

    def find_payment(self, bill_name: str) -> int | None:
        ids = self.client.env["account.payment"].search(
            [("payment_reference", "=", f"{DEMO_REF_PREFIX}-{bill_name}")]
        )
        return ids[0] if ids else None

    def post_payment(self, payment_id: int, payment_name: str) -> AgentAction:
        return AgentAction(
            model="account.payment",
            method="call",
            args={"ids": [payment_id], "method": "action_post"},
            reason=f"Post payment {payment_name} to settle the supplier bill.",
        )

    # -- convenience: run the whole bill+payment chain for one PO ------------

    def run_once(self, po_id: int, auto_approve: bool = False) -> list[dict[str, Any]]:
        """Create + post bill, create + post payment — every step gated."""
        from agent.gate import Decision

        steps: list[dict[str, Any]] = []
        po = self.client.env["purchase.order"].read([po_id], ["name"])[0]
        po_name = po["name"]

        def do(action: AgentAction, label: str) -> dict[str, Any]:
            request_id, decision = self.gate.submit(action)
            out = {"label": label, "request_id": request_id, "decision": decision.value}
            if decision is Decision.PENDING and auto_approve:
                result = self.gate.decide(request_id, True, comment=f"payment agent demo: {label}")
                out["decision"] = Decision.APPROVED.value
                out["result"] = result
            return out

        steps.append(do(self.create_bill(po_id), "create bill"))
        bill_id = self.find_bill(po_name)
        if bill_id is None:
            return steps
        bill_rows = self.client.env["account.move"].read([bill_id], ["name", "amount_total", "partner_id", "state", "invoice_date"])
        bill = bill_rows[0]
        if not bill.get("invoice_date"):
            from datetime import date
            steps.append(
                do(
                    AgentAction(
                        model="account.move",
                        method="write",
                        args={"ids": [bill_id], "values": {"invoice_date": date.today().isoformat()}},
                        reason=f"Set bill date on {bill['name']} — required before posting.",
                    ),
                    "set bill date",
                )
            )
        steps.append(do(self.post_bill(bill_id, bill["name"]), "post bill"))

        steps.append(do(self.create_payment(bill), "create payment"))
        payment_id = self.find_payment(bill["name"])
        if payment_id is not None:
            payment = self.client.env["account.payment"].read([payment_id], ["name", "state"])[0]
            steps.append(do(self.post_payment(payment_id, payment["name"]), "post payment"))
        return steps
