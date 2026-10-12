"""
Who may do what. Every permission check in the app goes through can().

Roles (one per user) plus a separate admin flag:

  Requester      Raise tickets (choosing the vendor and the items) and view
                 all of them. Edit or cancel their own ticket until it
                 reaches Approval Pending.
  Purchase team  Run the procurement process: add the prices, change the
                 vendor or items until approval, move stages, tracking,
                 hold / send back / cancel / reopen, archive; manage
                 vendors, projects and purchase orders.
  Team Lead      Approve or reject tickets estimated below ₹25,000.
  Approver       Approve or reject tickets estimated at ₹25,000 or more (Chirag).
  Admin (flag)   Everything the purchase team can do, plus manage users,
                 company settings and permanently delete. Admin does NOT include approving;
                 assign an approval role separately.

Approvers may approve their own tickets while ALLOW_SELF_APPROVAL is True
(config.py); the timeline then marks the approval "Self-approved".
"""

from decimal import Decimal

from flask import abort, current_app, g

from .constants import APPROVAL_STAGE, PROCUREMENT_FLOW, STATE_ACTIVE
from .models import ROLE_APPROVER, ROLE_PURCHASE, ROLE_TEAM_LEAD

APPROVAL_INDEX = PROCUREMENT_FLOW.index(APPROVAL_STAGE)
APPROVAL_LIMIT = Decimal("25000.00")


def is_staff(user):
    """Runs the procurement process (purchase team or admin)."""
    return user.role == ROLE_PURCHASE or user.is_admin


def owns(user, ticket):
    return (
        ticket is not None and ticket.created_by_id is not None and ticket.created_by_id == user.id
    )


def before_approval(ticket):
    return ticket.stage_index is not None and ticket.stage_index < APPROVAL_INDEX


def at_approval(ticket):
    return ticket is not None and ticket.status == APPROVAL_STAGE


def requisition_open(ticket):
    """Vendor, items and prices can change up to Approval Pending, then they're locked."""
    return (
        ticket.state == STATE_ACTIVE
        and not ticket.archived
        and ticket.stage_index is not None
        and ticket.stage_index <= APPROVAL_INDEX
    )


def can_approve(user, ticket):
    if not at_approval(ticket):
        return False
    if user.role != approval_role(ticket):
        return False
    return not owns(user, ticket) or current_app.config["ALLOW_SELF_APPROVAL"]


def approval_role(ticket):
    """Approval role; exactly ₹25,000 routes to Chirag."""
    estimate = ticket.estimate
    if estimate is not None and estimate.total < APPROVAL_LIMIT:
        return ROLE_TEAM_LEAD
    return ROLE_APPROVER


def can(action, ticket=None, user=None):
    user = user if user is not None else g.get("user")
    if user is None or not user.active:
        return False

    staff = is_staff(user)

    if action == "create":
        return True
    if action == "edit":
        return staff or (
            owns(user, ticket)
            and before_approval(ticket)
            and ticket.state == STATE_ACTIVE
            and not ticket.archived
        )
    if action == "advance":
        # moving past Approval Pending is approving
        return can_approve(user, ticket) if at_approval(ticket) else staff
    if action == "send-back":
        return staff or can_approve(user, ticket)
    if action == "cancel":
        return (
            staff or can_approve(user, ticket) or (owns(user, ticket) and before_approval(ticket))
        )
    if action in ("hold", "resume", "reopen", "tracking", "archive", "restore"):
        return staff
    # the requisition is locked once approved, so the approved figures stay
    if action == "edit_items":
        # vendor and item rows: the engineer who raised it (before approval) or purchase
        return requisition_open(ticket) and (
            staff or (owns(user, ticket) and before_approval(ticket))
        )
    if action == "pricing":
        # estimated price and GST per item, from the vendor's quotation
        return staff and requisition_open(ticket)
    # purchasing: vendors, projects and POs (everyone signed in can view them)
    if action in ("manage_vendors", "manage_projects", "manage_pos"):
        return staff
    if action in ("delete", "manage_users", "company_settings"):
        return user.is_admin

    raise ValueError(f"Unknown permission: {action}")


def require(action, ticket=None):
    if not can(action, ticket):
        abort(403)
