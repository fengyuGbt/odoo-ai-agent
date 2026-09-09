#!/usr/bin/env python3
"""Payment agent demo: purchase → receive → vendor bill → payment.

The payment step of the chain:

  scan      -- read-only: list purchase orders confirmed and fully received
  default   -- create+confirm a demo purchase order, receive it, then leave
               the bill/payment chain PENDING
  --approve -- full loop: purchase (approved) → confirm (approved) →
               receive (approved) → create bill (approved) → post bill
               (approved) → create payment (approved) → post payment
               (approved) → verify → cleanup (approved)

Usage:
    venv/bin/python payment_demo.py [--scan]
    venv/bin/python payment_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.demand_agent import default_date_planned
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.payment_agent import PaymentAgent
from agent.purchase_agent import PurchaseAgent
from agent.receiving_agent import ReceivingAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
DEMO_NOTE = "AI-drafted (payment demo)"
PRODUCT_ID = 1          # 可乐 (consu, purchase_ok)
PARTNER_ID = 3
QTY = 10
PRICE = 1.0


def find_demo_purchases(client: OdooClient) -> list[int]:
    return client.env["purchase.order"].search([("note", "like", "%payment demo%")])


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
        reason="payment demo: create a demo purchase order",
    )
    request_id, decision = gate.submit(action)
    print(f"  create purchase.order -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("  creation left pending (run with --approve to continue)")
        return None
    result = gate.decide(request_id, True, comment="payment demo: approve purchase creation")
    return result["id"] if isinstance(result, dict) else result


def confirm_po(client: OdooClient, gate: ActionGate, po_id: int, auto_approve: bool) -> str:
    outcomes = PurchaseAgent(client, gate).run_once(auto_approve=auto_approve, only_ids=[po_id])
    if not outcomes:
        return "not_found"
    print(f"  {outcomes[0]['document']} -> request #{outcomes[0]['request_id']} decision={outcomes[0]['decision']}")
    return outcomes[0]["decision"]


def receive_po(client: OdooClient, gate: ActionGate, po_id: int, auto_approve: bool) -> str:
    receiving = ReceivingAgent(client, gate)
    po_name = client.env["purchase.order"].read([po_id], ["name"])[0]["name"]
    pickings = [p for p in receiving.find_inbound_pickings() if p.get("origin") == po_name]
    if not pickings:
        print("  no inbound picking generated for the demo purchase order")
        return "no_picking"
    outcomes = receiving.run_once(auto_approve=auto_approve, only_ids=[pickings[0]["id"]])
    o = outcomes[0]
    print(f"  {o['picking']} -> request #{o['request_id']} decision={o['decision']}")
    for f in o["findings"]:
        print(f"    {f}")
    return o["decision"]


def read_state(client: OdooClient, model: str, record_id: int) -> str | None:
    rows = client.env[model].read([record_id], ["state"])
    return rows[0]["state"] if rows else None


def cleanup(client: OdooClient, gate: ActionGate, auto_approve: bool) -> None:
    po_ids = find_demo_purchases(client)
    po_names: list[str] = []
    if po_ids:
        po_names = [r.get("name") for r in client.env["purchase.order"].read(po_ids, ["name"]) if r.get("name")]

    # 1) payments first (cancel then unlink) — demo payments carry the
    #    AI-PAYDEMO marker in payment_reference
    pay_ids: list[int] = client.env["account.payment"].search(
        [("payment_reference", "like", "AI-PAYDEMO%")]
    )
    if pay_ids:
        pay_rows = client.env["account.payment"].read(pay_ids, ["id", "state"])
        to_cancel = [r["id"] for r in pay_rows if r["state"] != "cancelled"]
        if to_cancel:
            act = AgentAction(model="account.payment", method="call", args={"ids": to_cancel, "method": "action_cancel"}, reason="payment demo cleanup: cancel demo payments")
            rid, dec = gate.submit(act)
            if dec is Decision.PENDING and auto_approve:
                gate.decide(rid, True, comment="payment cleanup: approve payment cancel")
        act = AgentAction(model="account.payment", method="unlink", args={"ids": pay_ids}, reason="payment demo cleanup: remove demo payments")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="payment cleanup: approve payment removal")
        print(f"  cleaned {len(pay_ids)} demo payment(s)")

    # 2) bills (draft them back if posted, then unlink)
    bill_ids: list[int] = []
    if po_names:
        bill_ids = client.env["account.move"].search([("invoice_origin", "in", po_names), ("move_type", "=", "in_invoice")])
    if bill_ids:
        bill_rows = client.env["account.move"].read(bill_ids, ["id", "state"])
        posted = [r["id"] for r in bill_rows if r["state"] == "posted"]
        if posted:
            act = AgentAction(model="account.move", method="call", args={"ids": posted, "method": "button_draft"}, reason="payment demo cleanup: reset demo bills to draft")
            rid, dec = gate.submit(act)
            if dec is Decision.PENDING and auto_approve:
                gate.decide(rid, True, comment="payment cleanup: approve bill draft reset")
        act = AgentAction(model="account.move", method="unlink", args={"ids": bill_ids}, reason="payment demo cleanup: remove demo bills")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="payment cleanup: approve bill removal")
        print(f"  cleaned {len(bill_ids)} demo bill(s)")

    # 3) inbound pickings (keep done ones — real stock semantics)
    if po_names:
        pids = client.env["stock.picking"].search([("origin", "in", po_names)])
        if pids:
            rows = client.env["stock.picking"].read(pids, ["id", "state"])
            removable = [r["id"] for r in rows if r["state"] != "done"]
            completed = [r["id"] for r in rows if r["state"] == "done"]
            if removable:
                act = AgentAction(model="stock.picking", method="unlink", args={"ids": removable}, reason="payment demo cleanup: remove demo pickings")
                rid, dec = gate.submit(act)
                if dec is Decision.PENDING and auto_approve:
                    gate.decide(rid, True, comment="payment cleanup: approve picking removal")
                print(f"  cleaned {len(removable)} demo picking(s)")
            if completed:
                print(
                    f"  note: {len(completed)} completed picking(s) kept — run "
                    "scripts/cleanup_demo_pickings.sh on the demo DB to purge them."
                )

    # 4) purchase orders
    if po_ids:
        po_rows = client.env["purchase.order"].read(po_ids, ["id", "state"])
        confirmed = [r["id"] for r in po_rows if r["state"] != "draft"]
        if confirmed:
            act = AgentAction(model="purchase.order", method="call", args={"ids": confirmed, "method": "button_cancel"}, reason="payment demo cleanup: cancel demo purchase orders")
            rid, dec = gate.submit(act)
            if dec is Decision.PENDING and auto_approve:
                gate.decide(rid, True, comment="payment cleanup: approve purchase cancel")
        act = AgentAction(model="purchase.order", method="unlink", args={"ids": po_ids}, reason="payment demo cleanup: remove demo purchase orders")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="payment cleanup: approve purchase removal")
        print(f"  cleaned {len(po_ids)} demo purchase order(s)")


def scan_only(client: OdooClient) -> None:
    gate = ActionGate(client, mode=GateMode.MANUAL_APPROVAL)
    agent = PaymentAgent(client, gate)
    pos = agent.find_received_pos()
    print(f"[scan] {len(pos)} confirmed purchase order(s) fully received:")
    for po in pos:
        print(f"  {po['name']} partner={(po.get('partner_id') or ['', ''])[1]} total={po.get('amount_total')} invoice_status={po.get('invoice_status')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan of received purchase orders")
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

    print("\n== step 3: receiving agent receives the goods (gated) ==")
    dec = receive_po(client, gate, po_id, args.approve)
    if dec != Decision.APPROVED.value:
        print(f"[demo] stopped at step 3 ({dec})")
        cleanup(client, gate, args.approve)
        client.close()
        return 0

    print("\n== step 4-7: payment agent — bill and payment chain (gated) ==")
    payment = PaymentAgent(client, gate)
    steps = payment.run_once(po_id, auto_approve=args.approve)
    for s in steps:
        print(f"  {s['label']:<16} -> request #{s['request_id']} decision={s['decision']}")

    # verify
    po_name = client.env["purchase.order"].read([po_id], ["name"])[0]["name"]
    bill_id = payment.find_bill(po_name)
    if bill_id is not None:
        print(f"  [verify] bill state = {read_state(client, 'account.move', bill_id)}")
    pay_rows = client.env["account.payment"].search([("payment_reference", "like", "AI-PAYDEMO%")])
    if pay_rows:
        pay_row = client.env["account.payment"].read([pay_rows[-1]], ["name", "state"])[0]
        print(f"  [verify] payment {pay_row['name']} state = {pay_row['state']}")

    print("\n== step 8: cleanup (gated) ==")
    cleanup(client, gate, args.approve)

    print("\n[audit] last events:")
    for event in audit.read_all(limit=12):
        print(
            f"  {event.get('ts', '?'):>24}  #{event.get('request_id')} "
            f"{event.get('decision'):<8} {event.get('action', {}).get('model')}."
            f"{event.get('action', {}).get('method')}"
        )

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
