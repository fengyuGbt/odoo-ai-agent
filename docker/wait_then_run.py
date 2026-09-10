#!/usr/bin/env python3
"""Wait until Odoo answers, then exec the command passed as argv.

Odoo spends a couple of minutes initializing its database on first
boot; the ai-agent container must not race it. Uses only the stdlib
(python:3.12-slim ships no curl).
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from urllib.error import URLError

HOST = os.environ.get("ODOO_HOST", "odoo")
PORT = os.environ.get("ODOO_PORT", "8069")
URL = f"http://{HOST}:{PORT}/web/database/selector"
DEADLINE_SECONDS = 600


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: wait_then_run.py <command> [args...]", file=sys.stderr)
        return 2

    started = time.monotonic()
    while True:
        try:
            with urllib.request.urlopen(URL, timeout=10) as resp:
                if resp.status == 200:
                    print(f"[wait] Odoo is up after {int(time.monotonic() - started)}s")
                    break
        except Exception as exc:
            # Odoo may be busy initializing (slow/timeout) or not listening
            # yet — treat every failure as "not ready" and keep waiting.
            print(f"[wait] not ready ({type(exc).__name__}), retrying in 5s ...")
            if time.monotonic() - started > DEADLINE_SECONDS:
                print("[wait] timed out waiting for Odoo", file=sys.stderr)
                return 1
            time.sleep(5)
            continue
        if time.monotonic() - started > DEADLINE_SECONDS:
            print("[wait] timed out waiting for Odoo", file=sys.stderr)
            return 1

    os.execvp(sys.argv[1], sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
