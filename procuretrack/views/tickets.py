"""
Ticket pages: queue, tracking, archive, the request form, the ticket detail
page and every action on a ticket (advance, approve, hold, edit, ...).

Pattern for each action: load the ticket (404 if missing) -> require()
the permission (403) -> validate the form -> call a function in services.py
-> commit -> flash a message and redirect back to the ticket.
"""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from .. import purchasing, services
from ..constants import (
    APPROVAL_STAGE,
    EVENT_LABELS,
    FINAL_STAGE,
    FULFILMENT_STATUSES,
    GST_RATES,
    INDIAN_STATES,
    PAYMENT_STATUSES,
    PO_DRAFT,
    PRIORITIES,
    PROCUREMENT_FLOW,
    PROCUREMENT_TYPES,
    STATE_CANCELLED,
    TRACKING_STAGES,
    UNITS,
)
from ..extensions import db
from ..forms import (
    categories_for,
    validate_comment,
    validate_pricing_form,
    validate_request_form,
    validate_tracking_form,
)
from ..models import (
    ROLE_APPROVER,
    ROLE_TEAM_LEAD,
    CompanySettings,
    ProcurementRequest,
    Project,
    PurchaseOrder,
    User,
    Vendor,
)
from ..permissions import approval_role, can, require

bp = Blueprint("tickets", __name__)


def approver_names(ticket):
    """Approvers eligible for this ticket's estimated total."""
    return db.session.scalars(
        select(User.name)
        .where(User.role == approval_role(ticket), User.active.is_(True))
        .order_by(User.name)
    ).all()


def requisition_vendors(ticket=None):
    """Active vendors, plus the ticket's current one even if it was deactivated."""
    current = ticket.recommended_vendor_id if ticket else None
    return db.session.scalars(
        select(Vendor)
        .where(or_(Vendor.active.is_(True), Vendor.id == current))
        .order_by(Vendor.name)
    ).all()


def latest_event(ticket, event_type, new_status=None):
    for event in ticket.history:  # newest first
        if event.event_type == event_type and (
            new_status is None or event.new_status == new_status
        ):
            return event
    return None


def draft_pos():
    """Offered on an approved ticket: 'add to an existing draft PO'."""
    return db.session.scalars(
        select(PurchaseOrder).where(PurchaseOrder.status == PO_DRAFT).order_by(PurchaseOrder.id)
    ).all()


def get_ticket_or_404(ticket_id):
    ticket = db.session.scalar(
        select(ProcurementRequest).where(ProcurementRequest.ticket_id == ticket_id)
    )
    if ticket is None:
        abort(404, description=f"Procurement case {ticket_id} does not exist.")
    return ticket


def item_rows_from_form(form):
    """The item rows as typed, to show the form again after an error."""
    columns = [form.getlist(f"item_{f}") for f in ("id", "description", "quantity", "unit")]
    count = max(len(c) for c in columns)
    rows = []
    for i in range(count):
        values = [c[i] if i < len(c) else "" for c in columns]
        if any(v.strip() for v in values[1:3]):
            rows.append(dict(zip(("id", "description", "quantity", "unit"), values, strict=True)))
    return rows


def render_form(mode, values, errors, item_rows, ticket=None):
    # active projects, plus the ticket's current one even if it has been deactivated
    projects = db.session.scalars(
        select(Project)
        .where(or_(Project.active.is_(True), Project.id == (ticket.project_id if ticket else None)))
        .order_by(Project.name)
    ).all()
    return render_template(
        "ticket_form.html",
        mode=mode,
        ticket=ticket,
        values=values,
        errors=errors,
        item_rows=item_rows or [{"id": "", "description": "", "quantity": "1", "unit": "Nos"}],
        # vendor and items are locked after approval
        edit_items=ticket is None or can("edit_items", ticket),
        vendors=requisition_vendors(ticket),
        states=INDIAN_STATES,
        projects=projects,
        units=UNITS,
        categories=categories_for(ticket),
        procurement_types=PROCUREMENT_TYPES,
        priorities=PRIORITIES,
    ), 400 if errors else 200


