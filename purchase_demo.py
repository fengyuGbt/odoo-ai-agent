#!/usr/bin/env python3
"""Purchase agent demo: intake → tier pre-check → human approval → confirm.

The second domain agent (purchase) of the AI operation layer:

  scan     -- read-only: list draft purchase orders
  default  -- create one demo purchase order, submit confirmation, leave it
              PENDING (nothing is executed without the human)
  --approve -- full loop: create (approved) → confirm (approved) → verify
              state=purchase → cleanup (cancel+delete, approved) → audit summary

Usage:
    venv/bin/python purchase_demo.py [--scan]
    venv/bin/python purchase_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.purchase_agent import PurchaseAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
DEMO_NOTE = "AI-drafted (purchase agent demo)"


def find_demo_records(client: OdooClient) -> list[int]:
    return client.env["purchase.order"].search([("note", "like", "%purchase agent demo%")])


def create_demo_order(
    client: OdooClient, gate: ActionGate, partner_id: int, auto_approve: bool = False
) -> int | None:
    action = AgentAction(
        model="purchase.order",
        method="create",
        args={"values": {"partner_id": partner_id, "note": DEMO_NOTE}},
        reason="purchase agent demo: create a draft purchase order to demonstrate the intake flow",
    )
    request_id, decision = gate.submit(action)
    print(f"[demo] create purchase order -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("[demo] creation request left pending (run with --approve to execute)")
        return None
    result = gate.decide(request_id, True, comment="purchase demo: approve purchase order creation")
    print(f"[demo] human approved creation -> {result}")
    return result["id"] if isinstance(result, dict) else result


def confirm_via_agent(
    client: OdooClient, gate: ActionGate, auto_approve: bool, only_ids: list[int] | None = None
) -> None:
    agent = PurchaseAgent(client, gate, limit=10)
    print(f"\n[purchase-agent] scanning draft purchase orders ...")
    outcomes = agent.run_once(auto_approve=auto_approve, only_ids=only_ids)
    if not outcomes:
        print("[purchase-agent] no draft purchase orders found")
        return
    for o in outcomes:
        print(
            f"[purchase-agent] {o['document']} -> request #{o['request_id']} decision={o['decision']}"
        )
    for o in outcomes:
        if o["decision"] == Decision.PENDING.value:
            print(f"[gate] request #{o['request_id']} waiting for HUMAN approval")
            print(f"[gate]   confirm purchase.order {o['document']}")


def verify_state(client: OdooClient, record_id: int) -> str | None:
    rows = client.env["purchase.order"].read([record_id], ["state", "name"])
    return rows[0]["state"] if rows else None


def cleanup(client: OdooClient, gate: ActionGate) -> None:
    ids = find_demo_records(client)
    if not ids:
        return
    rows = client.env["purchase.order"].read(ids, ["id", "state"])
    confirmed = [r["id"] for r in rows if r["state"] != "draft"]
    if confirmed:
        cancel = AgentAction(
            model="purchase.order",
            method="call",
            args={"ids": confirmed, "method": "button_cancel"},
            reason="purchase agent demo cleanup: cancel confirmed demo purchase orders before removal",
        )
        request_id, decision = gate.submit(cancel)
        if decision is Decision.PENDING:
            gate.decide(request_id, True, comment="purchase demo: approve cancel")
        print(f"[demo] cancelled {len(confirmed)} confirmed demo purchase order(s)")
    action = AgentAction(
        model="purchase.order",
        method="unlink",
        args={"ids": ids},
        reason="purchase agent demo cleanup: remove the demo purchase orders so the business db stays clean",
    )
    request_id, decision = gate.submit(action)
    if decision is Decision.PENDING:
        gate.decide(request_id, True, comment="purchase demo: approve cleanup")
    print(f"[demo] cleaned {len(ids)} demo purchase order(s)")


def scan_only(client: OdooClient) -> None:
    agent = PurchaseAgent(client, ActionGate(client, mode=GateMode.MANUAL_APPROVAL), limit=10)
    orders = agent.find_pending(["id", "name", "partner_id", "amount_total"])
    print(f"[scan] {len(orders)} draft purchase order(s) in erp19:")
    for o in orders:
        print(
            f"  {o['name']:>10} partner={(o.get('partner_id') or ['',''])[1]:<20} "
            f"total={o.get('amount_total')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan of draft purchase orders")
    parser.add_argument("--approve", action="store_true", help="resolve every pending request as approved")
    parser.add_argument("--partner", type=int, default=3, help="partner id for the demo purchase order")
    args = parser.parse_args()

    cfg = OdooConfig.load()
    client = OdooClient(cfg)
    audit = AuditLog(AUDIT_PATH)
    gate = ActionGate(client, mode=GateMode.MANUAL_APPROVAL, audit=audit, precheck_hook=make_precheck_hook(client))

    print(f"[agent] connected as {client.whoami()['login']} (mode={gate.mode.value})")

    if args.scan:
        scan_only(client)
        client.close()
        return 0

    record_id = create_demo_order(client, gate, args.partner, auto_approve=args.approve)

    demo_ids = find_demo_records(client)
    confirm_via_agent(client, gate, auto_approve=args.approve, only_ids=demo_ids)

    if record_id is not None and args.approve:
        state = verify_state(client, record_id)
        print(f"\n[verify] purchase order state after confirmation = {state}")

    if args.approve:
        cleanup(client, gate)

    if not args.approve:
        print("\n[demo] requests left pending — run with --approve to complete the loop")

    print("\n[audit] last events:")
    for event in audit.read_all(limit=8):
        print(
            f"  {event.get('ts', '?'):>24}  #{event.get('request_id')} "
            f"{event.get('decision'):<8} {event.get('action', {}).get('model')}."
            f"{event.get('action', {}).get('method')}"
        )

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
