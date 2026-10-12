"""Users page (admins only): add people and set their role."""

import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from ..constants import MAX_SHORT
from ..extensions import db
from ..models import ROLE_LABELS, ROLE_REQUESTER, User
from ..permissions import require

bp = Blueprint("users", __name__, url_prefix="/users")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.before_request
def admins_only():
    require("manage_users")


def active_admin_count():
    return db.session.scalar(
        select(func.count()).select_from(User).where(User.is_admin.is_(True), User.active.is_(True))
    )


def validate_user_form(form, existing=None):
    errors = {}
    email = (form.get("email") or "").strip().lower()
    name = (form.get("name") or "").strip()
    role = form.get("role") or ROLE_REQUESTER

    if existing is None:
        if not EMAIL_RE.match(email):
            errors["email"] = "Enter a valid email address."
        elif db.session.scalar(select(User).where(func.lower(User.email) == email)):
            errors["email"] = "A user with this email already exists."
    if not name:
        errors["name"] = "Name is required."
    elif len(name) > MAX_SHORT:
        errors["name"] = f"Name must be at most {MAX_SHORT} characters."
    if role not in ROLE_LABELS:
        errors["role"] = "Choose a valid role."
    department = (form.get("department") or "").strip()
    if len(department) > MAX_SHORT:
        errors["department"] = f"Department must be at most {MAX_SHORT} characters."

    return {
        "email": email,
        "name": name,
        "role": role,
        "department": department or None,
        "is_admin": form.get("is_admin") == "on",
    }, errors


def render_users(values=None, errors=None):
    users = db.session.scalars(select(User).order_by(User.active.desc(), User.name)).all()
    return render_template(
        "users.html",
        users=users,
        roles=ROLE_LABELS,
        values=values or {"role": ROLE_REQUESTER},
        errors=errors or {},
    ), 400 if errors else 200


@bp.route("/")
def index():
    return render_users()


@bp.route("/", methods=["POST"])
def add():
    data, errors = validate_user_form(request.form)
    if errors:
        return render_users(request.form, errors)
    db.session.add(User(**data))
    db.session.commit()
    flash(f"{data['name']} added as {ROLE_LABELS[data['role']]}.", "success")
    return redirect(url_for("users.index"))


@bp.route("/<int:user_id>", methods=["POST"])
def update(user_id):
    user = db.get_or_404(User, user_id)
    data, errors = validate_user_form(request.form, existing=user)
    if errors:
        flash(" ".join(errors.values()), "error")
        return redirect(url_for("users.index"))

    active = request.form.get("active") == "on"

    # don't let the last admin lock everyone out
    losing_admin = user.is_admin and user.active and (not data["is_admin"] or not active)
    if losing_admin and active_admin_count() <= 1:
        flash("There must be at least one active admin.", "error")
        return redirect(url_for("users.index"))
    if user.id == g.user.id and (not data["is_admin"] or not active):
        flash(
            "You can't remove your own admin access or disable yourself. Ask another admin.",
            "error",
        )
        return redirect(url_for("users.index"))

    user.name = data["name"]
    user.role = data["role"]
    user.department = data["department"]
    user.is_admin = data["is_admin"]
    user.active = active
    db.session.commit()
    flash(f"{user.name} updated.", "success")
    return redirect(url_for("users.index"))
