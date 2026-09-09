#!/usr/bin/env python3
"""End-to-end chain demo: sales intake → approval → confirm → demand analysis → purchase.

This is the first end-to-end slice of the AI operation layer, following the
chain the founder described: an order comes in, gets approved, and then
produces downstream documents — here a purchase order.

  sale draft (approved) → sales agent confirms (approved)
    → demand agent checks stock, shortfall → purchase draft (approved)
      → purchase agent confirms (approved)
        → verify states → cleanup (every step through the gate, audited)

Usage:
    venv/bin/python e2e_demo.py [--scan]
    venv/bin/python e2e_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.demand_agent import DemandAgent, default_date_planned
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.purchase_agent import PurchaseAgent
from agent.sales_agent import SalesAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
E2E_NOTE = "AI-drafted (e2e demo)"
PRODUCT_ID = 1          # 可乐 (consu, purchase_ok, qty 0)
PARTNER_ID = 3          # demo environment has no supplier master data
QTY = 10
PRICE = 1.0


def find_demo_sales(client: OdooClient) -> list[int]:
    return client.env["sale.order"].search([("note", "like", "%e2e demo%")])


def find_demo_purchases(client: OdooClient) -> list[int]:
    return client.env["purchase.order"].search([("note", "like", "%e2e demo%")])


def gated_create(
    client: OdooClient,
    gate: ActionGate,
    model: str,
    values: dict,
    reason: str,
    approve_label: str,
    auto_approve: bool,
) -> int | None:
    action = AgentAction(model=model, method="create", args={"values": values}, reason=reason)
    request_id, decision = gate.submit(action)
    print(f"  create {model} -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print(f"  creation left pending (run with --approve to continue the chain)")
        return None
    result = gate.decide(request_id, True, comment=approve_label)
    return result["id"] if isinstance(result, dict) else result


def confirm_one(client: OdooClient, gate: ActionGate, agent, only_id: int, auto_approve: bool) -> str:
    outcomes = agent.run_once(auto_approve=auto_approve, only_ids=[only_id])
    if not outcomes:
        return "not_found"
    print(f"  {outcomes[0]['document']} -> request #{outcomes[0]['request_id']} decision={outcomes[0]['decision']}")
    return outcomes[0]["decision"]


def read_state(client: OdooClient, model: str, record_id: int) -> str | None:
    rows = client.env[model].read([record_id], ["state"])
    return rows[0]["state"] if rows else None


def gated_cleanup_act(client: OdooClient, gate: ActionGate, action: AgentAction, label: str, auto_approve: bool) -> None:
    request_id, decision = gate.submit(action)
    if decision is Decision.PENDING and auto_approve:
        gate.decide(request_id, True, comment=f"e2e cleanup: approve {label}")


def cleanup(client: OdooClient, gate: ActionGate, auto_approve: bool) -> None:
    # 0) collect demo order names for picking lookup
    so_ids = find_demo_sales(client)
    po_ids = find_demo_purchases(client)
    origins: list[str] = []
    if so_ids:
        origins += [r.get("name") for r in client.env["sale.order"].read(so_ids, ["name"]) if r.get("name")]
    if po_ids:
        origins += [r.get("name") for r in client.env["purchase.order"].read(po_ids, ["name"]) if r.get("name")]

    # 1) clean pickings created by the demo confirmations (cancel then remove)
    if origins:
        pids = client.env["stock.picking"].search([("origin", "in", origins)])
        if pids:
            rows = client.env["stock.picking"].read(pids, ["id", "state"])
            to_cancel = [r["id"] for r in rows if r["state"] != "cancel"]
            if to_cancel:
                gated_cleanup_act(
                    client, gate,
                    AgentAction(model="stock.picking", method="call", args={"ids": to_cancel, "method": "action_cancel"},
                                reason="e2e cleanup: cancel demo pickings"),
                    "picking cancel", auto_approve,
                )
            gated_cleanup_act(
                client, gate,
                AgentAction(model="stock.picking", method="unlink", args={"ids": pids},
                            reason="e2e cleanup: remove demo pickings"),
                "picking removal", auto_approve,
            )
            print(f"  cleaned {len(pids)} demo picking(s)")

    # 2) purchases, then sales
    if po_ids:
        po_rows = client.env["purchase.order"].read(po_ids, ["id", "state"])
        confirmed = [r["id"] for r in po_rows if r["state"] != "draft"]
        if confirmed:
            gated_cleanup_act(
                client, gate,
                AgentAction(model="purchase.order", method="call", args={"ids": confirmed, "method": "button_cancel"},
                            reason="e2e cleanup: cancel demo purchase orders"),
                "purchase cancel", auto_approve,
            )
        gated_cleanup_act(
            client, gate,
            AgentAction(model="purchase.order", method="unlink", args={"ids": po_ids},
                        reason="e2e cleanup: remove demo purchase orders"),
            "purchase removal", auto_approve,
        )
        print(f"  cleaned {len(po_ids)} demo purchase order(s)")

    if so_ids:
        so_rows = client.env["sale.order"].read(so_ids, ["id", "state"])
        confirmed = [r["id"] for r in so_rows if r["state"] != "draft"]
        if confirmed:
            gated_cleanup_act(
                client, gate,
                AgentAction(model="sale.order", method="call", args={"ids": confirmed, "method": "action_cancel"},
                            reason="e2e cleanup: cancel demo sale orders"),
                "sale cancel", auto_approve,
            )
        gated_cleanup_act(
            client, gate,
            AgentAction(model="sale.order", method="unlink", args={"ids": so_ids},
                        reason="e2e cleanup: remove demo sale orders"),
            "sale removal", auto_approve,
        )
        print(f"  cleaned {len(so_ids)} demo sale order(s)")


def scan_only(client: OdooClient) -> None:
    env = client.env
    product = env["product.product"].read([PRODUCT_ID], ["name", "qty_available", "list_price"])
    p = product[0]
    print(f"[scan] product #{PRODUCT_ID} {p['name']!r} qty_available={p['qty_available']} price={p['list_price']}")
    print(f"[scan] demand agent view: needed {QTY:g}, shortfall = {max(0, QTY - float(p['qty_available'] or 0)):g}")
    so = env["sale.order"].search([("state", "=", "draft")], limit=5)
    po = env["purchase.order"].search([("state", "=", "draft")], limit=5)
    print(f"[scan] draft sale orders: {len(so)}, draft purchase orders: {len(po)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan of products / stock / drafts")
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

    print("\n== step 1: sales intake — draft quotation (gated) ==")
    sale_id = gated_create(
        client, gate, "sale.order",
        {
            "partner_id": PARTNER_ID,
            "note": E2E_NOTE,
            "order_line": [(0, 0, {"product_id": PRODUCT_ID, "product_uom_qty": QTY, "price_unit": PRICE})],
        },
        "e2e demo: create a draft quotation for the intake step",
        "e2e: approve sale draft", args.approve,
    )
    if sale_id is None:
        print("\n[demo] chain stopped at step 1 (pending) — run with --approve")
        client.close()
        return 0

    print("\n== step 2: sales agent confirms the quotation (gated) ==")
    dec = confirm_one(client, gate, SalesAgent(client, gate), sale_id, args.approve)
    if dec != Decision.APPROVED.value:
        print(f"\n[demo] chain stopped at step 2 ({dec})")
        cleanup(client, gate, args.approve)
        client.close()
        return 0
    print(f"  [verify] sale state = {read_state(client, 'sale.order', sale_id)}")

    print("\n== step 3: demand agent checks stock, proposes purchase (gated) ==")
    demand = DemandAgent(client, gate)
    out = demand.run(
        PRODUCT_ID, QTY, PARTNER_ID, PRICE,
        default_date_planned(7), auto_approve=args.approve, note=E2E_NOTE,
    )
    print(f"  analysis: needed={out['needed']:g} available={out['available']:g} shortfall={out['shortfall']:g}")
    print(f"  {out['decision']} -> gate={out.get('gate_decision')} request=#{out.get('request_id')}")
    if out["decision"] != "purchase_requested" or out.get("gate_decision") != Decision.APPROVED.value:
        print(f"\n[demo] chain stopped at step 3")
        cleanup(client, gate, args.approve)
        client.close()
        return 0
    po_ids = find_demo_purchases(client)
    if not po_ids:
        print("ERROR: purchase proposal not found")
        cleanup(client, gate, args.approve)
        client.close()
        return 1
    po_id = po_ids[0]

    print("\n== step 4: purchase agent confirms the purchase order (gated) ==")
    dec = confirm_one(client, gate, PurchaseAgent(client, gate), po_id, args.approve)
    if dec != Decision.APPROVED.value:
        print(f"\n[demo] chain stopped at step 4 ({dec})")
        cleanup(client, gate, args.approve)
        client.close()
        return 0
    print(f"  [verify] purchase state = {read_state(client, 'purchase.order', po_id)}")

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
