"""
Database tables.

  User                 people who can sign in, with their role
  ProcurementRequest   a ticket ("procurement case") = one purchase requisition:
                       one recommended vendor and one or more items. Its
                       `status` is the workflow stage and `state` is
                       Active / On Hold / Cancelled
  TicketItem           one item row on a ticket: quantity, unit and the
                       estimated price / GST filled in by the purchase team
  StatusHistory        the activity timeline: one row per event on a ticket
  Project              managed list of projects a ticket can belong to
  Vendor               supplier directory (GSTIN, state/country, terms)
  CompanySettings      one row: ERIC's details printed on POs
  PurchaseOrder        a PO to one vendor: Draft -> Issued (or Cancelled)
  POLine               one line on a PO; each line is for one ticket item, so a
                       PO can combine tickets and an item can be split across POs

Changing a model means writing a migration too (see docs/DEVELOPER_GUIDE.md).
The `can_*` properties say whether the workflow allows an action in the
ticket's current state; whether the *user* may do it is in permissions.py.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.types import Integer, TypeDecorator

from .constants import (
    BASE_CURRENCY,
    FINAL_STAGE,
    INDIA,
    MAX_ITEM,
    MAX_PO,
    MAX_SHORT,
    MAX_VENDOR,
    PO_CANCELLED,
    PO_DRAFT,
    PO_ISSUED,
    PROCUREMENT_FLOW,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_ON_HOLD,
    TAX_IMPORT,
    TAX_INTER,
    TAX_INTRA,
    TRACKING_STAGES,
)
from .extensions import db
from .money import Totals, line_amounts


class ExactDecimal(TypeDecorator):
    """
    A Decimal stored as a whole number (e.g. ₹12.50 with places=2 -> 1250).
    Exact on every database; SQLite would otherwise store Numeric as float.
    """

    impl = Integer
    cache_ok = True

    def __init__(self, places=2):
        super().__init__()
        self.places = places
        self.factor = Decimal(10) ** places

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int((Decimal(value) * self.factor).to_integral_value())

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / self.factor).quantize(Decimal(1).scaleb(-self.places))


ROLE_REQUESTER = "requester"
ROLE_PURCHASE = "purchase"
ROLE_APPROVER = "approver"
ROLE_TEAM_LEAD = "team_lead"

ROLE_LABELS = {
    ROLE_REQUESTER: "Requester",
    ROLE_PURCHASE: "Purchase team",
    ROLE_APPROVER: "Approver",
    ROLE_TEAM_LEAD: "Team Lead",
}


class User(db.Model):
    """
    A person who can sign in. Created by an admin on the Users page, or
    automatically (as a Requester) the first time someone signs in with
    their Microsoft 365 account.
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False)
    name = db.Column(db.String(MAX_SHORT), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_REQUESTER)
    department = db.Column(db.String(MAX_SHORT))  # printed on requisitions
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Microsoft Entra object id, linked on first Microsoft sign-in
    ms_oid = db.Column(db.String(64), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_login_at = db.Column(db.DateTime)

    def __init__(self, **kwargs):
        # column defaults only apply when saved; set them now so checks
        # like `user.active` work on a user that hasn't been saved yet
        kwargs.setdefault("role", ROLE_REQUESTER)
        kwargs.setdefault("is_admin", False)
        kwargs.setdefault("active", True)
        super().__init__(**kwargs)

    @property
    def role_label(self):
        label = ROLE_LABELS.get(self.role, self.role)
        return f"{label} · Admin" if self.is_admin else label

    def __repr__(self):
        return f"<User {self.email}>"


class ProcurementRequest(db.Model):
    __tablename__ = "procurement_requests"
    # AUTOINCREMENT keeps SQLite from reusing ids of deleted tickets (same as V0.3).
    __table_args__ = {"sqlite_autoincrement": True}

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String(20), unique=True, nullable=False)
    # optional; when empty the first item's name is shown instead (see `summary`)
    title = db.Column(db.String(MAX_ITEM))
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"))
    owner = db.Column(db.String(MAX_SHORT))
    category = db.Column(db.String(50))
    procurement_type = db.Column(db.String(50))
    priority = db.Column(db.String(20), nullable=False, default="Normal")
    description = db.Column(db.Text)
    request_date = db.Column(db.DateTime, default=datetime.now)
    required_by = db.Column(db.Date)
    status = db.Column(db.String(50), nullable=False, default=PROCUREMENT_FLOW[0])
    state = db.Column(db.String(20), nullable=False, default=STATE_ACTIVE)
    selected_vendor = db.Column(db.String(MAX_VENDOR))
    po_number = db.Column(db.String(MAX_PO))
    payment_status = db.Column(db.String(30), nullable=False, default="Pending")
    fulfilment_status = db.Column(db.String(30), nullable=False, default="Not Ordered")
    expected_delivery = db.Column(db.Date)
    archived = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    # who raised it in the app (empty for tickets created before V0.5)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # chosen by the engineer (or changed by the purchase team) before approval
    recommended_vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"))

    created_by = db.relationship("User")
    project = db.relationship("Project")
    recommended_vendor = db.relationship("Vendor")
    items = db.relationship(
        "TicketItem",
        back_populates="ticket",
        order_by="(TicketItem.position, TicketItem.id)",
        cascade="all, delete-orphan",
    )
    history = db.relationship(
        "StatusHistory",
        primaryjoin="foreign(StatusHistory.ticket_id)==ProcurementRequest.ticket_id",
        order_by="desc(StatusHistory.id)",
        cascade="all, delete-orphan",
    )

    # ---- workflow position ----

    @property
    def stage_index(self):
        """Position in PROCUREMENT_FLOW, or None if the stored status is unknown."""
        try:
            return PROCUREMENT_FLOW.index(self.status)
        except ValueError:
            return None

    @property
    def is_closed(self):
        return self.status == FINAL_STAGE

    @property
    def in_tracking(self):
        return self.status in TRACKING_STAGES

    @property
    def is_overdue(self):
        return (
            self.required_by is not None
            and self.required_by < date.today()
            and not self.is_closed
            and self.state != STATE_CANCELLED
        )

    # ---- items and requisition ----

    @property
    def summary(self):
        """Name shown in lists: the title, or the first item (+ how many more)."""
        if self.title:
            return self.title
        if not self.items:
            return "(no items)"
        more = len(self.items) - 1
        return self.items[0].description + (f" + {more} more" if more else "")

    @property
    def quantity_summary(self):
        """'10 Nos' for a single item, '3 items' otherwise."""
        if len(self.items) == 1:
            return f"{self.items[0].quantity} {self.items[0].unit}"
        return f"{len(self.items)} items"

    @property
    def project_name(self):
        return self.project.name if self.project else None

    @property
    def missing_for_approval(self):
        """What still has to be filled in before sending for approval (empty = ready)."""
        missing = []
        if self.recommended_vendor_id is None:
            missing.append("the vendor")
        if not self.items:
            missing.append("at least one item")
        elif not all(item.is_priced for item in self.items):
            missing.append("prices and GST for every item")
        return missing

    @property
    def requisition_complete(self):
        return not self.missing_for_approval

    @property
    def estimate(self):
        """Estimated cost of the priced items (a money.Totals), or None if none is priced."""
        priced = [item.estimate for item in self.items if item.is_priced]
        if not priced:
            return None
        totals = Totals()
        for amounts in priced:
            totals.add(amounts)
        return totals

    # ---- purchase orders ----

    @property
    def po_lines(self):
        return [line for item in self.items for line in item.po_lines]

    @property
    def active_po_lines(self):
        """Lines on draft or issued POs (cancelled POs don't count)."""
        return [line for item in self.items for line in item.active_po_lines]

    @property
    def is_orderable(self):
        """Approved and still open, so its items can go on POs."""
        return (
            self.state == STATE_ACTIVE
            and not self.archived
            and self.in_tracking
            and not self.is_closed
        )

    @property
    def orderable_items(self):
        return [item for item in self.items if item.can_be_ordered]

    @property
    def can_be_ordered(self):
        """Approved, still open, and with quantity left to put on a PO."""
        return bool(self.orderable_items)

    # ---- which actions are allowed right now ----

    @property
    def can_advance(self):
        return self.state == STATE_ACTIVE and self.stage_index is not None and not self.is_closed

    @property
    def can_send_back(self):
        return self.state == STATE_ACTIVE and bool(self.stage_index) and not self.is_closed

    @property
    def can_hold(self):
        return self.state == STATE_ACTIVE and not self.is_closed

    @property
    def can_resume(self):
        return self.state == STATE_ON_HOLD

    @property
    def can_cancel(self):
        return self.state in (STATE_ACTIVE, STATE_ON_HOLD) and not self.is_closed

    @property
    def can_reopen(self):
        return self.state == STATE_CANCELLED

    def __repr__(self):
        return f"<ProcurementRequest {self.ticket_id}>"


