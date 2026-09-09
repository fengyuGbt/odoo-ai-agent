#!/usr/bin/env python3
"""Production agent demo: manufacturing order → confirm → produce.

The production step of the chain:

  scan      -- read-only: list manufacturing orders waiting for production
  default   -- create a demo MO, leave the confirm/completion PENDING
  --approve -- full loop: create MO (approved) → confirm (approved) →
               mark done (approved) → verify → cleanup (approved)

Usage:
    venv/bin/python production_demo.py [--scan]
    venv/bin/python production_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.production_agent import DEMO_ORIGIN, ProductionAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
PRODUCT_ID = 2          # 汉堡 (consu, no BOM in demo)
QTY = 5
ORIGIN = DEMO_ORIGIN


def find_demo_mos(client: OdooClient) -> list[int]:
    return client.env["mrp.production"].search([("origin", "=", ORIGIN)])


def gated_create_bom(client: OdooClient, gate: ActionGate, auto_approve: bool) -> int | None:
    """Create the demo BOM (汉堡 = 可乐 × 2) if it does not exist yet."""
    existing = client.env["mrp.bom"].search([("code", "=", "AI-PRODDEMO-BOM")])
    if existing:
        return existing[0]
    tmpl = client.env["product.product"].read([PRODUCT_ID], ["product_tmpl_id"])[0]["product_tmpl_id"][0]
    action = AgentAction(
        model="mrp.bom",
        method="create",
        args={
            "values": {
                "product_tmpl_id": tmpl,
                "product_qty": 1,
                "code": "AI-PRODDEMO-BOM",
                "type": "normal",
                "bom_line_ids": [
                    (0, 0, {"product_id": 1, "product_qty": 2})  # 可乐 × 2
                ],
            }
        },
        reason="production demo: create the demo BOM (汉堡 = 可乐 × 2)",
    )
    request_id, decision = gate.submit(action)
    print(f"  create mrp.bom -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("  BOM creation left pending (run with --approve to continue)")
        return None
    result = gate.decide(request_id, True, comment="production demo: approve BOM creation")
    return result["id"] if isinstance(result, dict) else result


def gated_create_mo(client: OdooClient, gate: ActionGate, auto_approve: bool, bom_id: int) -> int | None:
    action = AgentAction(
        model="mrp.production",
        method="create",
        args={
            "values": {
                "product_id": PRODUCT_ID,
                "product_qty": QTY,
                "bom_id": bom_id,
                "origin": ORIGIN,
            }
        },
        reason="production demo: create a demo manufacturing order",
    )
    request_id, decision = gate.submit(action)
    print(f"  create mrp.production -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("  creation left pending (run with --approve to continue)")
        return None
    result = gate.decide(request_id, True, comment="production demo: approve MO creation")
    return result["id"] if isinstance(result, dict) else result


def read_state(client: OdooClient, model: str, record_id: int) -> str | None:
    rows = client.env[model].read([record_id], ["state"])
    return rows[0]["state"] if rows else None


def cleanup(client: OdooClient, gate: ActionGate, auto_approve: bool) -> None:
    # demo BOM (only when no demo MO references it anymore)
    bom_ids = client.env["mrp.bom"].search([("code", "=", "AI-PRODDEMO-BOM")])
    mo_ids = find_demo_mos(client)
    if bom_ids and not mo_ids:
        act = AgentAction(model="mrp.bom", method="unlink", args={"ids": bom_ids}, reason="production demo cleanup: remove demo BOM")
        rid, dec = gate.submit(act)
        if dec is Decision.PENDING and auto_approve:
            gate.decide(rid, True, comment="production cleanup: approve BOM removal")
        print(f"  cleaned {len(bom_ids)} demo BOM(s)")
    if mo_ids:
        # done MOs are protected (finished moves) — report, keep, point to helper
        rows = client.env["mrp.production"].read(mo_ids, ["id", "name", "state"])
        done = [r for r in rows if r["state"] == "done"]
        pending = [r["id"] for r in rows if r["state"] != "done"]
        if pending:
            act = AgentAction(model="mrp.production", method="unlink", args={"ids": pending}, reason="production demo cleanup: remove pending demo MOs")
            rid, dec = gate.submit(act)
            if dec is Decision.PENDING and auto_approve:
                gate.decide(rid, True, comment="production cleanup: approve MO removal")
            print(f"  cleaned {len(pending)} pending demo MO(s)")
        if done:
            print(
                f"  note: {len(done)} completed MO(s) kept — run "
                "scripts/cleanup_demo_production.sh on the demo DB to purge them."
            )


def scan_only(client: OdooClient) -> None:
    gate = ActionGate(client, mode=GateMode.MANUAL_APPROVAL)
    agent = ProductionAgent(client, gate)
    mos = agent.find_pending_mos()
    print(f"[scan] {len(mos)} manufacturing order(s) waiting for production:")
    for mo in mos:
        product = (mo.get("product_id") or ["", ""])[1]
        print(f"  {mo['name']} {product} × {mo.get('product_qty')} state={mo['state']} origin={mo.get('origin') or '-'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan of manufacturing orders")
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

    print("\n== step 1: create demo BOM + manufacturing order (gated) ==")
    bom_id = gated_create_bom(client, gate, args.approve)
    if bom_id is None:
        print("\n[demo] stopped at BOM creation (pending) — run with --approve")
        client.close()
        return 0
    mo_id = gated_create_mo(client, gate, args.approve, bom_id)
    if mo_id is None:
        print("\n[demo] stopped at step 1 (pending) — run with --approve")
        client.close()
        return 0

    print("\n== step 2: production agent confirms the MO (gated) ==")
    production = ProductionAgent(client, gate)
    outcomes = production.run_once(auto_approve=args.approve, only_ids=[mo_id])
    o = outcomes[0]
    print(f"  inspection findings:")
    for f in o["findings"]:
        print(f"    {f}")
    print(f"  confirm -> request #{o['confirm_request_id']} decision={o['confirm_decision']}")
    if o["confirm_decision"] != Decision.APPROVED.value:
        print(f"[demo] stopped at confirm ({o['confirm_decision']})")
        cleanup(client, gate, args.approve)
        client.close()
        return 0
    confirm_state = read_state(client, 'mrp.production', mo_id)
    print(f"  [verify] MO state after confirm = {confirm_state}")
    if confirm_state == "done":
        print("  (Odoo community: no work orders installed — confirming a simple MO completes it immediately; mark-done step skipped)")
    else:
        print("\n== step 3: production agent marks the MO done (gated) ==")
        print(f"  mark done -> request #{o['done_request_id']} decision={o['done_decision']}")
        if o["done_decision"] != Decision.APPROVED.value:
            print(f"[demo] stopped at mark done ({o['done_decision']})")
            cleanup(client, gate, args.approve)
            client.close()
            return 0
        print(f"  [verify] MO state after done = {read_state(client, 'mrp.production', mo_id)}")

    # report what happened to finished-goods picking
    mo_name = client.env["mrp.production"].read([mo_id], ["name"])[0]["name"]
    finished_pickings = client.env["stock.picking"].search([("origin", "=", mo_name)])
    if finished_pickings:
        rows = client.env["stock.picking"].read(finished_pickings, ["name", "state", "picking_type_code"])
        print(f"  [verify] {len(finished_pickings)} finished-goods picking(s) generated:")
        for r in rows:
            print(f"    {r['name']} state={r['state']} type={r.get('picking_type_code')}")
    else:
        print("  [verify] no finished-goods picking generated (consumable product, demo env)")

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
