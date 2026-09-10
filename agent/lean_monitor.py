"""Lean operations monitor — delivery, inventory and waste signals.

The management-layer agent. Unlike the flow agents, it is primarily
read-only: it computes lean-performance metrics from live Odoo data and
surfaces the exceptions so a human manager can judge. This is the
"从精益角度，避免成本浪费" part of the vision.

Three lenses:

1. **delivery**: on-time delivery rate, overdue undelivered orders,
   delivery lead time;
2. **inventory**: stock value per product, slow-moving / idle stock;
3. **waste**: aged draft documents (waiting waste), overdue pickings,
   returns (defect waste).

The only write action is an optional follow-up note on a problem
record, and it still goes through the gate — AI presents the analysis,
humans make the operating decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agent.gate import ActionGate, AgentAction, Decision
from agent.odoo_client import OdooClient

# A document older than this without progress counts as a waste signal.
AGED_DAYS = 14
SLOW_MOVING_DAYS = 90


class LeanMonitorAgent:
    """Compute lean metrics and propose follow-ups through the gate."""

    def __init__(self, client: OdooClient, gate: ActionGate | None = None) -> None:
        self.client = client
        self.gate = gate
        self.now = datetime.utcnow()

    # ------------------------------------------------------------------
# delivery performance
    # ------------------------------------------------------------------

    def delivery_performance(self) -> dict[str, Any]:
        so_ids = self.client.env["sale.order"].search([("state", "=", "sale")])
        sales = (
            self.client.env["sale.order"].read(
                so_ids, ["name", "partner_id", "date_order", "commitment_date", "amount_total"]
            )
            if so_ids
            else []
        )

        delivered = 0
        on_time = 0
        overdue: list[dict[str, Any]] = []
        for so in sales:
            pids = self.client.env["stock.picking"].search([("origin", "=", so["name"])])
            if not pids:
                continue
            pick_rows = self.client.env["stock.picking"].read(
                pids, ["state", "scheduled_date", "date_done"]
            )
            is_done = all(p["state"] == "done" for p in pick_rows)
            if is_done:
                delivered += 1
                last_done = max((p.get("date_done") or "") for p in pick_rows)
                due = so.get("commitment_date") or min(
                    (p.get("scheduled_date") or "") for p in pick_rows
                )
                if last_done and due and last_done <= due:
                    on_time += 1
            else:
                earliest = min((p.get("scheduled_date") or "9999") for p in pick_rows)
                if earliest and earliest != "9999":
                    due_dt = self._parse(earliest)
                    if due_dt and due_dt < self.now:
                        overdue.append(
                            {
                                "sale": so["name"],
                                "partner": (so.get("partner_id") or ["", ""])[1],
                                "due": earliest,
                                "days_late": (self.now - due_dt).days,
                            }
                        )

        total = len(sales)
        rate = round(100.0 * on_time / delivered, 1) if delivered else None
        return {
            "confirmed_orders": total,
            "delivered": delivered,
            "on_time": on_time,
            "on_time_rate": rate,
            "overdue": sorted(overdue, key=lambda r: -r["days_late"]),
        }

    # ------------------------------------------------------------------
# inventory health
    # ------------------------------------------------------------------

    def inventory_health(self) -> dict[str, Any]:
        quant_ids = self.client.env["stock.quant"].search([("quantity", "!=", 0)])
        quants = (
            self.client.env["stock.quant"].read(quant_ids, ["product_id", "quantity", "location_id"])
            if quant_ids
            else []
        )

        # Aggregate per product (a product can have quants in several locations).
        by_product: dict[int, dict[str, Any]] = {}
        for q in quants:
            product = q.get("product_id")
            if not product:
                continue
            pid = product[0]
            bucket = by_product.setdefault(pid, {"name": product[1], "net_qty": 0.0, "negative": 0.0})
            qty = q.get("quantity") or 0.0
            bucket["net_qty"] += qty
            if qty < 0:
                bucket["negative"] += qty

        rows: list[dict[str, Any]] = []
        anomalies: list[str] = []
        total_value = 0.0
        for pid, bucket in by_product.items():
            prod_rows = self.client.env["product.product"].read([pid], ["standard_price"])
            cost = prod_rows[0].get("standard_price", 0.0) if prod_rows else 0.0
            net = bucket["net_qty"]
            value = net * (cost or 0.0)
            total_value += value

            flags: list[str] = []
            if bucket["negative"]:
                flags.append("存在负库存（数据/流程异常）")
                anomalies.append(f"{bucket['name']}: 负库存合计 {bucket['negative']}")
            if not cost:
                flags.append("成本未维护（价值口径不可信）")
                anomalies.append(f"{bucket['name']}: standard_price=0")

            last_out = self._last_outbound_date(pid)
            rows.append(
                {
                    "product": bucket["name"],
                    "qty": net,
                    "cost": cost,
                    "value": round(value, 2),
                    "last_outbound": last_out,
                    "idle_days": self._idle_days(last_out),
                    "flags": flags,
                }
            )

        return {
            "total_stock_value": round(total_value, 2),
            "items": sorted(rows, key=lambda r: -(r["value"] or 0.0)),
            "anomalies": anomalies,
        }

    def _last_outbound_date(self, product_id: int) -> str | None:
        move_ids = self.client.env["stock.move"].search(
            [
                ("product_id", "=", product_id),
                ("picking_id.picking_type_code", "=", "outgoing"),
                ("state", "=", "done"),
            ],
            order="date desc",
            limit=1,
        )
        if not move_ids:
            return None
        rows = self.client.env["stock.move"].read(move_ids, ["date"])
        return rows[0].get("date") if rows else None

    def _idle_days(self, last_out: str | None) -> int | None:
        if not last_out:
            return None
        dt = self._parse(last_out)
        return (self.now - dt).days if dt else None

    # ------------------------------------------------------------------
# waste signals
    # ------------------------------------------------------------------

    def waste_signals(self) -> dict[str, Any]:
        aged_drafts = self._aged_documents("sale.order", [("state", "=", "draft")])
        aged_po = self._aged_documents("purchase.order", [("state", "=", "draft")])

        return_ids = self.client.env["stock.picking"].search(
            [("origin", "like", "%Return%")]
        )
        return {
            "aged_draft_sales": aged_drafts,
            "aged_draft_purchases": aged_po,
            "return_count": len(return_ids),
        }

    def _aged_documents(self, model: str, domain: list) -> list[dict[str, Any]]:
        ids = self.client.env[model].search(domain)
        if not ids:
            return []
        rows = self.client.env[model].read(ids, ["name", "create_date"])
        aged: list[dict[str, Any]] = []
        for r in rows:
            created = self._parse(r.get("create_date"))
            if created and (self.now - created).days >= AGED_DAYS:
                aged.append(
                    {"name": r.get("name"), "age_days": (self.now - created).days}
                )
        return sorted(aged, key=lambda r: -r["age_days"])

    # ------------------------------------------------------------------
# report + follow-up
    # ------------------------------------------------------------------

    def full_report(self) -> dict[str, Any]:
        return {
            "generated_at": self.now.isoformat(),
            "delivery": self.delivery_performance(),
            "inventory": self.inventory_health(),
            "waste": self.waste_signals(),
        }

    def plan_followup(self, model: str, record_id: int, note: str) -> AgentAction:
        return AgentAction(
            model=model,
            method="call",
            args={
                "ids": [record_id],
                "method": "message_post",
                "kwargs": {"body": note},
            },
            reason=f"lean monitor: post follow-up note after human review",
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _parse(value: Any) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
