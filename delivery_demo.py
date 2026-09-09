#!/usr/bin/env python3
"""Delivery agent demo: sale order → outbound picking → ship.

The delivery step of the chain:

  scan      -- read-only: list outbound pickings waiting to be shipped
  default   -- create+confirm a demo sale order, leave the shipping PENDING
  --approve -- full loop: sale draft (approved) → sales agent confirm
               (approved) → delivery agent ship (approved) → verify →
               cleanup (approved)

Usage:
    venv/bin/python delivery_demo.py [--scan]
    venv/bin/python delivery_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.delivery_agent import DeliveryAgent
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.sales_agent import SalesAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
DEMO_NOTE = "AI-drafted (delivery demo)"
PRODUCT_ID = 1          # 可乐 (consu)
PARTNER_ID = 3
QTY = 5
PRICE = 1.0


def find_demo_sales(client: OdooClient) -> list[int]:
    return client.env["sale.order"].search([("note", "like", "%delivery demo%")])


def gated_create_so(client: OdooClient, gate: ActionGate, auto_approve: bool) -> int | None:
    action = AgentAction(
        model="sale.order",
        method="create",
        args={
            "values": {
                "partner_id": PARTNER_ID,
                "note": DEMO_NOTE,
                "order_line": [
                    (0, 0, {"product_id": PRODUCT_ID, "product_uom_qty": QTY, "price_unit": PRICE})
                ],
            }
        },
        reason="delivery demo: create a demo sale order",
    )
    request_id, decision = gate.submit(action)
    print(f"  create sale.order -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("  creation left pending (run with --approve to continue)")
        return None
    result = gate.decide(request_id, True, comment="delivery demo: approve sale creation")
    return result["id"] if isinstance(result, dict) else result


def confirm_so(client: OdooClient, gate: ActionGate, so_id: int, auto_approve: bool) -> str:
    outcomes = SalesAgent(client, gate).run_once(auto_approve=auto_approve, only_ids=[so_id])
    if not outcomes:
        return "not_found"
    o = outcomes[0]
    print(f"  {o.get('document')} -> request #{o.get('request_id')} decision={o.get('decision')}")
    return o.get("decision", "")


def read_state(client: OdooClient, model: str, record_id: int) -> str | None:
    rows = client.env[model].read([record_id], ["state"])
    return rows[0]["state"] if rows else None


def cleanup(client: OdooClient, gate: ActionGate, auto_approve: bool) -> None:
    so_ids = find_demo_sales(client)
    so_names: list[str] = []
    if so_ids:
        so_names = [r.get("name") for r in client.env["sale.order"].read(so_ids, ["name"]) if r.get("name")]

    # outbound pickings first (done ones are kept — real stock semantics)
    if so_names:
        pids = client.env["stock.picking"].search([("origin", "in", so_names)])
        if pids:
            rows = client.env["stock.picking"].read(pids, ["id", "state"])
            removable = [r["id"] for r in rows if r["state"] != "done"]
            completed = [r["id"] for r in rows if r["state"] == "done"]
            if removable:
                act = AgentAction(model="stock.picking", method="unlink", args={"ids": removable}, reason="delivery demo cleanup: remove demo pickings")
                rid, dec = gate.submit(act)
                if dec is Decision.PENDING and auto_approve:
                    gate.decide(rid, True, comment="delivery cleanup: approve picking removal")
                print(f"  cleaned {len(removable)} demo picking(s)")
            if completed:
                print(
                    f"  note: {len(completed)} completed picking(s) kept — run "
                    "scripts/cleanup_demo_pickings.sh on the demo DB to purge them."
                )

    # sale orders
    if so_ids:
        so_rows = client.env["sale.order"].read(so_ids, ["id", "state"])
        confirmed = [r["id"] for r in so_rows if r["state"] != "draft"]
        if confirmed:
            act = AgentAction(model="sale.order", method="call", args={"ids": confirmed, "method": "action_cancel"}, reason="delivery demo cleanup: cancel demo sale orders")
            rid, dec = gate.submit(act)
            if dec is Decision.PENDING and auto_approve:
                gate.decide(rid, True, comment="delivery cleanup: approve sale cancel")
        act = AgentAction(model="sale.order", method="unlink", args={"ids": so_ids}, reason="delivery demo cleanup: remove demo sale orders")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="delivery cleanup: approve sale removal")
        print(f"  cleaned {len(so_ids)} demo sale order(s)")


def scan_only(client: OdooClient) -> None:
    gate = ActionGate(client, mode=GateMode.MANUAL_APPROVAL)
    agent = DeliveryAgent(client, gate)
    pickings = agent.find_outbound_pickings()
    print(f"[scan] {len(pickings)} outbound picking(s) waiting to be shipped:")
    for p in pickings:
        print(f"  {p['name']} origin={p.get('origin')} state={p['state']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan of outbound pickings")
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

    print("\n== step 1: create demo sale order (gated) ==")
    so_id = gated_create_so(client, gate, args.approve)
    if so_id is None:
        print("\n[demo] stopped at step 1 (pending) — run with --approve")
        client.close()
        return 0

    print("\n== step 2: sales agent confirms (gated) ==")
    dec = confirm_so(client, gate, so_id, args.approve)
    if dec != Decision.APPROVED.value:
        print(f"[demo] stopped at step 2 ({dec})")
        cleanup(client, gate, args.approve)
        client.close()
        return 0
    print(f"  [verify] sale state = {read_state(client, 'sale.order', so_id)}")

    print("\n== step 3: delivery agent ships the outbound picking (gated) ==")
    delivery = DeliveryAgent(client, gate)
    so_name = client.env["sale.order"].read([so_id], ["name"])[0]["name"]
    pickings = [p for p in delivery.find_outbound_pickings() if p.get("origin") == so_name]
    if not pickings:
        print("[demo] no outbound picking generated for the demo sale order")
        cleanup(client, gate, args.approve)
        client.close()
        return 1
    outcomes = delivery.run_once(auto_approve=args.approve, only_ids=[pickings[0]["id"]])
    o = outcomes[0]
    print(f"  shipping findings:")
    for f in o["findings"]:
        print(f"    {f}")
    print(f"  {o['picking']} -> request #{o['request_id']} decision={o['decision']}")
    if o["decision"] != Decision.APPROVED.value:
        print(f"\n[demo] shipping left pending — run with --approve to ship")
        cleanup(client, gate, args.approve)
        client.close()
        return 0

    state = read_state(client, "stock.picking", pickings[0]["id"])
    print(f"  [verify] picking state after shipping = {state}")

    print("\n== step 4: cleanup (gated) ==")
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
