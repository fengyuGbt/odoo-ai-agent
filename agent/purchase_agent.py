"""Purchase domain agent — watches draft purchase orders, confirms through the gate.

Second domain agent of the AI operation layer. In the end-to-end chain the
purchase agent picks up what the sales flow produced (procurement needs →
purchase orders → supplier order confirmation → receiving), and — like every
agent in this project — never confirms anything on its own: the confirmation
is a pending request waiting for a human decision.
"""

from __future__ import annotations

from agent.domain_agent import DomainAgent


class PurchaseAgent(DomainAgent):
    model = "purchase.order"
    state_field = "state"
    draft_state = "draft"
    confirm_method = "button_confirm"
    reason_template = (
        "AI proposes confirming this purchase order; "
        "human decides whether the terms (supplier, price, date) are acceptable."
    )
