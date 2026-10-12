"""Dashboard (counts, recent tickets, the approver's to-do list) and Reports."""

from datetime import date

from flask import Blueprint, current_app, g, render_template
from sqlalchemy import func, select

from .. import __version__
from ..constants import APPROVAL_STAGE, FINAL_STAGE, STATE_ACTIVE, STATE_CANCELLED, STATE_ON_HOLD
from ..extensions import db
from ..models import ROLE_APPROVER, ROLE_TEAM_LEAD
from ..models import ProcurementRequest as PR
from ..permissions import can_approve

bp = Blueprint("main", __name__)


def count(*conditions):
    """Number of non-archived tickets matching the conditions."""
    return db.session.scalar(
        select(func.count()).select_from(PR).where(PR.archived.is_(False), *conditions)
    )


@bp.route("/")
def dashboard():
    stats = {
        "active": count(PR.state == STATE_ACTIVE, PR.status != FINAL_STAGE),
        "approval": count(PR.state == STATE_ACTIVE, PR.status == APPROVAL_STAGE),
        "payment": count(PR.state == STATE_ACTIVE, PR.status == "Payment Processing"),
        "transit": count(PR.state == STATE_ACTIVE, PR.status == "In Transit"),
        "on_hold": count(PR.state == STATE_ON_HOLD),
        "overdue": count(
            PR.required_by < date.today(),
            PR.status != FINAL_STAGE,
            PR.state != STATE_CANCELLED,
        ),
    }
    recent = db.session.scalars(
        select(PR).where(PR.archived.is_(False)).order_by(PR.id.desc()).limit(5)
    ).all()

    my_approvals = []
    if g.user.role in (ROLE_APPROVER, ROLE_TEAM_LEAD):
        pending = db.session.scalars(
            select(PR)
            .where(PR.archived.is_(False), PR.state == STATE_ACTIVE, PR.status == APPROVAL_STAGE)
            .order_by(PR.id)
        ).all()
        my_approvals = [ticket for ticket in pending if can_approve(g.user, ticket)]

    return render_template("dashboard.html", stats=stats, recent=recent, my_approvals=my_approvals)


@bp.route("/health")
def health():
    """For the hosting platform's health check / uptime monitor. No sign-in needed."""
    try:
        db.session.execute(select(1))
    except Exception:
        current_app.logger.exception("Health check: database not reachable")
        return {"status": "error", "database": "unreachable"}, 503
    return {"status": "ok", "version": __version__}


@bp.route("/reports")
def reports():
    return render_template("reports.html")
