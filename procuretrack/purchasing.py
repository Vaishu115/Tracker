"""
Purchase orders: draft, add lines from approved tickets, issue, cancel.

Same pattern as services.py: views check permissions, then call these.
Functions add to the session and the view commits, except issue(), which
commits itself so it can retry if two POs get the same number.

Rules enforced here:
  - only Draft POs can be changed; an issued PO is locked (cancel it instead)
  - a line is for one ticket item whose ticket is approved and open, with
    quantity left; one line per item per PO; line quantities never exceed
    the item's
  - issuing needs the company details (for GST) and at least one line;
    it numbers the PO, freezes vendor/company details and logs on each ticket
"""

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .constants import (
    BASE_CURRENCY,
    FINAL_STAGE,
    PO_CANCELLED,
    PO_ISSUED,
    STATE_ACTIVE,
    TRACKING_STAGES,
)
from .extensions import db
from .models import POLine, ProcurementRequest, PurchaseOrder, TicketItem
from .money import format_amount
from .services import log_event


class PurchasingError(Exception):
    """An action that isn't allowed on the PO in its current state."""


def vendor_snapshot(vendor):
    return {
        "name": vendor.name,
        "contact_person": vendor.contact_person,
        "email": vendor.email,
        "phone": vendor.phone,
        "address": vendor.address,
        "city": vendor.city,
        "state_code": vendor.state_code,
        "state_name": vendor.state_name,
        "country": vendor.country,
        "gstin": vendor.gstin,
        "pan": vendor.pan,
    }


def company_snapshot(company):
    from .constants import INDIAN_STATES

    return {
        "name": company.name,
        "address": company.address,
        "state_code": company.state_code,
        "state_name": INDIAN_STATES.get(company.state_code or "", ""),
        "gstin": company.gstin,
        "pan": company.pan,
        "email": company.email,
        "phone": company.phone,
        "plant": company.plant,
    }