class TicketItem(db.Model):
    """
    One row of a requisition. The engineer enters description, quantity and
    unit; the purchase team adds the estimated price and GST from the quotation.
    """

    __tablename__ = "ticket_items"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("procurement_requests.id"), nullable=False, index=True
    )
    position = db.Column(db.Integer, nullable=False, default=1)  # Sr. No. on the form
    description = db.Column(db.String(MAX_ITEM), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit = db.Column(db.String(20), nullable=False, default="Nos")
    est_unit_price = db.Column(ExactDecimal(2))  # INR, excluding GST
    est_gst_rate = db.Column(ExactDecimal(2))

    ticket = db.relationship("ProcurementRequest", back_populates="items")
    po_lines = db.relationship("POLine", back_populates="item")

    @property
    def is_priced(self):
        return self.est_unit_price is not None and self.est_gst_rate is not None

    @property
    def estimate(self):
        """Estimated amounts (quantity x estimated price + GST), or None if not priced."""
        if not self.is_priced:
            return None
        return line_amounts(self.quantity, self.est_unit_price, self.est_gst_rate, TAX_INTER)

    @property
    def label(self):
        """'1. BLDC Motor' - used in timeline entries and error messages."""
        return f"{self.position}. {self.description}"

    @property
    def active_po_lines(self):
        """Lines on draft or issued POs (cancelled POs don't count)."""
        return [line for line in self.po_lines if line.po.status != PO_CANCELLED]

    @property
    def quantity_on_pos(self):
        """Quantity on draft or issued POs."""
        return sum(line.quantity for line in self.active_po_lines)

    @property
    def ordered_quantity(self):
        """Quantity on issued POs."""
        return sum(line.quantity for line in self.po_lines if line.po.status == PO_ISSUED)

    @property
    def remaining_quantity(self):
        """Quantity not yet on any draft or issued PO."""
        return max(self.quantity - self.quantity_on_pos, 0)

    @property
    def can_be_ordered(self):
        return self.ticket.is_orderable and self.remaining_quantity > 0


class StatusHistory(db.Model):
    __tablename__ = "status_history"
    __table_args__ = {"sqlite_autoincrement": True}

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String(20), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    event_type = db.Column(db.String(30), nullable=False)
    old_status = db.Column(db.String(50))
    new_status = db.Column(db.String(50))
    comment = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # name at the time of the event, so the timeline stays readable if the user is renamed
    actor_name = db.Column(db.String(MAX_SHORT))


# ---------------------------------------------------------------- purchasing


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(MAX_SHORT), unique=True, nullable=False)
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __init__(self, **kwargs):
        kwargs.setdefault("active", True)
        super().__init__(**kwargs)