def render_detail(ticket, tracking_values=None, tracking_errors=None):
    return render_template(
        "detail.html",
        ticket=ticket,
        flow=PROCUREMENT_FLOW,
        payment_statuses=PAYMENT_STATUSES,
        fulfilment_statuses=FULFILMENT_STATUSES,
        event_labels=EVENT_LABELS,
        approval_stage=APPROVAL_STAGE,
        approvers=approver_names(ticket) if ticket.status == APPROVAL_STAGE else [],
        draft_pos=draft_pos() if ticket.can_be_ordered else [],
        gst_rates=GST_RATES,
        tracking_values=tracking_values,
        tracking_errors=tracking_errors or {},
    ), 400 if tracking_errors else 200


def to_detail(ticket):
    return redirect(url_for("tickets.detail", ticket_id=ticket.ticket_id))


# ---- lists ----


@bp.route("/queue")
def queue():
    tickets = db.session.scalars(
        select(ProcurementRequest)
        .where(ProcurementRequest.archived.is_(False))
        .order_by(ProcurementRequest.id.desc())
    ).all()
    return render_template("queue.html", tickets=tickets)


@bp.route("/tracking")
def tracking():
    tickets = db.session.scalars(
        select(ProcurementRequest)
        .where(
            ProcurementRequest.archived.is_(False),
            ProcurementRequest.state != STATE_CANCELLED,
            ProcurementRequest.status.in_([s for s in TRACKING_STAGES if s != FINAL_STAGE]),
        )
        # soonest delivery first, cases without a date last
        .order_by(
            ProcurementRequest.expected_delivery.is_(None), ProcurementRequest.expected_delivery
        )
    ).all()
    return render_template("tracking.html", tickets=tickets)


@bp.route("/archive")
def archive():
    tickets = db.session.scalars(
        select(ProcurementRequest)
        .where(ProcurementRequest.archived.is_(True))
        .order_by(ProcurementRequest.id.desc())
    ).all()
    return render_template("archive.html", tickets=tickets)


# ---- create / view / edit ----


@bp.route("/request", methods=["GET", "POST"])
def new_request():
    if request.method == "POST":
        data, items, errors = validate_request_form(request.form)
        if errors:
            return render_form("create", request.form, errors, item_rows_from_form(request.form))
        ticket = services.create_ticket(data, items, g.user)
        flash(f"{ticket.ticket_id} created.", "success")
        return to_detail(ticket)

    return render_form("create", {"priority": "Normal", "owner": g.user.name}, {}, None)


@bp.route("/ticket/<ticket_id>")
def detail(ticket_id):
    return render_detail(get_ticket_or_404(ticket_id))


@bp.route("/ticket/<ticket_id>/edit", methods=["GET", "POST"])
def edit(ticket_id):
    ticket = get_ticket_or_404(ticket_id)
    require("edit", ticket)

    if request.method == "POST":
        with_items = can("edit_items", ticket)
        data, items, errors = validate_request_form(
            request.form, with_requisition=with_items, ticket=ticket
        )
        if with_items and not errors:
            errors = services.check_item_changes(ticket, items)
        if errors:
            return render_form(
                "edit", request.form, errors, item_rows_from_form(request.form), ticket
            )
        changes = services.edit_ticket(ticket, g.user, data, items)
        db.session.commit()
        flash(
            "Changes saved." if changes else "No changes to save.", "success" if changes else "info"
        )
        return to_detail(ticket)

    values = {
        "title": ticket.title or "",
        "project_id": str(ticket.project_id or ""),
        "owner": ticket.owner or "",
        "category": ticket.category,
        "procurement_type": ticket.procurement_type,
        "priority": ticket.priority,
        "required_by": ticket.required_by.isoformat() if ticket.required_by else "",
        "description": ticket.description or "",
        "recommended_vendor_id": str(ticket.recommended_vendor_id or ""),
    }
    rows = [
        {"id": str(i.id), "description": i.description, "quantity": str(i.quantity), "unit": i.unit}
        for i in ticket.items
    ]
    return render_form("edit", values, {}, rows, ticket)


@bp.route("/ticket/<ticket_id>/prices", methods=["POST"])
def update_prices(ticket_id):
    ticket = get_ticket_or_404(ticket_id)
    require("pricing", ticket)
    prices, errors = validate_pricing_form(request.form, ticket)
    if errors:
        flash(" ".join(errors.values()), "error")
        return to_detail(ticket)
    changes = services.update_prices(ticket, g.user, prices)
    db.session.commit()
    flash("Prices saved." if changes else "No changes to save.", "success" if changes else "info")
    return to_detail(ticket)


