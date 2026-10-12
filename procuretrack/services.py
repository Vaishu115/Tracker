"""
Business logic: creating tickets, moving them through the workflow and
recording every change, and who made it, in status_history.

Views check permissions (permissions.py), then call these functions.
Functions add their changes to the session and the calling view commits,
except create_ticket(), which commits itself so it can retry on a
duplicate ticket number.

Rule: every change to a ticket goes through a function here, so that it is
always recorded in the timeline with the user who made it.
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .constants import (
    APPROVAL_STAGE,
    PROCUREMENT_FLOW,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_ON_HOLD,
)
from .extensions import db
from .models import (
    ROLE_APPROVER,
    ROLE_TEAM_LEAD,
    ProcurementRequest,
    Project,
    StatusHistory,
    TicketItem,
    Vendor,
)
from .money import format_amount


class WorkflowError(Exception):
    """An action that isn't allowed in the ticket's current state."""


FIELD_LABELS = {
    "title": "Title",
    "project_id": "Project",
    "owner": "Owner",
    "category": "Category",
    "procurement_type": "Procurement type",
    "priority": "Priority",
    "required_by": "Required by",
    "description": "Description",
    "recommended_vendor_id": "Vendor",
    "selected_vendor": "Vendor",
    "po_number": "PO number",
    "payment_status": "Payment status",
    "fulfilment_status": "Fulfilment status",
    "expected_delivery": "Expected delivery",
}


_CURRENT = object()


def log_event(ticket, user, event_type, comment=None, old_status=_CURRENT, new_status=_CURRENT):
    """Record an event. Statuses default to the ticket's current stage."""
    db.session.add(
        StatusHistory(
            ticket_id=ticket.ticket_id,
            timestamp=datetime.now(),
            event_type=event_type,
            old_status=ticket.status if old_status is _CURRENT else old_status,
            new_status=ticket.status if new_status is _CURRENT else new_status,
            comment=comment or None,
            user_id=user.id if user else None,
            actor_name=user.name if user else None,
        )
    )


def _display(value, field=None):
    if value is None or value == "":
        return "—"
    if field == "project_id":
        project = db.session.get(Project, value)
        return project.name if project else "—"
    if field == "recommended_vendor_id":
        vendor = db.session.get(Vendor, value)
        return vendor.name if vendor else "—"
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def apply_changes(ticket, data):
    """Set fields from data and return a readable list of what changed."""
    changes = []
    for field in (f for f in FIELD_LABELS if f in data):  # listed in form order
        new_value = data[field]
        old_value = getattr(ticket, field)
        if (old_value or None) != (new_value or None):
            if field == "description":
                changes.append("Description updated")
            else:
                changes.append(
                    f"{FIELD_LABELS[field]}: {_display(old_value, field)} → "
                    f"{_display(new_value, field)}"
                )
            setattr(ticket, field, new_value)
    return changes


# ---- creation ----


