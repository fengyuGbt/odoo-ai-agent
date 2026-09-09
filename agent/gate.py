"""Action permission gate for the AI operation layer.

The core rule of this project: **the AI runs the process, the human makes
the judgment.** The gate enforces it mechanically:

- read operations pass through freely (the agent may look at anything);
- write operations (create / write / unlink / call) are **never executed
  directly**: they are submitted as an :class:`AgentAction`, checked against
  the gate policy, and — when the policy requires it — must wait for an
  explicit human approval before touching Odoo.

This is the mechanism that guarantees the AI can never "walk the whole
process on its own". A rule-engine pre-check hook is provided and will be
wired to OCA ``base_tier_validation`` next, so that the same tier definitions
that gate human approvals can also feed the AI's pre-check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from agent.audit import AuditLog
from agent.odoo_client import OdooClient

# Methods that only read data and are always allowed without approval.
READ_METHODS: frozenset[str] = frozenset({"search", "read", "search_count", "name_get", "fields_get"})


class GateMode(str, Enum):
    READ_ONLY = "read_only"          # write actions are rejected outright
    MANUAL_APPROVAL = "manual_approval"  # write actions wait for a human
    UNRESTRICTED = "unrestricted"    # dev-only; never used in production


class Decision(str, Enum):
    ALLOWED = "allowed"              # read action, passed through
    BLOCKED = "blocked"              # rejected by policy (read_only mode)
    PENDING = "pending"              # waiting for human approval
    APPROVED = "approved"            # human approved, action executed
    REJECTED = "rejected"            # human rejected, action not executed


@dataclass
class AgentAction:
    """A single operation the agent wants to perform on Odoo."""

    model: str
    method: str                      # create / write / unlink / call
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""                 # why the AI wants to do this (for the human)
    precheck: list[str] = field(default_factory=list)  # rule-engine results

    @property
    def is_read(self) -> bool:
        return self.method in READ_METHODS

    def describe(self) -> str:
        return f"{self.method} on {self.model}: {self.args}"


# Signature for the rule-engine pre-check hook (to be wired to OCA tier
# validation). Returns a list of human-readable findings.
PreCheckHook = Callable[[OdooClient, AgentAction], list[str]]


class ActionGate:
    """Enforces the human-approval policy on agent actions."""

    def __init__(
        self,
        client: OdooClient,
        mode: GateMode = GateMode.MANUAL_APPROVAL,
        audit: AuditLog | None = None,
        precheck_hook: PreCheckHook | None = None,
    ) -> None:
        self.client = client
        self.mode = mode
        self.audit = audit or AuditLog(Path("audit/agent_audit.jsonl"))
        self.precheck_hook = precheck_hook
        self._pending: dict[int, AgentAction] = {}
        self._seq = 0

    # -- submission --------------------------------------------------------

    def submit(self, action: AgentAction) -> tuple[int, Decision]:
        """Submit an action. Returns (request_id, decision).

        Read actions are allowed immediately. Write actions are either
        blocked (read-only mode) or parked as a pending request that only
        :meth:`decide` can resolve.
        """
        self._seq += 1
        request_id = self._seq

        if action.is_read:
            self._audit(request_id, action, Decision.ALLOWED, "read method, auto-allowed")
            return request_id, Decision.ALLOWED

        # write action: run the rule-engine pre-check when a hook is wired
        if self.precheck_hook is not None:
            action.precheck = self.precheck_hook(self.client, action)

        if self.mode is GateMode.READ_ONLY:
            self._audit(request_id, action, Decision.BLOCKED, "write blocked by read_only mode")
            return request_id, Decision.BLOCKED

        if self.mode is GateMode.UNRESTRICTED:
            # dev-only shortcut; not used in production configuration
            self._audit(request_id, action, Decision.ALLOWED, "unrestricted dev mode")
            return request_id, Decision.ALLOWED

        self._pending[request_id] = action
        self._audit(request_id, action, Decision.PENDING, "waiting for human approval")
        return request_id, Decision.PENDING

    # -- human decision ----------------------------------------------------

    def decide(self, request_id: int, approved: bool, comment: str = "") -> Any | None:
        """Resolve a pending request. Only the human can call this."""
        action = self._pending.pop(request_id, None)
        if action is None:
            raise KeyError(f"no pending request #{request_id}")

        if not approved:
            self._audit(request_id, action, Decision.REJECTED, comment or "rejected by human")
            return None

        result = self._execute(action)
        self._audit(request_id, action, Decision.APPROVED, comment or "approved by human", result=result)
        return result

    # -- internals ----------------------------------------------------------

    def _execute(self, action: AgentAction) -> Any:
        model = self.client.env[action.model]
        if action.method == "create":
            record_id = model.create(action.args.get("values", {}))
            return {"id": record_id}
        if action.method == "write":
            ids = action.args.get("ids", [])
            return model.write(ids, action.args.get("values", {}))
        if action.method == "unlink":
            return model.unlink(action.args.get("ids", []))
        if action.method == "call":
            target = model.browse(action.args.get("ids", [])) if action.args.get("ids") else model
            method = action.args.get("method")
            return getattr(target, method)(*action.args.get("pos_args", []), **action.args.get("kwargs", {}))
        raise ValueError(f"unsupported method: {action.method}")

    def _audit(self, request_id: int, action: AgentAction, decision: Decision, note: str, **extra: Any) -> None:
        self.audit.record(
            {
                "request_id": request_id,
                "decision": decision.value,
                "note": note,
                "action": {
                    "model": action.model,
                    "method": action.method,
                    "args": action.args,
                    "reason": action.reason,
                    "precheck": action.precheck,
                },
                **extra,
            }
        )
