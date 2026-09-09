"""Sales domain agent — watches draft quotations, confirms through the gate."""

from __future__ import annotations

from agent.domain_agent import DomainAgent


class SalesAgent(DomainAgent):
    model = "sale.order"
    state_field = "state"
    draft_state = "draft"
    confirm_method = "action_confirm"
    read_fields = ["id", "name", "partner_id", "amount_total", "state"]
    reason_template = (
        "AI proposes confirming this quotation after order intake; "
        "human decides whether it meets the agreed conditions."
    )

    def find_pending_quotations(self):
        """Alias kept for backward compatibility with earlier docs."""
        return self.find_pending()