def next_ticket_id(year):
    prefix = f"PROC-{year}-"
    existing = db.session.scalars(
        select(ProcurementRequest.ticket_id).where(ProcurementRequest.ticket_id.like(prefix + "%"))
    )
    numbers = [int(tid.rsplit("-", 1)[1]) for tid in existing if tid.rsplit("-", 1)[1].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def create_ticket(data, items, user, attempts=3):
    """
    Create a ticket with its items and first history entry, and commit.
    Retries if two people submit at the same moment and get the same number.
    """
    new_vendor = data.pop("new_vendor", None)
    for attempt in range(attempts):
        vendor_note = _add_new_vendor(data, new_vendor, user)
        ticket = ProcurementRequest(
            ticket_id=next_ticket_id(datetime.now().year),
            request_date=datetime.now(),
            status=PROCUREMENT_FLOW[0],
            state=STATE_ACTIVE,
            created_by_id=user.id,
            **data,
        )
        for position, row in enumerate(items, start=1):
            ticket.items.append(
                TicketItem(
                    position=position,
                    description=row["description"],
                    quantity=row["quantity"],
                    unit=row["unit"],
                )
            )
        db.session.add(ticket)
        comment = "Procurement requirement created" + (f"\n{vendor_note}" if vendor_note else "")
        log_event(ticket, user, "STATUS_CHANGE", comment, old_status=None)
        try:
            db.session.commit()
            return ticket
        except IntegrityError:
            db.session.rollback()
            if attempt == attempts - 1:
                raise


# ---- workflow actions ----


def advance(ticket, user, comment):
    if not ticket.can_advance:
        raise WorkflowError("This case can't be advanced in its current state.")
    missing = ticket.missing_for_approval
    if PROCUREMENT_FLOW[ticket.stage_index + 1] == APPROVAL_STAGE and missing:
        raise WorkflowError(f"Fill in {' and '.join(missing)} before sending for approval.")
    old = ticket.status
    ticket.status = PROCUREMENT_FLOW[ticket.stage_index + 1]
    if old == APPROVAL_STAGE:
        if ticket.created_by_id == user.id:
            comment = f"Self-approved. {comment}" if comment else "Self-approved."
        log_event(ticket, user, "APPROVED", comment, old_status=old, new_status=ticket.status)
    else:
        log_event(
            ticket,
            user,
            "STATUS_CHANGE",
            comment or "Stage advanced",
            old_status=old,
            new_status=ticket.status,
        )


def send_back(ticket, user, reason):
    if not ticket.can_send_back:
        raise WorkflowError("This case can't be sent back from its current stage.")
    old = ticket.status
    ticket.status = PROCUREMENT_FLOW[ticket.stage_index - 1]
    # the approver sending it back is a rejection; purchase pulling it back is not
    event = (
        "REJECTED"
        if old == APPROVAL_STAGE and user.role in (ROLE_APPROVER, ROLE_TEAM_LEAD)
        else "SENT_BACK"
    )
    log_event(ticket, user, event, reason, old_status=old, new_status=ticket.status)


def hold(ticket, user, reason):
    if not ticket.can_hold:
        raise WorkflowError("Only active, open cases can be put on hold.")
    ticket.state = STATE_ON_HOLD
    log_event(ticket, user, "ON_HOLD", reason)


def resume(ticket, user, comment):
    if not ticket.can_resume:
        raise WorkflowError("Only cases on hold can be resumed.")
    ticket.state = STATE_ACTIVE
    log_event(ticket, user, "RESUMED", comment)


def cancel(ticket, user, reason):
    if not ticket.can_cancel:
        raise WorkflowError("This case can't be cancelled.")
    ticket.state = STATE_CANCELLED
    log_event(ticket, user, "CANCELLED", reason)


def reopen(ticket, user, reason):
    if not ticket.can_reopen:
        raise WorkflowError("Only cancelled cases can be reopened.")
    ticket.state = STATE_ACTIVE
    log_event(ticket, user, "REOPENED", reason)


# ---- edits ----


def check_item_changes(ticket, rows):
    """
    Item edits that would conflict with purchase orders, as form errors
    ({"items": message}). Rows are what validate_request_form returned.
    """
    kept = {row["id"]: row for row in rows if row["id"] is not None}
    problems = []
    for item in ticket.items:
        row = kept.get(item.id)
        if row is None:
            if item.po_lines:
                problems.append(f"{item.label} is on a PO, so it can't be removed.")
            continue
        on_pos = item.quantity_on_pos
        if on_pos and row["quantity"] is not None and row["quantity"] < on_pos:
            problems.append(f"{item.label}: {on_pos} {item.unit} are already on POs.")
        if on_pos and row["unit"] != item.unit:
            problems.append(f"{item.label}: the unit can't change once it is on a PO.")
    unknown = set(kept) - {item.id for item in ticket.items}
    if unknown:
        problems.append("The item list changed while you were editing. Reload and try again.")
    return {"items": " ".join(problems)} if problems else {}


def _apply_items(ticket, rows):
    """Replace the ticket's items with rows (same order); return what changed."""
    existing = {item.id: item for item in ticket.items}
    kept_ids = {row["id"] for row in rows}
    changes = [
        f"Item removed: {item.description}" for item in ticket.items if item.id not in kept_ids
    ]
    ticket.items = []  # rebuilt below in the new order; removed items are deleted
    for position, row in enumerate(rows, start=1):
        item = existing.get(row["id"])
        if item is None:
            item = TicketItem(description=row["description"])
            changes.append(f"Item added: {row['description']}, {row['quantity']} {row['unit']}")
        else:
            diffs = [
                f"{label} {old} → {new}"
                for label, old, new in (
                    ("description", item.description, row["description"]),
                    ("quantity", item.quantity, row["quantity"]),
                    ("unit", item.unit, row["unit"]),
                )
                if old != new
            ]
            if diffs:
                changes.append(f"Item {item.description}: {', '.join(diffs)}")
        item.position = position
        item.description = row["description"]
        item.quantity = row["quantity"]
        item.unit = row["unit"]
        ticket.items.append(item)
    return changes


def _add_new_vendor(data, vendor_data, user):
    """
    Save a vendor typed on the request form ("+ Add a new vendor") and choose
    it on the ticket. Returns a line for the timeline, or None.
    """
    if not vendor_data:
        return None
    vendor = Vendor(**{**vendor_data, "notes": vendor_data.get("notes") or f"Added by {user.name}"})
    db.session.add(vendor)
    db.session.flush()  # assigns vendor.id
    data["recommended_vendor_id"] = vendor.id
    return f"New vendor added to the Vendors list: {vendor.name}"


def edit_ticket(ticket, user, data, items=None):
    """General fields, plus the vendor and items when `items` is given."""
    vendor_note = _add_new_vendor(data, data.pop("new_vendor", None), user)
    changes = ([vendor_note] if vendor_note else []) + apply_changes(ticket, data)
    if items is not None:
        changes += _apply_items(ticket, items)
    if changes:
        log_event(ticket, user, "EDITED", "\n".join(changes))
    return changes


def update_prices(ticket, user, prices):
    """Estimated price and GST per item (purchase team). prices: {item: (price, gst)}."""
    changes = []
    for item, (price, gst) in prices.items():
        if (item.est_unit_price, item.est_gst_rate) == (price, gst):
            continue
        before = _price_text(item.est_unit_price, item.est_gst_rate)
        item.est_unit_price, item.est_gst_rate = price, gst
        changes.append(f"{item.description}: {before} → {_price_text(price, gst)}")
    if changes:
        log_event(ticket, user, "REQUISITION", "\n".join(changes))
    return changes


def _price_text(price, gst):
    if price is None:
        return "—"
    return f"{format_amount(price)} + {gst.normalize():f}% GST"


def update_tracking(ticket, user, data, comment):
    changes = apply_changes(ticket, data)
    if changes or comment:
        lines = ([comment] if comment else []) + changes
        log_event(ticket, user, "TRACKING_UPDATE", "\n".join(lines))
    return changes


def set_archived(ticket, user, archived):
    ticket.archived = archived
    log_event(ticket, user, "ARCHIVED" if archived else "RESTORED")


def delete_ticket(ticket):
    # history is removed with it (cascade on the relationship)
    db.session.delete(ticket)
