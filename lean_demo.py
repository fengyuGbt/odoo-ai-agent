#!/usr/bin/env python3
"""Lean monitor demo: delivery / inventory / waste management report.

  default   -- compute the full lean report (read-only, no writes)
  --approve -- additionally post a follow-up note on the most overdue
               order through the gate (human approves it)

Usage:
    venv/bin/python lean_demo.py
    venv/bin/python lean_demo.py --approve
"""

from __future__ import annotations

import argparse

from agent.audit import AuditLog
from agent.config import OdooConfig
from agent.gate import ActionGate, Decision, GateMode
from agent.lean_monitor import LeanMonitorAgent
from agent.odoo_client import OdooClient
from agent.tier_precheck import make_precheck_hook
from pathlib import Path

AUDIT_PATH = Path("audit/agent_audit.jsonl")


def print_report(report: dict) -> None:
    print(f"\n[lean] report generated at {report['generated_at']} (UTC)")

    d = report["delivery"]
    print("\n── 1. 交货表现 (delivery) ──")
    print(f"   已确认订单 {d['confirmed_orders']} / 已交付 {d['delivered']} / 准时 {d['on_time']}")
    if d["on_time_rate"] is None:
        print("   准时交付率: 无已交付样本（没有完成的出库单）")
    else:
        print(f"   准时交付率: {d['on_time_rate']}%")
    if d["overdue"]:
        print(f"   延期未交付订单 {len(d['overdue'])} 个：")
        for r in d["overdue"]:
            print(f"     {r['sale']:<8} partner={r['partner']:<14} 延期 {r['days_late']} 天")

    inv = report["inventory"]
    print("\n── 2. 库存健康 (inventory) ──")
    print(f"   库存总价值: {inv['total_stock_value']}（按产品净库存 × 成本）")
    if not inv["items"]:
        print("   无库存")
    for it in inv["items"]:
        idle = "从无出库" if it["idle_days"] is None else f"闲置 {it['idle_days']} 天"
        print(
            f"     {it['product']:<10} 净库存={it['qty']:<6} 成本={it['cost']:<6} "
            f"价值={it['value']:<8} {idle}"
        )
        for flag in it.get("flags", []):
            print(f"       ⚠ {flag}")
    if inv.get("anomalies"):
        print(f"   库存异常 {len(inv['anomalies'])} 项（需人工核实）")

    w = report["waste"]
    print("\n── 3. 浪费信号 (waste) ──")
    print(f"   长期停留草稿销售单: {len(w['aged_draft_sales'])} 个")
    for r in w["aged_draft_sales"][:8]:
        print(f"     {r['name']:<8} 停留 {r['age_days']} 天")
    if len(w["aged_draft_sales"]) > 8:
        print(f"     ... 另有 {len(w['aged_draft_sales']) - 8} 个")
    print(f"   长期停留草稿采购单: {len(w['aged_draft_purchases'])} 个")
    print(f"   退货单（缺陷浪费）: {w['return_count']} 个")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve", action="store_true", help="post a gated follow-up on the worst overdue order")
    args = parser.parse_args()

    cfg = OdooConfig.load()
    client = OdooClient(cfg)
    audit = AuditLog(AUDIT_PATH)
    gate = ActionGate(
        client,
        mode=GateMode.MANUAL_APPROVAL,
        audit=audit,
        precheck_hook=make_precheck_hook(client),
    )
    print(f"[agent] connected as {client.whoami()['login']}")

    monitor = LeanMonitorAgent(client, gate)
    report = monitor.full_report()
    print_report(report)

    if args.approve:
        overdue = report["delivery"]["overdue"]
        if not overdue:
            print("\n[follow-up] no overdue order to follow up")
        else:
            worst = overdue[0]
            so_ids = client.env["sale.order"].search([("name", "=", worst["sale"])])
            print(f"\n── follow-up: 给最严重延期单 {worst['sale']}（延期 {worst['days_late']} 天）登记提醒 ──")
            note = (
                f"精益监控提醒：该订单已延期 {worst['days_late']} 天仍未交付，"
                "请人工核实原因并推进（AI 只提醒，决策在人）。"
            )
            action = monitor.plan_followup("sale.order", so_ids[0], note)
            rid, decision = gate.submit(action)
            print(f"   submit -> request #{rid} decision={decision.value}")
            if decision is Decision.PENDING:
                gate.decide(rid, True, comment="lean demo: approve follow-up note")
                print("   follow-up note posted after human approval")

        print("\n[audit] last events:")
        for event in audit.read_all(limit=5):
            print(
                f"  {event.get('ts', '?'):>24}  #{event.get('request_id')} "
                f"{event.get('decision'):<8} {event.get('action', {}).get('model')}."
                f"{event.get('action', {}).get('method')}"
            )

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