class Vendor(db.Model):
    __tablename__ = "vendors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(MAX_VENDOR), unique=True, nullable=False)
    contact_person = db.Column(db.String(MAX_SHORT))
    email = db.Column(db.String(254))
    phone = db.Column(db.String(50))
    address = db.Column(db.Text)
    city = db.Column(db.String(MAX_SHORT))
    # GST state code (constants.INDIAN_STATES) for Indian vendors; empty for foreign ones
    state_code = db.Column(db.String(2))
    country = db.Column(db.String(MAX_SHORT), nullable=False, default=INDIA)
    gstin = db.Column(db.String(15))
    pan = db.Column(db.String(10))
    payment_terms = db.Column(db.String(200))
    default_currency = db.Column(db.String(3), nullable=False, default=BASE_CURRENCY)
    notes = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    purchase_orders = db.relationship("PurchaseOrder", back_populates="vendor")

    def __init__(self, **kwargs):
        kwargs.setdefault("active", True)
        kwargs.setdefault("country", INDIA)
        kwargs.setdefault("default_currency", BASE_CURRENCY)
        super().__init__(**kwargs)

    @property
    def is_foreign(self):
        return self.country != INDIA

    @property
    def state_name(self):
        from .constants import INDIAN_STATES

        return INDIAN_STATES.get(self.state_code or "", "")

    def tax_type_for(self, company):
        """Which GST applies when ERIC (company) buys from this vendor."""
        if self.is_foreign:
            return TAX_IMPORT
        if company.state_code and self.state_code == company.state_code:
            return TAX_INTRA
        return TAX_INTER


