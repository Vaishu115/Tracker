"""
Sign-in.

Two modes (AUTH_MODE, chosen automatically in create_app):

  microsoft  Staff sign in with their Microsoft 365 account (Entra ID).
             Used whenever the PROCURETRACK_MS_* settings are present.
             First-time users are created as Requesters; an admin then
             assigns roles on the Users page.

  dev        No Microsoft settings yet. The login page lists users to
             "sign in as", for testing. Only works for requests made on
             this computer (127.0.0.1), so it can't be used over the network.
"""

from datetime import datetime

import msal
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, select

from ..extensions import db
from ..models import ROLE_REQUESTER, User

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {
    "auth.login",
    "auth.dev_login",
    "auth.microsoft_start",
    "auth.microsoft_callback",
    "main.health",
    "static",
}


# ---- current user + login required everywhere ----


@bp.before_app_request
def load_current_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        user = db.session.get(User, user_id)
        if user is not None and user.active:
            g.user = user
        else:
            session.clear()

    # request.endpoint is None for unknown URLs: let the 404 page handle those
    if g.user is None and request.endpoint is not None and request.endpoint not in PUBLIC_ENDPOINTS:
        next_url = request.full_path.rstrip("?") if request.method == "GET" else None
        return redirect(url_for("auth.login", next=next_url))


def safe_next(url):
    """Only allow redirects to pages on this site."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return url_for("main.dashboard")


def login_user(user):
    user.last_login_at = datetime.now()
    if user.email in current_app.config["ADMIN_EMAILS"] and not user.is_admin:
        user.is_admin = True
        current_app.logger.warning(
            "%s made admin (listed in PROCURETRACK_ADMIN_EMAILS)", user.email
        )
    db.session.commit()  # before reading user.id: a new user gets its id here

    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    current_app.logger.info("Sign-in: %s", user.email)


def is_local_request():
    """The test sign-in: only from this computer, and never in production."""
    if current_app.config.get("PRODUCTION"):
        return False
    return request.remote_addr in ("127.0.0.1", "::1")


# ---- login page ----


@bp.route("/login")
def login():
    if g.user is not None:
        return redirect(safe_next(request.args.get("next")))

    mode = current_app.config["AUTH_MODE"]
    dev_users = []
    if mode == "dev" and is_local_request():
        dev_users = db.session.scalars(
            select(User).where(User.active.is_(True)).order_by(User.name)
        ).all()

    return render_template(
        "login.html",
        mode=mode,
        dev_allowed=is_local_request(),
        dev_users=dev_users,
        next=request.args.get("next", ""),
    )


@bp.route("/login/dev", methods=["POST"])
def dev_login():
    if current_app.config["AUTH_MODE"] != "dev" or not is_local_request():
        abort(403)
    user = db.session.get(User, request.form.get("user_id", type=int))
    if user is None or not user.active:
        flash("Choose an active user.", "error")
        return redirect(url_for("auth.login"))
    login_user(user)
    return redirect(safe_next(request.form.get("next")))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


# ---- Microsoft 365 ----


def msal_app():
    config = current_app.config
    return msal.ConfidentialClientApplication(
        config["MS_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{config['MS_TENANT_ID']}",
        client_credential=config["MS_CLIENT_SECRET"],
    )


def redirect_uri():
    return current_app.config["MS_REDIRECT_URI"] or url_for(
        "auth.microsoft_callback", _external=True
    )


@bp.route("/auth/microsoft")
def microsoft_start():
    if current_app.config["AUTH_MODE"] != "microsoft":
        abort(404)
    try:
        flow = msal_app().initiate_auth_code_flow(["User.Read"], redirect_uri=redirect_uri())
    except Exception:
        # usually a wrong tenant id, or the server can't reach login.microsoftonline.com
        current_app.logger.exception("Microsoft sign-in could not start")
        return login_failed(
            "Microsoft sign-in isn't working on this server right now. "
            "Please tell the ProcureTrack admin."
        )
    session["auth_flow"] = flow
    session["login_next"] = request.args.get("next", "")
    return redirect(flow["auth_uri"])


def login_failed(message):
    flash(message, "error")
    return redirect(url_for("auth.login"))


@bp.route("/auth/callback")
def microsoft_callback():
    if current_app.config["AUTH_MODE"] != "microsoft":
        abort(404)

    flow = session.pop("auth_flow", None)
    next_url = session.pop("login_next", "")
    if flow is None:
        return login_failed("Your sign-in took too long. Please try again.")

    try:
        result = msal_app().acquire_token_by_auth_code_flow(flow, request.args.to_dict())
    except ValueError:
        # state mismatch: the response doesn't belong to this browser's sign-in
        return login_failed("Sign-in could not be verified. Please try again.")

    if "error" in result:
        current_app.logger.warning(
            "Microsoft sign-in failed: %s %s", result.get("error"), result.get("error_description")
        )
        return login_failed(
            "Microsoft sign-in failed. Please try again or contact the ProcureTrack admin."
        )

    claims = result.get("id_token_claims") or {}
    if claims.get("tid") != current_app.config["MS_TENANT_ID"]:
        return login_failed("Please sign in with your ERIC Robotics work account.")

    oid = claims.get("oid")
    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    name = (claims.get("name") or email).strip()
    if not oid or not email:
        return login_failed("Your Microsoft account did not provide an email address.")

    user = db.session.scalar(select(User).where(User.ms_oid == oid))
    if user is None:
        user = db.session.scalar(select(User).where(func.lower(User.email) == email))
        if user is not None and user.ms_oid not in (None, oid):
            current_app.logger.warning(
                "Sign-in refused: %s is linked to a different Microsoft account", email
            )
            return login_failed(
                "This email is linked to a different Microsoft account. Contact the ProcureTrack admin."
            )

    if user is None:
        user = User(email=email, name=name[:100], role=ROLE_REQUESTER, ms_oid=oid)
        db.session.add(user)
        current_app.logger.info("New user from Microsoft sign-in: %s", email)
    else:
        user.ms_oid = oid

    if not user.active:
        db.session.commit()
        return login_failed(
            "Your ProcureTrack access has been disabled. Contact the ProcureTrack admin."
        )

    login_user(user)
    return redirect(safe_next(next_url))
