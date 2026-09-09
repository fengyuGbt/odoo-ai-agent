#!/usr/bin/env python3
"""Sales agent demo: quotation intake → tier pre-check → human approval → confirm.

The demo shows the first domain agent (sales) of the AI operation layer:

  scan     -- read-only: list draft quotations + tier pre-check conclusion
  default  -- create one demo quotation, submit confirmation, leave it
              PENDING (nothing is executed without the human)
  --approve -- full loop: create (approved) → confirm (approved) → verify
              state=sale → cleanup (approved) → audit summary

Usage:
    venv/bin/python sales_demo.py [--scan]
    venv/bin/python sales_demo.py [--approve]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.sales_agent import SalesAgent
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")
DEMO_NOTE = "AI-drafted (sales agent demo)"


def find_demo_records(client: OdooClient) -> list[int]:
    return client.env["sale.order"].search([("note", "like", "%sales agent demo%")])


def create_demo_quotation(
    client: OdooClient, gate: ActionGate, partner_id: int, auto_approve: bool = False
) -> int | None:
    action = AgentAction(
        model="sale.order",
        method="create",
        args={"values": {"partner_id": partner_id, "note": DEMO_NOTE}},
        reason="sales agent demo: create a draft quotation to demonstrate the intake flow",
    )
    request_id, decision = gate.submit(action)
    print(f"[demo] create quotation -> decision={decision.value}")
    if decision is not Decision.PENDING:
        return None
    if not auto_approve:
        print("[demo] creation request left pending (run with --approve to execute)")
        return None
    result = gate.decide(request_id, True, comment="sales demo: approve quotation creation")
    print(f"[demo] human approved creation -> {result}")
    return result["id"] if isinstance(result, dict) else result


def confirm_via_agent(client: OdooClient, gate: ActionGate, auto_approve: bool, only_ids: list[int] | None = None) -> None:
    agent = SalesAgent(client, gate, limit=10)
    print(f"\n[sales-agent] scanning draft quotations ...")
    outcomes = agent.run_once(auto_approve=auto_approve, only_ids=only_ids)
    if not outcomes:
        print("[sales-agent] no draft quotations found")
        return
    for o in outcomes:
        print(
            f"[sales-agent] {o['quotation']} (partner={o['partner']}, total={o['total']}) "
            f"-> request #{o['request_id']} decision={o['decision']}"
        )
    # show the pending request details for the human
    for o in outcomes:
        if o["decision"] == Decision.PENDING.value:
            print(f"[gate] request #{o['request_id']} waiting for HUMAN approval")
            print(f"[gate]   confirm sale.order {o['quotation']}")


def verify_state(client: OdooClient, record_id: int) -> str | None:
    rows = client.env["sale.order"].read([record_id], ["state", "name"])
    return rows[0]["state"] if rows else None


def cleanup(client: OdooClient, gate: ActionGate) -> None:
    ids = find_demo_records(client)
    if not ids:
        return
    rows = client.env["sale.order"].read(ids, ["id", "state"])
    confirmed = [r["id"] for r in rows if r["state"] == "sale"]
    if confirmed:
        cancel = AgentAction(
            model="sale.order",
            method="call",
            args={"ids": confirmed, "method": "action_cancel"},
            reason="sales agent demo cleanup: cancel confirmed demo quotations before removal",
        )
        request_id, decision = gate.submit(cancel)
        if decision is Decision.PENDING:
            gate.decide(request_id, True, comment="sales demo: approve cancel")
        print(f"[demo] cancelled {len(confirmed)} confirmed demo quotation(s)")
    action = AgentAction(
        model="sale.order",
        method="unlink",
        args={"ids": ids},
        reason="sales agent demo cleanup: remove the demo quotations so the business db stays clean",
    )
    request_id, decision = gate.submit(action)
    if decision is Decision.PENDING:
        gate.decide(request_id, True, comment="sales demo: approve cleanup")
    print(f"[demo] cleaned {len(ids)} demo quotation(s)")


def scan_only(client: OdooClient) -> None:
    agent = SalesAgent(client, ActionGate(client, mode=GateMode.MANUAL_APPROVAL), limit=10)
    quotations = agent.find_pending_quotations()
    print(f"[scan] {len(quotations)} draft quotation(s) in erp19:")
    for q in quotations:
        print(
            f"  {q['name']:>10} partner={(q.get('partner_id') or ['',''])[1]:<20} "
            f"total={q.get('amount_total')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="read-only scan of draft quotations")
    parser.add_argument("--approve", action="store_true", help="resolve every pending request as approved")
    parser.add_argument("--partner", type=int, default=3, help="partner id for the demo quotation")
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

    record_id = create_demo_quotation(client, gate, args.partner, auto_approve=args.approve)

    # only act on records this demo created — never on real business orders
    demo_ids = find_demo_records(client)
    confirm_via_agent(client, gate, auto_approve=args.approve, only_ids=demo_ids)

    if record_id is not None and args.approve:
        state = verify_state(client, record_id)
        print(f"\n[verify] quotation state after confirmation = {state}")

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
