"""Rule-engine pre-check backed by OCA ``base_tier_validation``.

When the AI submits a write action, this hook reads the tier-approval rules
configured in Odoo (``tier.definition``) and evaluates them against the
target record, before anything is executed. The findings — which rules hit,
who must approve, whether approvals are sequential — are attached to the
pending request so the human sees the AI's due-diligence result before
deciding.

This is the "AI 按事前定的规则做核实" part of the project: the same tier
definitions that gate human approvals also drive the AI's pre-check.
"""

from __future__ import annotations

import ast
from typing import Any

from agent.gate import AgentAction
from agent.odoo_client import OdooClient

# Ops that can be evaluated against create-values without a live record.
_SIMPLE_OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "not in", "like")


def get_tier_rules(client: OdooClient, model: str) -> list[dict[str, Any]]:
    """Read active tier rules for *model* (read-only).

    Degrades gracefully when OCA tier validation is not installed (the
    ``tier.definition`` model is missing) — precheck is an enhancement,
    the action gate itself does not depend on it.
    """
    try:
        rules = client.env["tier.definition"].search(
            [("model", "=", model), ("active", "=", True)], order="sequence"
        )
    except Exception:
        return []
    if not rules:
        return []
    return client.env["tier.definition"].read(
        rules,
        [
            "name",
            "definition_domain",
            "review_type",
            "reviewer_id",
            "reviewer_group_id",
            "approve_sequence",
        ],
    )


def _parse_domain(raw: str | None) -> list[Any] | None:
    if not raw:
        return None
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None
    return parsed if isinstance(parsed, list) and parsed else None


def _approver_text(rule: dict[str, Any]) -> str:
    review_type = rule.get("review_type") or "individual"
    if review_type == "group":
        group = rule.get("reviewer_group_id") or []
        if group:
            return f"组:{group[1]}"
    reviewer = rule.get("reviewer_id") or []
    if reviewer:
        return f"用户:{reviewer[1]}"
    return "未指定审批人"


def _domain_hits_existing(
    client: OdooClient, model: str, rule: dict[str, Any], record_ids: list[int]
) -> bool | None:
    """Test a rule domain against existing records via Odoo's search."""
    domain = _parse_domain(rule.get("definition_domain"))
    if domain is None:
        return None
    hit_ids = client.env[model].search(domain + [("id", "in", record_ids)])
    return bool(hit_ids)


def _domain_matches_create_values(rule: dict[str, Any], values: dict[str, Any]) -> bool | None:
    """Best-effort evaluation of a flat domain against create values.

    Returns True/False when every clause could be evaluated; None when some
    clause depends on fields that only exist after creation (e.g. computed
    totals), in which case the human is asked to review manually.
    """
    domain = _parse_domain(rule.get("definition_domain"))
    if domain is None:
        return None
    evaluated: list[Any] = []
    unverifiable: list[Any] = []
    for clause in domain:
        if isinstance(clause, str):  # '&' | '!' operators: keep for manual review
            unverifiable.append(clause)
            continue
        if not (isinstance(clause, (list, tuple)) and len(clause) == 3):
            continue
        field, op, expected = clause
        if field == "id" or field not in values:
            unverifiable.append(clause)
            continue
        actual = values[field]
        try:
            if op == "=" and actual == expected:
                evaluated.append(clause)
            elif op == "!=" and actual != expected:
                evaluated.append(clause)
            elif op == ">" and actual > expected:
                evaluated.append(clause)
            elif op == ">=" and actual >= expected:
                evaluated.append(clause)
            elif op == "<" and actual < expected:
                evaluated.append(clause)
            elif op == "<=" and actual <= expected:
                evaluated.append(clause)
            elif op == "in" and actual in expected:
                evaluated.append(clause)
            elif op == "not in" and actual not in expected:
                evaluated.append(clause)
            else:
                unverifiable.append(clause)
        except TypeError:
            unverifiable.append(clause)
    if not evaluated and not unverifiable:
        return None
    return bool(evaluated) and not unverifiable


def make_precheck_hook(client: OdooClient):
    """Build a gate-compatible pre-check hook for *client*."""

    def hook(_client: OdooClient, action: AgentAction) -> list[str]:
        model = action.model
        rules = get_tier_rules(_client, model)
        if not rules:
            return [f"pre-check: no tier rules configured for {model}; nothing gated"]

        findings = [f"pre-check: {len(rules)} tier rule(s) apply to {model}:"]
        for rule in rules:
            if action.method == "create":
                hit = _domain_matches_create_values(rule, action.args.get("values", {}))
            else:
                hit = _domain_hits_existing(_client, model, rule, action.args.get("ids", []))
            if hit is True:
                seq = "（顺序审批）" if rule.get("approve_sequence") else "（任一审批人即可）"
                findings.append(
                    f"  [命中] {rule['name']} → 需 {_approver_text(rule)} 审批{seq}"
                )
            elif hit is False:
                findings.append(f"  [未命中] {rule['name']}")
            else:
                findings.append(
                    f"  [无法评估] {rule['name']}（domain 含创建后才可用的字段，建议人工复核）"
                )
        return findings

    return hook
