#!/usr/bin/env python3
"""AI Agent skeleton: verify the Odoo connectivity closed loop.

This is the minimal end-to-end check of the whole project's "AI operation
layer" premise: an independent service (outside Odoo) talks to the Odoo API,
authenticates, and reads live business data. Nothing is written in this step;
the agent is read-only until the action-permission gate lands.

Usage:
    python main.py

Exit code is 0 when the connection and all model checks succeed.
"""

from __future__ import annotations

from agent.config import OdooConfig
from agent.odoo_client import OdooClient

# Models the skeleton probes to prove the data path is alive.
CHECK_MODELS: list[str] = [
    "res.users",
    "res.partner",
    "sale.order",
    "purchase.order",
    "stock.picking",
]


def main() -> int:
    cfg = OdooConfig.load()
    client = OdooClient(cfg)

    me = client.whoami()
    print(f"[OK] connected to {cfg.host}:{cfg.port}  db={cfg.db}  user={me['login']} ({me['name']})")
    print("[OK] probing live record counts:")
    for model in CHECK_MODELS:
        total = client.count(model)
        print(f"      {model:<16} {total}")
    client.close()
    print("[OK] closed session. minimal closed loop works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
