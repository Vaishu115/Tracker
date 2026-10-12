"""
Purchase orders: list, create a draft, edit header and lines, issue, cancel,
delete a draft, and the printable PO document.

Same pattern as views/tickets.py: load (404) -> require() -> validate ->
call purchasing.py -> commit -> flash + redirect.
"""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import purchasing
from ..constants import (
    BASE_CURRENCY,
    CURRENCIES,
    GST_RATES,
    INDIAN_STATES,
    PO_CANCELLED,
    PO_DRAFT,
    PO_ISSUED,
    TAX_LABELS,
)
from ..extensions import db
from ..forms import validate_comment, validate_po_header_form, validate_po_line_form
from ..models import (
    CompanySettings,
    POLine,
    ProcurementRequest,
    PurchaseOrder,
    TicketItem,
    Vendor,
)
from ..permissions import require

bp = Blueprint("pos", __name__, url_prefix="/pos")

STATUSES = [PO_DRAFT, PO_ISSUED, PO_CANCELLED]


def to_po(po):
    return redirect(url_for("pos.detail", po_id=po.id))


def flash_errors(errors):
    flash(" ".join(errors.values()), "error")


def active_vendors():
    return db.session.scalars(
        select(Vendor).where(Vendor.active.is_(True)).order_by(Vendor.name)
    ).all()


@bp.route("/")
def index():
    status = request.args.get("status")
    query = select(PurchaseOrder).order_by(PurchaseOrder.id.desc())
    if status in STATUSES:
        query = query.where(PurchaseOrder.status == status)
    return render_template(
        "pos.html", pos=db.session.scalars(query).all(), status=status, statuses=STATUSES
    )


@bp.route("/new", methods=["GET", "POST"])
def new():
    """Step 1: pick the vendor. The draft is created and opened for adding lines."""
    require("manage_pos")
    ticket_id = request.values.get("ticket", "")  # when started from a ticket page

    if request.method == "POST":
        vendor_id = request.form.get("vendor_id", "")
        vendor = db.session.get(Vendor, int(vendor_id)) if vendor_id.isdigit() else None
        if vendor is None:
            flash("Choose a vendor.", "error")
            return redirect(url_for("pos.new", ticket=ticket_id))
        try:
            po = purchasing.create_draft(vendor, g.user, CompanySettings.get())
        except purchasing.PurchasingError as exc:
            flash(str(exc), "error")
            return redirect(url_for("pos.new", ticket=ticket_id))
        db.session.commit()
        flash(f"Draft PO created for {vendor.name}. Add lines, then issue it.", "success")
        return redirect(url_for("pos.detail", po_id=po.id, add=ticket_id or None))

    # started from a ticket: suggest the vendor recommended on its requisition
    vendor_id = request.args.get("vendor", "")
    if not vendor_id and ticket_id.isdigit():
        ticket = db.session.get(ProcurementRequest, int(ticket_id))
        if ticket is not None and ticket.recommended_vendor_id:
            vendor_id = str(ticket.recommended_vendor_id)

    return render_template(
        "po_new.html",
        vendors=active_vendors(),
        ticket_id=ticket_id,
        vendor_id=vendor_id,
        orderable=purchasing.orderable_items(),
    )


@bp.route("/<int:po_id>")
def detail(po_id):
    po = db.get_or_404(PurchaseOrder, po_id)
    company = CompanySettings.get()
    orderable = []
    if po.is_draft:
        on_po = [line.item for line in po.lines]
        orderable = [item for item in purchasing.orderable_items() if item not in on_po]
    return render_template(
        "po_detail.html",
        po=po,
        company=company,
        totals=po.totals,
        tax_labels=TAX_LABELS,
        vendors=active_vendors() if po.is_draft else [],
        orderable=orderable,
        # tickets with items still to add, for "Add all items of a ticket"
        orderable_tickets=list(dict.fromkeys(item.ticket for item in orderable)),
        preselect=request.args.get("add", ""),
        currencies=CURRENCIES,
        base_currency=BASE_CURRENCY,
        gst_rates=GST_RATES,
    )


@bp.route("/<int:po_id>/header", methods=["POST"])
def update_header(po_id):
    require("manage_pos")
    po = db.get_or_404(PurchaseOrder, po_id)
    data, errors = validate_po_header_form(request.form)
    if errors:
        flash_errors(errors)
        return to_po(po)
    try:
        purchasing.update_header(po, data, CompanySettings.get())
    except purchasing.PurchasingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return to_po(po)
    db.session.commit()
    flash("PO details saved.", "success")
    return to_po(po)


