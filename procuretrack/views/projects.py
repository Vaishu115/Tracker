"""Projects page: the list tickets choose from. Purchase team / admins manage it."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from ..constants import FINAL_STAGE, STATE_CANCELLED
from ..extensions import db
from ..forms import validate_project_form
from ..models import ProcurementRequest, Project
from ..permissions import require

bp = Blueprint("projects", __name__, url_prefix="/projects")


def render_projects(values=None, errors=None):
    projects = db.session.scalars(
        select(Project).order_by(Project.active.desc(), Project.name)
    ).all()
    open_counts = dict(
        db.session.execute(
            select(ProcurementRequest.project_id, func.count())
            .where(
                ProcurementRequest.archived.is_(False),
                ProcurementRequest.status != FINAL_STAGE,
                ProcurementRequest.state != STATE_CANCELLED,
            )
            .group_by(ProcurementRequest.project_id)
        ).all()
    )
    return render_template(
        "projects.html",
        projects=projects,
        open_counts=open_counts,
        values=values or {},
        errors=errors or {},
    ), 400 if errors else 200


@bp.route("/")
def index():
    return render_projects()


@bp.route("/", methods=["POST"])
def add():
    require("manage_projects")
    data, errors = validate_project_form(request.form)
    if errors:
        return render_projects(request.form, errors)
    db.session.add(Project(**data))
    db.session.commit()
    flash(f"Project {data['name']} added.", "success")
    return redirect(url_for("projects.index"))


@bp.route("/<int:project_id>", methods=["POST"])
def update(project_id):
    require("manage_projects")
    project = db.get_or_404(Project, project_id)
    data, errors = validate_project_form(request.form, existing=project)
    if errors:
        flash(" ".join(errors.values()), "error")
        return redirect(url_for("projects.index"))
    project.name = data["name"]
    project.description = data["description"]
    project.active = request.form.get("active") == "on"
    db.session.commit()
    flash(f"Project {project.name} updated.", "success")
    return redirect(url_for("projects.index"))