class CompanySettings(db.Model):
    """ERIC's own details, printed on every PO. Always a single row (id 1)."""

    __tablename__ = "company_settings"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(MAX_VENDOR), nullable=False)
    address = db.Column(db.Text)
    state_code = db.Column(db.String(2))
    gstin = db.Column(db.String(15))
    pan = db.Column(db.String(10))
    email = db.Column(db.String(254))
    phone = db.Column(db.String(50))
    delivery_address = db.Column(db.Text)
    plant = db.Column(db.String(MAX_VENDOR))  # e.g. "ERIC Robotics, MIDC Chinchwad"
    po_prefix = db.Column(db.String(30), nullable=False, default="PO")
    default_terms = db.Column(db.Text)

    @classmethod
    def get(cls):
        """The saved settings, or unsaved defaults if an admin hasn't filled them in yet."""
        return db.session.get(cls, 1) or cls(id=1, name="ERIC Robotics", po_prefix="ERIC/PO")

    @property
    def is_complete(self):
        return bool(self.name and self.address and self.state_code)


class PurchaseOrder(db.Model):
    """
    Draft: editable, no PO number yet.
    Issued: numbered, locked; vendor and company details are frozen in the
            *_snapshot columns so later edits don't change a sent PO.
    Cancelled: kept for the record; its lines no longer count as ordered.
    """

    __tablename__ = "purchase_orders"

    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(MAX_PO), unique=True)  # assigned when issued
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=PO_DRAFT)
    currency = db.Column(db.String(3), nullable=False, default=BASE_CURRENCY)
    exchange_rate = db.Column(ExactDecimal(4), nullable=False, default=Decimal(1))  # INR per unit
    tax_type = db.Column(db.String(10), nullable=False)
    po_date = db.Column(db.Date)  # date issued
    delivery_date = db.Column(db.Date)
    delivery_address = db.Column(db.Text)
    payment_terms = db.Column(db.String(200))
    terms = db.Column(db.Text)
    notes = db.Column(db.Text)
    vendor_snapshot = db.Column(db.JSON)
    company_snapshot = db.Column(db.JSON)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.now)
    issued_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    issued_at = db.Column(db.DateTime)
    cancelled_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    cancelled_at = db.Column(db.DateTime)
    cancel_reason = db.Column(db.Text)

    vendor = db.relationship("Vendor", back_populates="purchase_orders")
    lines = db.relationship(
        "POLine", back_populates="po", order_by="POLine.id", cascade="all, delete-orphan"
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    issued_by = db.relationship("User", foreign_keys=[issued_by_id])
    cancelled_by = db.relationship("User", foreign_keys=[cancelled_by_id])

    def __init__(self, **kwargs):
        kwargs.setdefault("status", PO_DRAFT)
        kwargs.setdefault("currency", BASE_CURRENCY)
        kwargs.setdefault("exchange_rate", Decimal(1))
        super().__init__(**kwargs)

    @property
    def display_number(self):
        return self.po_number or f"Draft #{self.id}"

    @property
    def is_draft(self):
        return self.status == PO_DRAFT

    @property
    def totals(self):
        totals = Totals()
        for line in self.lines:
            totals.add(line.amounts)
        return totals

    @property
    def total_inr(self):
        from .money import round2

        return round2(self.totals.total * self.exchange_rate)

    def vendor_details(self):
        """Vendor details to print: frozen copy once issued, live record while draft."""
        if self.vendor_snapshot:
            return self.vendor_snapshot
        from .purchasing import vendor_snapshot

        return vendor_snapshot(self.vendor)

    def company_details(self):
        if self.company_snapshot:
            return self.company_snapshot
        from .purchasing import company_snapshot

        return company_snapshot(CompanySettings.get())


class POLine(db.Model):
    __tablename__ = "po_lines"

    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("ticket_items.id"), nullable=False, index=True)
    description = db.Column(db.String(MAX_ITEM), nullable=False)
    hsn_sac = db.Column(db.String(10))
    quantity = db.Column(db.Integer, nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    unit_price = db.Column(ExactDecimal(2), nullable=False)
    gst_rate = db.Column(ExactDecimal(2), nullable=False, default=Decimal(0))

    po = db.relationship("PurchaseOrder", back_populates="lines")
    item = db.relationship("TicketItem", back_populates="po_lines")

    @property
    def ticket(self):
        return self.item.ticket

    @property
    def amounts(self):
        return line_amounts(self.quantity, self.unit_price, self.gst_rate, self.po.tax_type)