@bp.route("/<int:po_id>/lines", methods=["POST"])
def add_line(po_id):
    require("manage_pos")
    po = db.get_or_404(PurchaseOrder, po_id)
    item_id = request.form.get("item_id", "")
    item = db.session.get(TicketItem, int(item_id)) if item_id.isdigit() else None
    data, errors = validate_po_line_form(request.form)
    if item is None:
        errors["item_id"] = "Choose an item."
    if errors:
        flash_errors(errors)
        return to_po(po)
    try:
        purchasing.add_line(po, item, data)
    except purchasing.PurchasingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return to_po(po)
    db.session.commit()
    flash(f"{item.description} ({item.ticket.ticket_id}) added.", "success")
    return to_po(po)


@bp.route("/<int:po_id>/add-ticket", methods=["POST"])
def add_ticket(po_id):
    """All items of a ticket still to order, at their approved estimated prices."""
    require("manage_pos")
    po = db.get_or_404(PurchaseOrder, po_id)
    ticket_id = request.form.get("ticket_id", "")
    ticket = db.session.get(ProcurementRequest, int(ticket_id)) if ticket_id.isdigit() else None
    if ticket is None:
        flash("Choose a ticket.", "error")
        return to_po(po)
    try:
        lines = purchasing.add_ticket(po, ticket)
    except purchasing.PurchasingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return to_po(po)
    db.session.commit()
    flash(
        f"{len(lines)} item(s) from {ticket.ticket_id} added. Check quantities, prices and "
        "HSN codes before issuing.",
        "success",
    )
    return to_po(po)


def get_line_or_404(po_id, line_id):
    line = db.get_or_404(POLine, line_id)
    if line.po_id != po_id:
        abort(404)
    return line


@bp.route("/<int:po_id>/lines/<int:line_id>", methods=["POST"])
def update_line(po_id, line_id):
    require("manage_pos")
    line = get_line_or_404(po_id, line_id)
    data, errors = validate_po_line_form(request.form)
    if errors:
        flash_errors(errors)
        return to_po(line.po)
    try:
        purchasing.update_line(line, data)
    except purchasing.PurchasingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return to_po(line.po)
    db.session.commit()
    flash("Line saved.", "success")
    return to_po(line.po)


@bp.route("/<int:po_id>/lines/<int:line_id>/remove", methods=["POST"])
def remove_line(po_id, line_id):
    require("manage_pos")
    line = get_line_or_404(po_id, line_id)
    po = line.po
    try:
        purchasing.remove_line(line)
    except purchasing.PurchasingError as exc:
        flash(str(exc), "error")
        return to_po(po)
    db.session.commit()
    flash("Line removed.", "success")
    return to_po(po)


@bp.route("/<int:po_id>/issue", methods=["POST"])
def issue(po_id):
    require("manage_pos")
    po = db.get_or_404(PurchaseOrder, po_id)
    try:
        purchasing.issue(po, g.user, CompanySettings.get())
    except purchasing.PurchasingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return to_po(po)
    flash(f"PO {po.po_number} issued. Print it or save it as PDF to send to the vendor.", "success")
    return to_po(po)


@bp.route("/<int:po_id>/cancel", methods=["POST"])
def cancel(po_id):
    require("manage_pos")
    po = db.get_or_404(PurchaseOrder, po_id)
    reason, errors = validate_comment(request.form, required=True)
    if errors:
        flash_errors(errors)
        return to_po(po)
    try:
        purchasing.cancel(po, g.user, reason)
    except purchasing.PurchasingError as exc:
        flash(str(exc), "error")
        return to_po(po)
    db.session.commit()
    flash(f"PO {po.po_number} cancelled.", "success")
    return to_po(po)


@bp.route("/<int:po_id>/delete", methods=["POST"])
def delete(po_id):
    require("manage_pos")
    po = db.get_or_404(PurchaseOrder, po_id)
    try:
        purchasing.delete_draft(po)
    except purchasing.PurchasingError as exc:
        flash(str(exc), "error")
        return to_po(po)
    db.session.commit()
    flash("Draft deleted.", "success")
    return redirect(url_for("pos.index"))


@bp.route("/<int:po_id>/print")
def print_view(po_id):
    po = db.get_or_404(PurchaseOrder, po_id)
    # the Director box shows who approved each ticket on the PO
    approvals = []
    for ticket in dict.fromkeys(line.ticket for line in po.lines):
        approval = next((e for e in ticket.history if e.event_type == "APPROVED"), None)
        if approval is not None and approval not in approvals:
            approvals.append(approval)
    return render_template(
        "po_print.html",
        po=po,
        approvals=approvals,
        vendor=po.vendor_details(),
        company=po.company_details(),
        totals=po.totals,
        states=INDIAN_STATES,
    )
