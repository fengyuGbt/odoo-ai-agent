"""Minimal Odoo client wrapper used by AI agents.

Built on top of :mod:`odoorpc`, which exposes an ORM-like API over Odoo's
XML-RPC/JSON-RPC endpoints. This layer is intentionally thin: it normalizes
connection handling and provides the handful of read primitives the agent
skeleton needs. Later iterations will add ``create``/``write``/``call``
wrappers behind an explicit action-permission gate (read-only by default, as
decided in the project plan).
"""

from __future__ import annotations

from typing import Any

import odoorpc

from agent.config import OdooConfig


class OdooClient:
    """Authenticated session against an Odoo database."""

    def __init__(self, config: OdooConfig) -> None:
        self._config = config
        self._odoo = odoorpc.ODOO(
            host=config.host,
            port=config.port,
            protocol=config.protocol,
        )
        self._odoo.login(config.db, config.user, config.password)
        self.env = self._odoo.env

    def whoami(self) -> dict[str, Any]:
        """Return the authenticated user's basic identity."""
        user = self.env.user
        return {"id": user.id, "login": user.login, "name": user.name}

    def count(self, model: str, domain: list | None = None) -> int:
        """Number of records of *model* matching *domain* (empty = all)."""
        return self.env[model].search_count(domain or [])

    def read(
        self,
        model: str,
        domain: list | None = None,
        fields: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Read up to *limit* records of *model* matching *domain*."""
        ids = self.env[model].search(domain or [], limit=limit)
        return self.env[model].read(ids, fields or [])

    def close(self) -> None:
        """Release the remote session."""
        self._odoo.logout()
