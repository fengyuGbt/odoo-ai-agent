#!/usr/bin/env python3
"""Demo: the action permission gate and human-approval loop.

This demonstrates the core rule of the project — **AI runs the process, the
human makes the judgment**:

1. The AI submits a write action (e.g. creating a draft quotation) with a
   reason. The gate parks it as *pending*; nothing touches Odoo.
2. The human reviews the request (reason + rule pre-check) and approves or
   rejects it.
3. Only then is the action executed — and every step is written to the
   audit trail.

Run modes:

    python demo_approval.py              # submit only; stop at "waiting for human"
    python demo_approval.py --approve    # submit, then approve and execute
    python demo_approval.py --reject     # submit, then reject (nothing executed)
    python demo_approval.py --read-only  # run the gate in read_only mode

The write action used here is intentionally harmless: it creates a *draft*
quotation (no workflow is triggered) and then deletes it again when the demo
runs with --approve, so the business database stays clean.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.gate import ActionGate, AgentAction, Decision, GateMode
from agent.odoo_client import OdooClient
from agent.tier_precheck import make_precheck_hook

AUDIT_PATH = Path("audit/agent_audit.jsonl")


def cleanup_stale(client: OdooClient, gate: ActionGate) -> None:
    """Remove quotations left behind by an earlier interrupted demo run.

    Everything still goes through the gate: the cleanup is itself a write
    action that requires human approval.
    """
    stale = client.env["sale.order"].search([("note", "like", "%AI-drafted quotation%")])
    if not stale:
        return
    action = AgentAction(
        model="sale.order",
        method="unlink",
        args={"ids": stale},
        reason="demo cleanup: remove stale AI-drafted quotations from an earlier interrupted run",
    )
    request_id, decision = gate.submit(action)
    if decision is Decision.PENDING:
        gate.decide(request_id, True, comment="demo cleanup approval")
        print(f"[gate] cleaned {len(stale)} stale quotation(s) left by a previous interrupted run")


def build_action(client: OdooClient, partner_id: int) -> AgentAction:
    return AgentAction(        model="sale.order",
        method="create",
        args={
            "values": {
                "partner_id": partner_id,
                "note": "AI-drafted quotation (demo of the approval gate)",
            }
        },
        reason="AI drafts the quotation after order intake; human decides whether it is acceptable.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partner", type=int, default=3, help="partner id for the demo quotation (3 hits the demo tier rule)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--approve", action="store_true", help="approve the pending request")
    group.add_argument("--reject", action="store_true", help="reject the pending request")
    group.add_argument("--read-only", action="store_true", help="run gate in read_only mode")
    args = parser.parse_args()

    cfg = OdooConfig.load()
    client = OdooClient(cfg)
    audit = AuditLog(AUDIT_PATH)

    mode = GateMode.READ_ONLY if args.read_only else GateMode.MANUAL_APPROVAL
    gate = ActionGate(client, mode=mode, audit=audit, precheck_hook=make_precheck_hook(client))

    cleanup_stale(client, gate)

    print(f"[agent] connected as {client.whoami()['login']} (mode={mode.value})")
    action = build_action(client, args.partner)
    print(f"[agent] submitting action: {action.describe()}")
    print(f"[agent] reason: {action.reason}")

    request_id, decision = gate.submit(action)
    print(f"[agent] submit -> decision={decision.value}")

    if decision is Decision.BLOCKED:
        print("[gate] write blocked by read_only mode; nothing executed. (AI stays read-only here.)")
        client.close()
        return 0

    if decision is Decision.PENDING:
        print(f"[gate] request #{request_id} waiting for HUMAN approval")
        print(f"[gate]   action : {action.describe()}")
        print(f"[gate]   reason : {action.reason}")
        for finding in action.precheck:
            print(f"[gate]   check  : {finding}")
        if args.approve:
            print(f"[human] approving request #{request_id} ...")
            result = gate.decide(request_id, True, comment="demo human approval")
            print(f"[gate] executed, result={result}")
            # cleanup: delete the demo quotation so the business db stays clean
            quote_id = result["id"]
            cleanup = AgentAction(
                model="sale.order",
                method="unlink",
                args={"ids": [quote_id]},
                reason="demo cleanup: remove the AI-drafted quotation",
            )
            rid2, dec2 = gate.submit(cleanup)
            if dec2 is Decision.PENDING:
                gate.decide(rid2, True, comment="demo cleanup approval")
                print(f"[gate] demo quotation #{quote_id} removed (db left clean)")
        elif args.reject:
            print(f"[human] rejecting request #{request_id} ...")
            gate.decide(request_id, False, comment="demo human rejection")
            print("[gate] nothing executed; request rejected")
        else:
            print("[gate] leaving request pending (run with --approve or --reject to resolve)")

    print("\n[audit] last events:")
    for event in audit.read_all(limit=6):
        print(f"  {event['ts']}  #{event['request_id']} {event['decision']:<9} {event['action']['model']}.{event['action']['method']}  {event['note']}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