@bp.route("/ticket/<ticket_id>/requisition/print")
def print_requisition(ticket_id):
    """The Purchase Requisition Form, in ERIC's paper format."""
    ticket = get_ticket_or_404(ticket_id)
    sent = latest_event(ticket, "STATUS_CHANGE", new_status=APPROVAL_STAGE)
    approved = latest_event(ticket, "APPROVED")
    # the requisitioner is the engineer who raised it; tickets from before
    # V0.5 have no creator, so fall back to whoever sent it for approval
    requisitioner = ticket.created_by
    if requisitioner is None and sent and sent.user_id:
        requisitioner = db.session.get(User, sent.user_id)
    return render_template(
        "requisition_print.html",
        ticket=ticket,
        company=CompanySettings.get(),
        # [(item, its most recent earlier PO line)] for items bought before
        last_purchases=[
            (item, line)
            for item in ticket.items
            if (line := purchasing.last_purchase(item)) is not None
        ],
        sent=sent,
        approved=approved,
        requisitioner=requisitioner,
    )


@bp.route("/ticket/<ticket_id>/tracking", methods=["POST"])
def update_tracking(ticket_id):
    ticket = get_ticket_or_404(ticket_id)
    require("tracking", ticket)
    if not ticket.in_tracking:
        flash("Tracking details can be recorded once the PO is released.", "error")
        return to_detail(ticket)

    # vendor / PO number text boxes only for older tickets without real POs
    data, comment, errors = validate_tracking_form(
        request.form, include_vendor_fields=not ticket.po_lines
    )
    if errors:
        return render_detail(ticket, tracking_values=request.form, tracking_errors=errors)

    changes = services.update_tracking(ticket, g.user, data, comment)
    db.session.commit()
    if changes or comment:
        flash("Tracking details saved.", "success")
    else:
        flash("No changes to save.", "info")
    return to_detail(ticket)


# ---- workflow actions ----

# action in URL -> (service function, reason required?, success message)
WORKFLOW_ACTIONS = {
    "advance": (services.advance, False, "Moved to {status}."),
    "send-back": (services.send_back, True, "Sent back to {status}."),
    "hold": (services.hold, True, "Case put on hold."),
    "resume": (services.resume, False, "Case resumed."),
    "cancel": (services.cancel, True, "Case cancelled."),
    "reopen": (services.reopen, True, "Case reopened."),
}


@bp.route(
    "/ticket/<ticket_id>/<any(advance,'send-back',hold,resume,cancel,reopen):action>",
    methods=["POST"],
)
def workflow_action(ticket_id, action):
    ticket = get_ticket_or_404(ticket_id)
    require(action, ticket)
    service, reason_required, message = WORKFLOW_ACTIONS[action]
    was_at_approval = ticket.status == APPROVAL_STAGE

    comment, errors = validate_comment(request.form, required=reason_required)
    if errors:
        flash(errors["comment"], "error")
        return to_detail(ticket)

    try:
        service(ticket, g.user, comment)
    except services.WorkflowError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return to_detail(ticket)

    db.session.commit()
    if was_at_approval and action == "advance":
        message = "Approved. Moved to {status}."
    elif (
        was_at_approval
        and action == "send-back"
        and g.user.role in (ROLE_APPROVER, ROLE_TEAM_LEAD)
    ):
        message = "Rejected. Sent back to {status}."
    flash(message.format(status=ticket.status), "success")
    return to_detail(ticket)


# ---- case management ----


@bp.route("/ticket/<ticket_id>/archive", methods=["POST"])
def archive_ticket(ticket_id):
    ticket = get_ticket_or_404(ticket_id)
    require("archive", ticket)
    services.set_archived(ticket, g.user, True)
    db.session.commit()
    flash(f"{ticket.ticket_id} archived.", "success")
    return redirect(url_for("tickets.queue"))


@bp.route("/ticket/<ticket_id>/restore", methods=["POST"])
def restore_ticket(ticket_id):
    ticket = get_ticket_or_404(ticket_id)
    require("restore", ticket)
    services.set_archived(ticket, g.user, False)
    db.session.commit()
    flash(f"{ticket.ticket_id} restored.", "success")
    return to_detail(ticket)


@bp.route("/ticket/<ticket_id>/delete", methods=["POST"])
def delete_ticket(ticket_id):
    ticket = get_ticket_or_404(ticket_id)
    require("delete", ticket)
    services.delete_ticket(ticket)
    db.session.commit()
    flash(f"{ticket_id} permanently deleted.", "success")
    return redirect(url_for("tickets.queue"))
