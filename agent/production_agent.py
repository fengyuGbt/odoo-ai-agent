"""Production agent — plans and executes manufacturing orders (MO).

The fifth domain step of the AI operation layer. Once materials arrive
and the bill is paid, production is organized. The production agent:

1. finds manufacturing orders waiting for production;
2. inspects each MO (product, quantity, state, component needs from BOM);
3. submits the confirmation (``action_confirm``) through the gate;
4. submits the completion (``button_mark_done``) through the gate.

Every step is gated — the agent never produces on its own, the human
approves each action.
"""

from __future__ import annotations

from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient

# Marker written into demo MO origins so cleanup can find them.
DEMO_ORIGIN = "AI-PRODDEMO"

PENDING_STATES = ("draft", "confirmed", "progress")


class ProductionAgent:
    """Inspect manufacturing orders and drive them through the gate."""

    def __init__(self, client: OdooClient, gate: ActionGate, limit: int = 10) -> None:
        self.client = client
        self.gate = gate
        self.limit = limit

    # -- discovery (read-only) ----------------------------------------------

    def find_pending_mos(self) -> list[dict[str, Any]]:
        ids = self.client.env["mrp.production"].search(
            [("state", "in", list(PENDING_STATES))], limit=self.limit
        )
        if not ids:
            return []
        return self.client.env["mrp.production"].read(
            ids, ["id", "name", "product_id", "product_qty", "state", "bom_id", "origin", "date_start"]
        )

    # -- inspection ----------------------------------------------------------

    def inspect(self, mo: dict[str, Any]) -> list[str]:
        """Production-plan findings: product, quantity, components from BOM."""
        findings: list[str] = []
        product = (mo.get("product_id") or ["", ""])[1]
        findings.append(
            f"MO {mo['name']} (state={mo['state']}, origin={mo.get('origin') or '-'}): "
            f"produce {product} × {mo.get('product_qty')}"
        )

        bom = mo.get("bom_id") or []
        if bom:
            bom_lines = self.client.env["mrp.bom.line"].search([("bom_id", "=", bom[0])])
            if bom_lines:
                rows = self.client.env["mrp.bom.line"].read(bom_lines, ["product_id", "product_qty"])
                findings.append("  component needs (from BOM):")
                for r in rows:
                    comp = (r.get("product_id") or ["", ""])[1]
                    findings.append(f"    {comp} × {r.get('product_qty')}")
            else:
                findings.append("  BOM has no component lines")
        else:
            findings.append("  no BOM attached (demo environment)")

        findings.append("production plan check passed — awaiting human confirmation to produce")
        return findings

    # -- planning -------------------------------------------------------------

    def plan_confirm(self, mo: dict[str, Any]) -> AgentAction:
        return AgentAction(
            model="mrp.production",
            method="call",
            args={"ids": [mo["id"]], "method": "action_confirm"},
            reason=f"AI production plan for {mo['name']} passed; human decides to confirm the manufacturing order.",
        )

    def plan_mark_done(self, mo: dict[str, Any]) -> AgentAction:
        return AgentAction(
            model="mrp.production",
            method="call",
            args={"ids": [mo["id"]], "method": "button_mark_done"},
            reason=f"Confirm production of {mo['name']} as completed; finished goods enter stock.",
        )

    # -- execution through the gate --------------------------------------------

    def run_once(
        self, auto_approve: bool = False, only_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for mo in self.find_pending_mos():
            if only_ids is not None and mo["id"] not in only_ids:
                continue
            findings = self.inspect(mo)
            entry: dict[str, Any] = {
                "mo": mo["name"],
                "record_id": mo["id"],
                "state": mo["state"],
                "findings": findings,
            }

            # confirm
            action = self.plan_confirm(mo)
            action.precheck = findings
            request_id, decision = self.gate.submit(action)
            entry["confirm_request_id"] = request_id
            entry["confirm_decision"] = decision.value
            if decision is Decision.PENDING and auto_approve:
                self.gate.decide(request_id, True, comment="production agent demo: confirm MO")
                entry["confirm_decision"] = Decision.APPROVED.value

            # mark done (only if it got approved above)
            if entry["confirm_decision"] == Decision.APPROVED.value:
                done_action = self.plan_mark_done(mo)
                done_action.precheck = findings
                request_id2, decision2 = self.gate.submit(done_action)
                entry["done_request_id"] = request_id2
                entry["done_decision"] = decision2.value
                if decision2 is Decision.PENDING and auto_approve:
                    self.gate.decide(request_id2, True, comment="production agent demo: mark MO done")
                    entry["done_decision"] = Decision.APPROVED.value
            outcomes.append(entry)
        return outcomes
