"""Vendor directory: list/search, add, edit, deactivate, and each vendor's POs."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from ..constants import CURRENCIES, INDIA, INDIAN_STATES, PO_ISSUED
from ..extensions import db
from ..forms import validate_vendor_form
from ..models import Vendor
from ..money import ZERO
from ..permissions import require

bp = Blueprint("vendors", __name__, url_prefix="/vendors")


def render_form(values, errors, vendor=None):
    return render_template(
        "vendor_form.html",
        vendor=vendor,
        values=values,
        errors=errors,
        states=INDIAN_STATES,
        currencies=CURRENCIES,
    ), 400 if errors else 200


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    show_inactive = request.args.get("show") == "all"
    query = select(Vendor).order_by(Vendor.name)
    if not show_inactive:
        query = query.where(Vendor.active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(
                Vendor.name.ilike(like),
                Vendor.city.ilike(like),
                Vendor.gstin.ilike(like),
                Vendor.contact_person.ilike(like),
            )
        )
    vendors = db.session.scalars(query).all()
    return render_template("vendors.html", vendors=vendors, q=q, show_inactive=show_inactive)


@bp.route("/new", methods=["GET", "POST"])
def new():
    require("manage_vendors")
    if request.method == "POST":
        data, errors = validate_vendor_form(request.form)
        if errors:
            return render_form(request.form, errors)
        vendor = Vendor(**data)
        db.session.add(vendor)
        db.session.commit()
        flash(f"Vendor {vendor.name} added.", "success")
        return redirect(url_for("vendors.detail", vendor_id=vendor.id))
    return render_form({"country": INDIA, "default_currency": "INR"}, {})


@bp.route("/<int:vendor_id>")
def detail(vendor_id):
    vendor = db.get_or_404(Vendor, vendor_id)
    pos = sorted(vendor.purchase_orders, key=lambda po: po.id, reverse=True)
    issued = [po for po in pos if po.status == PO_ISSUED]
    total_inr = sum((po.total_inr for po in issued), ZERO)
    return render_template(
        "vendor_detail.html", vendor=vendor, pos=pos, issued=issued, total_inr=total_inr
    )


@bp.route("/<int:vendor_id>/edit", methods=["GET", "POST"])
def edit(vendor_id):
    require("manage_vendors")
    vendor = db.get_or_404(Vendor, vendor_id)
    if request.method == "POST":
        data, errors = validate_vendor_form(request.form, existing=vendor)
        if errors:
            return render_form(request.form, errors, vendor)
        for field, value in data.items():
            setattr(vendor, field, value)
        db.session.commit()
        flash("Vendor updated. Issued POs keep the details they were issued with.", "success")
        return redirect(url_for("vendors.detail", vendor_id=vendor.id))

    values = {
        field: getattr(vendor, field) or ""
        for field in (
            "name contact_person email phone address city state_code country gstin pan "
            "payment_terms default_currency notes"
        ).split()
    }
    return render_form(values, {}, vendor)


@bp.route("/<int:vendor_id>/active", methods=["POST"])
def toggle_active(vendor_id):
    require("manage_vendors")
    vendor = db.get_or_404(Vendor, vendor_id)
    vendor.active = not vendor.active
    db.session.commit()
    flash(f"{vendor.name} {'reactivated' if vendor.active else 'deactivated'}.", "success")
    return redirect(url_for("vendors.detail", vendor_id=vendor.id))
