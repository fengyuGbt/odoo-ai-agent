"""AI Agent configuration loader.

Reads Odoo connection settings from environment variables or a local ``.env``
file. Environment variables take precedence over the ``.env`` file, which is
convenient for containerized deployments (docker-compose ``env_file``).

The file ``.env`` is git-ignored on purpose: it holds credentials and must not
be committed to the repository. A template is provided as ``.env.example``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# Mapping: config field -> environment variable name
_ENV_MAP = {
    "host": "ODOO_HOST",
    "port": "ODOO_PORT",
    "db": "ODOO_DB",
    "user": "ODOO_USER",
    "password": "ODOO_PASSWORD",
    "protocol": "ODOO_PROTOCOL",
}


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` .env file (no external dependency)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class OdooConfig:
    """Connection settings for the Odoo instance the agent talks to."""

    host: str = "localhost"
    port: int = 8069
    db: str = "erp19"
    user: str = "admin"
    password: str = "admin123"
    protocol: str = "jsonrpc"

    @classmethod
    def load(cls, env_file: Path = DEFAULT_ENV_FILE) -> "OdooConfig":
        """Build a config from environment variables + an optional .env file."""
        file_values = _read_env_file(env_file)
        kwargs: dict[str, object] = {}
        for field, env_name in _ENV_MAP.items():
            raw = os.environ.get(env_name) or file_values.get(env_name)
            if raw is None:
                continue
            if field == "port":
                kwargs[field] = int(raw)
            else:
                kwargs[field] = raw
        return cls(**kwargs)
