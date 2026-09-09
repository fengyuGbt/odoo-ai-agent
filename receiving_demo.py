#!/usr/bin/env python3
"""Receiving agent demo: purchase → inbound picking → inspection → receipt.

The receiving step of the chain:

  scan     -- read-only: list inbound pickings waiting to be received
  default  -- create+confirm a demo purchase order, inspect the generated
              inbound picking, submit the receipt request, leave it PENDING
  --approve -- full loop: purchase (approved) → confirm (approved) →
              inspect → receive (approved) → verify → cleanup (approved)

Usage:
    venv/bin/python receiving_demo.py [--scan]
    venv/bin/python receiving_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.demand_agent import default_date_planned
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.purchase_agent import PurchaseAgent
from agent.receiving_agent import ReceivingAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
DEMO_NOTE = "AI-drafted (receiving demo)"
PRODUCT_ID = 1          # 可乐 (consu, purchase_ok)
PARTNER_ID = 3
QTY = 10
PRICE = 1.0


def find_demo_purchases(client: OdooClient) -> list[int]:
    return client.env["purchase.order"].search([("note", "like", "%receiving demo%")])


def gated_create_po(client: OdooClient, gate: ActionGate, auto_approve: bool) -> int | None:
    action = AgentAction(
        model="purchase.order",
        method="create",
        args={
            "values": {
                "partner_id": PARTNER_ID,
                "note": DEMO_NOTE,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": PRODUCT_ID,
                            "product_qty": QTY,
                            "price_unit": PRICE,
                            "date_planned": default_date_planned(7),
                        },
                    )
                ],
            }
        },
        reason="receiving demo: create a demo purchase order to generate an inbound picking",
    )
    request_id, decision = gate.submit(action)
    print(f"  create purchase.order -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("  creation left pending (run with --approve to continue)")
        return None
    result = gate.decide(request_id, True, comment="receiving demo: approve purchase creation")
    return result["id"] if isinstance(result, dict) else result


def confirm_po(client: OdooClient, gate: ActionGate, po_id: int, auto_approve: bool) -> str:
    outcomes = PurchaseAgent(client, gate).run_once(auto_approve=auto_approve, only_ids=[po_id])
    if not outcomes:
        return "not_found"
    print(f"  {outcomes[0]['document']} -> request #{outcomes[0]['request_id']} decision={outcomes[0]['decision']}")
    return outcomes[0]["decision"]


def read_state(client: OdooClient, model: str, record_id: int) -> str | None:
    rows = client.env[model].read([record_id], ["state"])
    return rows[0]["state"] if rows else None


def cleanup(client: OdooClient, gate: ActionGate, auto_approve: bool) -> None:
    po_ids = find_demo_purchases(client)

    # pickings first: their origin names must be read before the purchase
    # orders are removed
    origins: list[str] = []
    if po_ids:
        origins = [r.get("name") for r in client.env["purchase.order"].read(po_ids, ["name"]) if r.get("name")]
    if origins:
        pids = client.env["stock.picking"].search([("origin", "in", origins)])
        if pids:
            rows = client.env["stock.picking"].read(pids, ["id", "state"])
            removable = [r["id"] for r in rows if r["state"] != "done"]
            completed = [r["id"] for r in rows if r["state"] == "done"]
            if removable:
                act = AgentAction(model="stock.picking", method="unlink", args={"ids": removable}, reason="receiving demo cleanup: remove demo pickings")
                rid, dec = gate.submit(act)
                if dec is Decision.PENDING and auto_approve:
                    gate.decide(rid, True, comment="receiving cleanup: approve picking removal")
                print(f"  cleaned {len(removable)} demo picking(s)")
            if completed:
                print(
                    f"  note: {len(completed)} completed picking(s) kept — Odoo forbids deleting "
                    "done transfers (real stock semantics). On the demo DB only, run "
                    "scripts/cleanup_demo_pickings.sh to purge them."
                )

    if po_ids:
        po_rows = client.env["purchase.order"].read(po_ids, ["id", "state"])
        confirmed = [r["id"] for r in po_rows if r["state"] != "draft"]
        if confirmed:
            act = AgentAction(model="purchase.order", method="call", args={"ids": confirmed, "method": "button_cancel"}, reason="receiving demo cleanup: cancel demo purchase orders")
            rid, dec = gate.submit(act)
            if dec is Decision.PENDING and auto_approve:
                gate.decide(rid, True, comment="receiving cleanup: approve purchase cancel")
        act = AgentAction(model="purchase.order", method="unlink", args={"ids": po_ids}, reason="receiving demo cleanup: remove demo purchase orders")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="receiving cleanup: approve purchase removal")
        print(f"  cleaned {len(po_ids)} demo purchase order(s)")


def scan_only(client: OdooClient) -> None:
    agent = ReceivingAgent(client, ActionGate(client, mode=GateMode.MANUAL_APPROVAL))
    pickings = agent.find_inbound_pickings()
    print(f"[scan] {len(pickings)} inbound picking(s) waiting to be received:")
    for p in pickings:
        print(f"  {p['name']} origin={p.get('origin')} state={p['state']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan of inbound pickings")
    parser.add_argument("--approve", action="store_true", help="resolve every pending request as approved")
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

    print("\n== step 1: create demo purchase order (gated) ==")
    po_id = gated_create_po(client, gate, args.approve)
    if po_id is None:
        print("\n[demo] stopped at step 1 (pending) — run with --approve")
        client.close()
        return 0

    print("\n== step 2: purchase agent confirms (gated) ==")
    dec = confirm_po(client, gate, po_id, args.approve)
    if dec != Decision.APPROVED.value:
        print(f"[demo] stopped at step 2 ({dec})")
        cleanup(client, gate, args.approve)
        client.close()
        return 0
    print(f"  [verify] purchase state = {read_state(client, 'purchase.order', po_id)}")

    print("\n== step 3: receiving agent inspects the inbound picking ==")
    receiving = ReceivingAgent(client, gate)
    pickings = receiving.find_inbound_pickings()
    demo_origin = client.env["purchase.order"].read([po_id], ["name"])[0]["name"]
    target = [p for p in pickings if p.get("origin") == demo_origin]
    if not target:
        print("[demo] no inbound picking generated for the demo purchase order")
        cleanup(client, gate, args.approve)
        client.close()
        return 1
    picking = target[0]

    print("\n== step 4: receiving agent submits the receipt request (gated) ==")
    outcomes = receiving.run_once(auto_approve=args.approve, only_ids=[picking["id"]])
    o = outcomes[0]
    print(f"  inspection findings:")
    for f in o["findings"]:
        print(f"    {f}")
    print(f"  {o['picking']} -> request #{o['request_id']} decision={o['decision']}")
    if o["decision"] != Decision.APPROVED.value:
        print(f"\n[demo] receipt left pending — run with --approve to receive")
        cleanup(client, gate, args.approve)
        client.close()
        return 0

    state = read_state(client, "stock.picking", picking["id"])
    print(f"  [verify] picking state after receipt = {state}")

    print("\n== step 5: cleanup (gated) ==")
    cleanup(client, gate, args.approve)

    print("\n[audit] last events:")
    for event in audit.read_all(limit=10):
        print(
            f"  {event.get('ts', '?'):>24}  #{event.get('request_id')} "
            f"{event.get('decision'):<8} {event.get('action', {}).get('model')}."
            f"{event.get('action', {}).get('method')}"
        )

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
