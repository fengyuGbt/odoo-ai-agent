#!/usr/bin/env python3
"""Logistics agent demo: sale order → ship → carrier → tracking → sign-off.

The logistics step of the chain:

  scan     -- read-only: list shipped deliveries + available carriers
  default  -- create a demo carrier + sale order, leave everything PENDING
  --approve -- full loop: carrier (approved) → sale (approved) → ship
               (approved) → assign carrier (approved) → tracking
               (approved) → sign-off (approved) → verify → cleanup

Usage:
    venv/bin/python logistics_demo.py [--scan]
    venv/bin/python logistics_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.delivery_agent import DeliveryAgent
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.logistics_agent import LogisticsAgent
from agent.odoo_client import OdooClient
from agent.sales_agent import SalesAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
DEMO_NOTE = "AI-drafted (logistics demo)"
PRODUCT_ID = 1          # 可乐 (consu)
PARTNER_ID = 3
QTY = 3
PRICE = 1.0
CARRIER_NAME = "AI Demo Carrier"
DELIVERY_PRODUCT = "AI Delivery Service"
TRACKING_REF = "AI-LOGDEMO-0001"


def find_demo_sales(client: OdooClient) -> list[int]:
    return client.env["sale.order"].search([("note", "like", "%logistics demo%")])


def gated_create(client: OdooClient, gate: ActionGate, model: str, action_vals: dict, reason: str, auto_approve: bool) -> int | None:
    action = AgentAction(model=model, method="create", args=action_vals, reason=reason)
    request_id, decision = gate.submit(action)
    print(f"  create {model} -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("  creation left pending (run with --approve to continue)")
        return None
    result = gate.decide(request_id, True, comment=f"logistics demo: approve {model} creation")
    return result["id"] if isinstance(result, dict) else result


def read_state(client: OdooClient, model: str, record_id: int) -> str | None:
    rows = client.env[model].read([record_id], ["state"])
    return rows[0]["state"] if rows else None


def cleanup(client: OdooClient, gate: ActionGate, auto_approve: bool) -> None:
    # sale orders
    so_ids = find_demo_sales(client)
    so_names: list[str] = []
    if so_ids:
        so_names = [r.get("name") for r in client.env["sale.order"].read(so_ids, ["name"]) if r.get("name")]

    # carriers
    carrier_ids = client.env["delivery.carrier"].search([("name", "=", CARRIER_NAME)])
    if carrier_ids:
        act = AgentAction(model="delivery.carrier", method="unlink", args={"ids": carrier_ids}, reason="logistics demo cleanup: remove demo carrier")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="logistics cleanup: approve carrier removal")
        print(f"  cleaned {len(carrier_ids)} demo carrier(s)")

    # delivery product template
    tids = client.env["product.template"].search([("name", "=", DELIVERY_PRODUCT)])
    if tids:
        act = AgentAction(model="product.template", method="unlink", args={"ids": tids}, reason="logistics demo cleanup: remove delivery service product")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="logistics cleanup: approve product removal")
        print(f"  cleaned {len(tids)} delivery product(s)")

    # outbound pickings (done kept)
    if so_names:
        pids = client.env["stock.picking"].search([("origin", "in", so_names)])
        if pids:
            rows = client.env["stock.picking"].read(pids, ["id", "state"])
            removable = [r["id"] for r in rows if r["state"] != "done"]
            completed = [r["id"] for r in rows if r["state"] == "done"]
            if removable:
                act = AgentAction(model="stock.picking", method="unlink", args={"ids": removable}, reason="logistics demo cleanup: remove demo pickings")
                rid, dec = gate.submit(act)
                if dec is Decision.PENDING and auto_approve:
                    gate.decide(rid, True, comment="logistics cleanup: approve picking removal")
                print(f"  cleaned {len(removable)} demo picking(s)")
            if completed:
                print(f"  note: {len(completed)} completed picking(s) kept — run scripts/cleanup_demo_pickings.sh to purge.")

    if so_ids:
        so_rows = client.env["sale.order"].read(so_ids, ["id", "state"])
        confirmed = [r["id"] for r in so_rows if r["state"] != "draft"]
        if confirmed:
            act = AgentAction(model="sale.order", method="call", args={"ids": confirmed, "method": "action_cancel"}, reason="logistics demo cleanup: cancel demo sale orders")
            rid, dec = gate.submit(act)
            if dec is Decision.PENDING and auto_approve:
                gate.decide(rid, True, comment="logistics cleanup: approve sale cancel")
        act = AgentAction(model="sale.order", method="unlink", args={"ids": so_ids}, reason="logistics demo cleanup: remove demo sale orders")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="logistics cleanup: approve sale removal")
        print(f"  cleaned {len(so_ids)} demo sale order(s)")


def scan_only(client: OdooClient) -> None:
    gate = ActionGate(client, mode=GateMode.MANUAL_APPROVAL)
    agent = LogisticsAgent(client, gate)
    pickings = agent.find_shipped_pickings()
    carriers = agent.find_carriers()
    print(f"[scan] {len(pickings)} shipped delivery(ies):")
    for p in pickings:
        print(f"  {p['name']} origin={p.get('origin')} carrier={(p.get('carrier_id') or ['', '-'])[1]} tracking={p.get('carrier_tracking_ref') or '-'}")
    print(f"[scan] {len(carriers)} carrier(s):")
    for c in carriers:
        print(f"  {c['name']} (type={c['delivery_type']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan")
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

    print("\n== step 1: create delivery service product + carrier (gated) ==")
    tid = gated_create(
        client, gate, "product.template",
        {"values": {"name": DELIVERY_PRODUCT, "type": "service", "sale_ok": True}},
        "logistics demo: create delivery service product", args.approve,
    )
    if tid is None:
        print("\n[demo] stopped at step 1")
        client.close()
        return 0
    pid_rows = client.env["product.product"].search([("product_tmpl_id", "=", tid)])
    pid = pid_rows[0] if pid_rows else None
    carrier_id = gated_create(
        client, gate, "delivery.carrier",
        {"values": {"name": CARRIER_NAME, "delivery_type": "fixed", "fixed_price": 0.0, "product_id": pid}},
        "logistics demo: create demo carrier", args.approve,
    )
    if carrier_id is None:
        print("\n[demo] stopped at step 1 (carrier)")
        cleanup(client, gate, args.approve)
        client.close()
        return 0

    print("\n== step 2: create demo sale order with carrier (gated) ==")
    so_id = gated_create(
        client, gate, "sale.order",
        {
            "values": {
                "partner_id": PARTNER_ID,
                "note": DEMO_NOTE,
                "carrier_id": carrier_id,
                "order_line": [
                    (0, 0, {"product_id": PRODUCT_ID, "product_uom_qty": QTY, "price_unit": PRICE})
                ],
            }
        },
        "logistics demo: create demo sale order with carrier", args.approve,
    )
    if so_id is None:
        print("\n[demo] stopped at step 2")
        cleanup(client, gate, args.approve)
        client.close()
        return 0

    print("\n== step 3: sales agent confirms (gated) ==")
    outcomes = SalesAgent(client, gate).run_once(auto_approve=args.approve, only_ids=[so_id])
    o = outcomes[0]
    print(f"  {o.get('document')} -> request #{o.get('request_id')} decision={o.get('decision')}")
    if o.get("decision") != Decision.APPROVED.value:
        cleanup(client, gate, args.approve)
        client.close()
        return 0

    print("\n== step 4: delivery agent ships (gated) ==")
    so_name = client.env["sale.order"].read([so_id], ["name"])[0]["name"]
    delivery = DeliveryAgent(client, gate)
    pickings = [p for p in delivery.find_outbound_pickings() if p.get("origin") == so_name]
    if not pickings:
        print("[demo] no outbound picking generated")
        cleanup(client, gate, args.approve)
        client.close()
        return 1
    ship_outcomes = delivery.run_once(auto_approve=args.approve, only_ids=[pickings[0]["id"]])
    print(f"  {ship_outcomes[0]['picking']} -> decision={ship_outcomes[0]['decision']}")

    print("\n== step 5: logistics agent — carrier → tracking → sign-off (gated) ==")
    logi = LogisticsAgent(client, gate)
    results = logi.run_once(
        auto_approve=args.approve,
        only_ids=[pickings[0]["id"]],
        carrier_id=carrier_id,
        tracking_ref=TRACKING_REF,
    )
    for r in results:
        for f in r["findings"]:
            print(f"    {f}")
        for a in r["actions"]:
            print(f"    {a['step']:<16} -> request #{a.get('request_id')} decision={a['decision']}")

    rows = client.env["stock.picking"].read(
        [pickings[0]["id"]], ["carrier_id", "carrier_tracking_ref"]
    )
    rec = rows[0]
    print(f"  [verify] carrier={(rec.get('carrier_id') or ['', '-'])[1]} tracking={rec.get('carrier_tracking_ref')}")

    print("\n== step 6: cleanup (gated) ==")
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
