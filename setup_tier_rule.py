#!/usr/bin/env python3
"""Create a demo tier-approval rule on sale.order.

The rule: quotations for partner #3 (the partner the demo uses) require a
human approval step. It is created through the Odoo API so the pre-check
hook can read it — this is configuration, not an agent action.

Usage:
    venv/bin/python setup_tier_rule.py
"""

from __future__ import annotations

from agent.config import OdooConfig
from agent.odoo_client import OdooClient

RULE_NAME = "AI demo: partner 3 quotations need approval"
RULE_DOMAIN = "[('partner_id', '=', 3)]"


def main() -> int:
    cfg = OdooConfig.load()
    client = OdooClient(cfg)
    env = client.env

    ir_model = env["ir.model"].search([("model", "=", "sale.order")], limit=1)
    if not ir_model:
        print("ERROR: ir.model for sale.order not found")
        return 1

    existing = env["tier.definition"].search(
        [("model", "=", "sale.order"), ("name", "=", RULE_NAME)]
    )
    if existing:
        print(f"demo rule already exists (id={existing[0]})")
        return 0

    rule_id = env["tier.definition"].create(
        {
            "model_id": ir_model[0],
            "name": RULE_NAME,
            "definition_domain": RULE_DOMAIN,
            "review_type": "individual",
            "reviewer_id": env.user.id,
            "approve_sequence": False,
        }
    )
    print(f"demo rule created (id={rule_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
