"""Company settings (admins only): ERIC's details printed on POs, and PO numbering."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..constants import INDIAN_STATES
from ..extensions import db
from ..forms import validate_settings_form
from ..models import CompanySettings
from ..permissions import require

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.before_request
def admins_only():
    require("company_settings")


@bp.route("/", methods=["GET", "POST"])
def company():
    settings = CompanySettings.get()

    if request.method == "POST":
        data, errors = validate_settings_form(request.form)
        if errors:
            return render_template(
                "settings.html", values=request.form, errors=errors, states=INDIAN_STATES
            ), 400
        for field, value in data.items():
            setattr(settings, field, value)
        db.session.add(settings)
        db.session.commit()
        flash("Company settings saved.", "success")
        return redirect(url_for("settings.company"))

    values = {
        "name": settings.name,
        "address": settings.address or "",
        "state_code": settings.state_code or "",
        "gstin": settings.gstin or "",
        "pan": settings.pan or "",
        "email": settings.email or "",
        "phone": settings.phone or "",
        "delivery_address": settings.delivery_address or "",
        "plant": settings.plant or "",
        "po_prefix": settings.po_prefix,
        "default_terms": settings.default_terms or "",
    }
    return render_template("settings.html", values=values, errors={}, states=INDIAN_STATES)