def financial_year(day):
    """Indian financial year (April-March): 24 Sep 2026 -> '2026-27'."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def next_po_number(prefix, day):
    base = f"{prefix}/{financial_year(day)}/"
    existing = db.session.scalars(
        select(PurchaseOrder.po_number).where(PurchaseOrder.po_number.like(base + "%"))
    )
    numbers = [int(n.rsplit("/", 1)[1]) for n in existing if n.rsplit("/", 1)[1].isdigit()]
    return f"{base}{max(numbers, default=0) + 1:04d}"


def orderable_tickets():
    """Tickets with items that can go on a PO: approved, open, with quantity left."""
    candidates = db.session.scalars(
        select(ProcurementRequest)
        .where(
            ProcurementRequest.archived.is_(False),
            ProcurementRequest.state == STATE_ACTIVE,
            ProcurementRequest.status.in_([s for s in TRACKING_STAGES if s != FINAL_STAGE]),
        )
        .order_by(ProcurementRequest.id)
    ).all()
    return [t for t in candidates if t.can_be_ordered]


def orderable_items():
    return [item for ticket in orderable_tickets() for item in ticket.orderable_items]


def last_purchase(item):
    """
    The most recent issued PO line for an item with the same description
    (ignoring case), on another ticket. For the requisition form's
    "Last Purchase Date" and "Last PO Qty/Rate". None if never bought before.
    """
    return db.session.scalars(
        select(POLine)
        .join(POLine.po)
        .join(POLine.item)
        .where(
            PurchaseOrder.status == PO_ISSUED,
            func.lower(func.trim(TicketItem.description)) == item.description.strip().lower(),
            TicketItem.ticket_id != item.ticket_id,
        )
        .order_by(PurchaseOrder.po_date.desc(), PurchaseOrder.id.desc())
    ).first()


def _require_draft(po):
    if not po.is_draft:
        raise PurchasingError("Only draft POs can be changed. Cancel the PO and create a new one.")


# ---- drafting ----


def create_draft(vendor, user, company):
    if not vendor.active:
        raise PurchasingError(f"{vendor.name} is inactive.")
    po = PurchaseOrder(
        vendor=vendor,
        tax_type=vendor.tax_type_for(company),
        currency=vendor.default_currency,
        payment_terms=vendor.payment_terms,
        terms=company.default_terms,
        delivery_address=company.delivery_address or company.address,
        created_by_id=user.id,
    )
    db.session.add(po)
    db.session.flush()  # assigns po.id for the "Draft #id" label
    return po


def update_header(po, data, company):
    _require_draft(po)
    vendor = data.pop("vendor")
    if not vendor.active and vendor.id != po.vendor_id:
        raise PurchasingError(f"{vendor.name} is inactive.")
    po.vendor = vendor
    po.tax_type = vendor.tax_type_for(company)
    for field, value in data.items():
        setattr(po, field, value)
    if po.currency == BASE_CURRENCY:
        po.exchange_rate = 1


def _name(item):
    return f"{item.ticket.ticket_id} item {item.label}"


def _check_quantity(item, quantity, already_on_this_line=0):
    available = item.remaining_quantity + already_on_this_line
    if quantity > available:
        raise PurchasingError(f"{_name(item)} has only {available} {item.unit} left to order.")


def add_line(po, item, data):
    _require_draft(po)
    if not item.can_be_ordered:
        raise PurchasingError(
            f"{_name(item)} can't be ordered: its ticket must be approved and open, "
            "with quantity left."
        )
    if any(line.item is item for line in po.lines):
        raise PurchasingError(f"{_name(item)} is already on this PO. Change that line instead.")
    _check_quantity(item, data["quantity"])

    line = POLine(
        item=item,
        description=data["description"] or item.description,
        hsn_sac=data["hsn_sac"],
        quantity=data["quantity"],
        unit=item.unit,
        unit_price=data["unit_price"],
        gst_rate=data["gst_rate"],
    )
    po.lines.append(line)
    return line


def add_ticket(po, ticket):
    """
    Add every item of a ticket still to order, at its remaining quantity and
    approved estimated price and GST. Only for INR POs, since the estimates
    are in rupees. Returns the new lines.
    """
    _require_draft(po)
    if po.currency != BASE_CURRENCY:
        raise PurchasingError(
            "Estimated prices are in INR. On a foreign-currency PO, add the items one by one."
        )
    on_po = [line.item for line in po.lines]
    items = [item for item in ticket.orderable_items if item not in on_po]
    if not items:
        raise PurchasingError(f"{ticket.ticket_id} has no items left to add to this PO.")
    unpriced = [item.label for item in items if not item.is_priced]
    if unpriced:
        raise PurchasingError(
            f"No estimated price for {', '.join(unpriced)}. Add those items one by one."
        )
    return [
        add_line(
            po,
            item,
            {
                "description": None,
                "hsn_sac": None,
                "quantity": item.remaining_quantity,
                "unit_price": item.est_unit_price,
                "gst_rate": item.est_gst_rate,
            },
        )
        for item in items
    ]


def update_line(line, data):
    _require_draft(line.po)
    _check_quantity(line.item, data["quantity"], already_on_this_line=line.quantity)
    line.description = data["description"] or line.item.description
    line.hsn_sac = data["hsn_sac"]
    line.quantity = data["quantity"]
    line.unit_price = data["unit_price"]
    line.gst_rate = data["gst_rate"]


def remove_line(line):
    _require_draft(line.po)
    line.po.lines.remove(line)


def delete_draft(po):
    _require_draft(po)
    db.session.delete(po)


# ---- issue / cancel ----


def issue(po, user, company):
    """Number and lock the PO, and log it on each ticket. Commits."""
    _require_draft(po)
    if not company.is_complete:
        raise PurchasingError(
            "Fill in the company details (name, address, state) in Settings before issuing POs."
        )
    if not po.lines:
        raise PurchasingError("Add at least one line before issuing.")
    if not po.vendor.active:
        raise PurchasingError(f"{po.vendor.name} is inactive.")
    closed = [
        line.ticket.ticket_id
        for line in po.lines
        if line.ticket.state != STATE_ACTIVE or line.ticket.archived
    ]
    if closed:
        raise PurchasingError(
            f"Remove these tickets first; they are no longer active: {', '.join(closed)}."
        )

    po.po_date = date.today()
    po.po_number = next_po_number(company.po_prefix, po.po_date)
    po.tax_type = po.vendor.tax_type_for(company)
    po.vendor_snapshot = vendor_snapshot(po.vendor)
    po.company_snapshot = company_snapshot(company)
    po.status = PO_ISSUED
    po.issued_by_id = user.id
    po.issued_at = datetime.now()

    for ticket, lines in _lines_by_ticket(po):
        details = [
            f"{line.item.description}: {line.quantity} {line.unit} at "
            f"{format_amount(line.unit_price, po.currency)} + GST {line.gst_rate.normalize():f}%"
            for line in lines
        ]
        log_event(
            ticket,
            user,
            "PO_ISSUED",
            "\n".join([f"PO {po.po_number} to {po.vendor.name}"] + details),
        )

    try:
        db.session.commit()
    except IntegrityError as exc:
        # someone issued a PO with the same number at the same moment
        db.session.rollback()
        raise PurchasingError(
            "Another PO was issued at the same moment. Please try again."
        ) from exc
    return po


def cancel(po, user, reason):
    if po.status != PO_ISSUED:
        raise PurchasingError("Only issued POs can be cancelled. Delete a draft instead.")
    po.status = PO_CANCELLED
    po.cancelled_by_id = user.id
    po.cancelled_at = datetime.now()
    po.cancel_reason = reason
    for ticket, _lines in _lines_by_ticket(po):
        log_event(ticket, user, "PO_CANCELLED", f"PO {po.po_number}: {reason}")


def _lines_by_ticket(po):
    """[(ticket, [its lines on this PO]), ...] in line order."""
    grouped = {}
    for line in po.lines:
        grouped.setdefault(line.ticket, []).append(line)
    return list(grouped.items())
