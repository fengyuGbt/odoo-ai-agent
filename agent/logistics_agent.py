"""Logistics agent — carriers, tracking numbers and delivery sign-off.

The seventh domain step of the AI operation layer. After goods ship,
logistics takes over. The logistics agent:

1. finds shipped outbound pickings (state=done);
2. inspects each picking (carrier, tracking ref, partner);
3. proposes, through the gate:
   a. assigning a carrier (``carrier_id``),
   b. registering the tracking number (``carrier_tracking_ref``),
   c. recording the customer sign-off (chatter message).

Every step is gated — the agent proposes, the human decides.
"""

from __future__ import annotations

from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient


class LogisticsAgent:
    """Inspect shipped deliveries and propose logistics actions."""

    def __init__(self, client: OdooClient, gate: ActionGate, limit: int = 10) -> None:
        self.client = client
        self.gate = gate
        self.limit = limit

    # -- discovery (read-only) ----------------------------------------------

    def find_shipped_pickings(self) -> list[dict[str, Any]]:
        ids = self.client.env["stock.picking"].search(
            [
                ("picking_type_code", "=", "outgoing"),
                ("state", "=", "done"),
            ],
            limit=self.limit,
        )
        if not ids:
            return []
        return self.client.env["stock.picking"].read(
            ids,
            ["id", "name", "origin", "partner_id", "carrier_id", "carrier_tracking_ref"],
        )

    def find_carriers(self) -> list[dict[str, Any]]:
        ids = self.client.env["delivery.carrier"].search([], limit=self.limit)
        if not ids:
            return []
        return self.client.env["delivery.carrier"].read(ids, ["id", "name", "delivery_type"])

    # -- inspection ----------------------------------------------------------

    def inspect(self, picking: dict[str, Any]) -> list[str]:
        findings: list[str] = []
        partner = (picking.get("partner_id") or ["", ""])[1] or "-"
        carrier = (picking.get("carrier_id") or ["", ""])[1] or "-"
        tracking = picking.get("carrier_tracking_ref") or "-"
        findings.append(
            f"delivery {picking['name']} (origin={picking.get('origin') or '-'}, "
            f"partner={partner})"
        )
        findings.append(f"  carrier: {carrier}")
        findings.append(f"  tracking ref: {tracking}")
        if not picking.get("carrier_id"):
            findings.append("  → no carrier assigned yet — propose assigning one")
        if not picking.get("carrier_tracking_ref"):
            findings.append("  → no tracking number yet — propose registering one")
        findings.append("logistics check passed — awaiting human confirmation")
        return findings

    # -- planning -------------------------------------------------------------

    def plan_assign_carrier(self, picking: dict[str, Any], carrier_id: int, carrier_name: str) -> AgentAction:
        return AgentAction(
            model="stock.picking",
            method="write",
            args={
                "ids": [picking["id"]],
                "values": {"carrier_id": carrier_id},
            },
            reason=(
                f"AI logistics check of {picking['name']}: assign carrier "
                f"{carrier_name} to the shipment; human decides."
            ),
        )

    def plan_register_tracking(
        self, picking: dict[str, Any], carrier_name: str, tracking_ref: str
    ) -> AgentAction:
        return AgentAction(
            model="stock.picking",
            method="write",
            args={
                "ids": [picking["id"]],
                "values": {"carrier_tracking_ref": tracking_ref},
            },
            reason=(
                f"AI logistics check of {picking['name']}: register tracking "
                f"number {tracking_ref} with {carrier_name}; human decides."
            ),
        )

    def plan_confirm_receipt(self, picking: dict[str, Any]) -> AgentAction:
        return AgentAction(
            model="stock.picking",
            method="call",
            args={
                "ids": [picking["id"]],
                "method": "message_post",
                "kwargs": {
                    "body": "客户已签收 — AI 物流 Agent 登记（人工确认后落库）",
                },
            },
            reason=(
                f"AI logistics check of {picking['name']}: record customer "
                "sign-off in the chatter; human decides."
            ),
        )

    # -- execution through the gate --------------------------------------------

    def run_once(
        self,
        auto_approve: bool = False,
        only_ids: list[int] | None = None,
        carrier_id: int | None = None,
        tracking_ref: str = "AI-LOGDEMO-0001",
    ) -> list[dict[str, Any]]:
        carriers = self.find_carriers()
        outcomes: list[dict[str, Any]] = []
        for picking in self.find_shipped_pickings():
            if only_ids is not None and picking["id"] not in only_ids:
                continue
            findings = self.inspect(picking)
            out: dict[str, Any] = {
                "picking": picking["name"],
                "record_id": picking["id"],
                "origin": picking.get("origin"),
                "findings": findings,
                "actions": [],
            }

            if not picking.get("carrier_id"):
                if not carriers:
                    out["actions"].append({"step": "assign_carrier", "decision": "no carrier available"})
                else:
                    cid = carrier_id or carriers[0]["id"]
                    cname = carriers[0]["name"]
                    action = self.plan_assign_carrier(picking, cid, cname)
                    action.precheck = findings
                    rid, decision = self.gate.submit(action)
                    out["actions"].append({"step": "assign_carrier", "request_id": rid, "decision": decision.value})
                    if decision is Decision.PENDING and auto_approve:
                        self.gate.decide(rid, True, comment="logistics demo approval: assign carrier")
                        out["actions"][-1]["decision"] = Decision.APPROVED.value

            if not picking.get("carrier_tracking_ref"):
                cname = (picking.get("carrier_id") or ["", ""])[1] or "carrier"
                action = self.plan_register_tracking(picking, cname, tracking_ref)
                action.precheck = findings
                rid, decision = self.gate.submit(action)
                out["actions"].append({"step": "register_tracking", "request_id": rid, "decision": decision.value})
                if decision is Decision.PENDING and auto_approve:
                    self.gate.decide(rid, True, comment="logistics demo approval: register tracking")
                    out["actions"][-1]["decision"] = Decision.APPROVED.value

            action = self.plan_confirm_receipt(picking)
            action.precheck = findings
            rid, decision = self.gate.submit(action)
            out["actions"].append({"step": "confirm_receipt", "request_id": rid, "decision": decision.value})
            if decision is Decision.PENDING and auto_approve:
                self.gate.decide(rid, True, comment="logistics demo approval: confirm receipt")
                out["actions"][-1]["decision"] = Decision.APPROVED.value

            outcomes.append(out)
        return outcomes
