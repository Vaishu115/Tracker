# Deploying ProcureTrack

For the team putting ProcureTrack live. It covers where to host it, how to install and
configure it, Microsoft 365 sign-in, backups and upgrades.

- How the code works: [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)
- Planned work: [ROADMAP.md](ROADMAP.md)
- Running it on a single PC: [README](../README.md)

## 1. What you are deploying

ProcureTrack is ERIC Robotics' internal procurement tool. Engineers raise purchase
requisitions; the purchase team prices them; the Director approves them; the purchase
team issues purchase orders and tracks delivery. Only ERIC staff use it.

| | |
|---|---|
| Users | ~10–50 ERIC staff, all with Microsoft 365 accounts |
| Stack | Python 3.12+, Flask 3, SQLAlchemy 2 + Alembic, Jinja2 templates, plain CSS/JS (no build step) |
| Web server | waitress (pure Python, Windows and Linux), started by `python app.py` |
| Database | SQLite file in a data folder (one file, plus backups and logs next to it) |
| Sign-in | Microsoft 365 / Entra ID (OAuth authorization-code flow via MSAL) |
| Outbound network | Only `login.microsoftonline.com` (sign-in) |
| Version | see `procuretrack/__init__.py` and [versionhistory.md](../versionhistory.md) |

## 2. Where to host it

**Constraint:** the database is SQLite. It must sit on the **local disk of one server** with
**one app instance**. Network file shares (SMB, Azure Files) and several instances behind a
load balancer can corrupt it or lock up. That is plenty for ERIC's user count.

| Option | Fit |
|---|---|
| **A. One small VM** (Azure VM, or an office server), Windows Server or Linux, with IIS or nginx in front for HTTPS | **Recommended.** Runs exactly as tested. 2 vCPU / 4 GB RAM / 20 GB disk is more than enough |
| B. Azure App Service (Linux, Python) | Possible, but only with a **single instance** and the data folder on `/home`, which is a network share, so SQLite locking isn't guaranteed. Prefer A |
| C. App Service / containers with PostgreSQL | Needs development work first: `DATABASE_URL` is supported in code, but the migrations and app have **only been tested on SQLite**, and a PostgreSQL driver isn't in `requirements.txt` |

**ERIC's plan: Cloudflare.** Cloudflare does not run Python/Flask apps itself (its
Pages/Workers products are for static sites and JavaScript). It sits **in front of** a server
that runs ProcureTrack. The usual setup is **Cloudflare Tunnel**: the app runs on one
always-on machine (an office server/PC, or a VM as in option A), and a small Cloudflare
program on that machine (`cloudflared`) connects out to Cloudflare. Cloudflare then
publishes it at an HTTPS address on ERIC's domain. No inbound ports or router changes are
needed, and Cloudflare provides the HTTPS certificate. See section 3.7.

The rest of this guide assumes option A; with Cloudflare Tunnel, section 3.7 replaces the
nginx/IIS step (3.6).

## 3. Install (option A)

### 3.1 Prerequisites

- Python **3.12 or newer**, and git
- A DNS name for the app, e.g. `procuretrack.ericrobotics.com` (internal is fine), with a TLS
  certificate. **Microsoft sign-in only works over HTTPS.**
- IIS with *URL Rewrite* + *Application Request Routing* (Windows), or nginx (Linux), as the
  reverse proxy that terminates HTTPS

### 3.2 Code and Python packages

```bash
git clone https://github.com/srishtic-ericr/ProcureTrack.git /opt/procuretrack   # Windows: e.g. C:\ProcureTrack\app
cd /opt/procuretrack
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt          # Windows: .venv\Scripts\python ...
```

Keep the data folder **outside** the code folder, e.g. `/var/lib/procuretrack`, or
`C:\ProcureTrack\data` on Windows. Only the account running the app should be able to read it:
it holds the database and the session-signing key.

### 3.3 Configuration (`.env`)

Copy `.env.example` to `.env` in the code folder (or set real environment variables, which
take precedence) and set:

```ini
PROCURETRACK_ENV=production
PROCURETRACK_DATA_DIR=/var/lib/procuretrack
PROCURETRACK_HOST=127.0.0.1          # only the reverse proxy talks to the app
PROCURETRACK_PORT=5000

PROCURETRACK_MS_TENANT_ID=<directory (tenant) id>
PROCURETRACK_MS_CLIENT_ID=<application (client) id>
PROCURETRACK_MS_CLIENT_SECRET=<client secret value>
PROCURETRACK_ADMIN_EMAILS=<first admin's Microsoft 365 address>

PROCURETRACK_OFFSITE_BACKUP_DIR=<another disk or network folder>   # recommended
```

`.env` contains a secret: keep it readable only by the service account, and never commit it.

What `PROCURETRACK_ENV=production` does:
- **The test "Sign in as" page is switched off completely.** Without Microsoft 365 settings,
  nobody can sign in, and an error is logged at startup.
- Session cookies are sent over HTTPS only.
- `X-Forwarded-Proto` / `-Host` / `-For` from **one** reverse proxy are trusted, so links and
  the Microsoft redirect address use `https://`. Don't expose the app port directly: the
  proxy must be the only way in.

All variables are listed in the README's *Configuration* table and in `procuretrack/config.py`.

### 3.4 Microsoft 365 app registration (Entra admin center)

1. *App registrations → New registration*, name `ProcureTrack`,
   **Accounts in this organizational directory only**.
2. Redirect URI, platform **Web**: `https://<your host>/auth/callback`
3. *Certificates & secrets → New client secret*. Put the **Value** in `.env` and **record the
   expiry date**: sign-in stops working when it expires.
4. Copy the *Application (client) ID* and *Directory (tenant) ID* into `.env`.
5. API permissions: the default **Microsoft Graph → User.Read** is enough. No admin consent
   is needed beyond that.

Anyone in the tenant can then sign in. First-time users get the **Requester** role. Admins
change roles on the Users page.

### 3.5 Run it as a service

The app is started with `python app.py` from the code folder. It creates or upgrades the
database on startup (see section 5).

**Linux (systemd)**, `/etc/systemd/system/procuretrack.service`:

```ini
[Unit]
Description=ProcureTrack
After=network.target

[Service]
User=procuretrack
WorkingDirectory=/opt/procuretrack
ExecStart=/opt/procuretrack/.venv/bin/python app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**Windows:** run `scripts\start.bat` as a service, using a service wrapper such as NSSM, or
as a Task Scheduler task "At startup" under a dedicated account.

### 3.6 Reverse proxy (HTTPS)

nginx example:

```nginx
server {
    listen 443 ssl;
    server_name procuretrack.ericrobotics.com;
    ssl_certificate     /etc/ssl/procuretrack.crt;
    ssl_certificate_key /etc/ssl/procuretrack.key;
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server { listen 80; server_name procuretrack.ericrobotics.com; return 301 https://$host$request_uri; }
```

On IIS, do the same with an ARR reverse-proxy rule to `http://127.0.0.1:5000`, and make it
send `X-Forwarded-Proto: https`.

### 3.7 Cloudflare Tunnel (instead of 3.6)

Prerequisite: ERIC's domain is managed in Cloudflare (DNS on Cloudflare).

1. Cloudflare dashboard → *Zero Trust → Networks → Tunnels* → **Create a tunnel**
   (type *Cloudflared*), name it `procuretrack`.
2. Install the connector on the server with the command the dashboard shows. It installs
   `cloudflared` as a service, so it starts with the machine.
3. Add a **public hostname**, e.g. `procuretrack.ericrobotics.com`, with service
   `http://localhost:5000`.
4. Use `https://procuretrack.ericrobotics.com/auth/callback` as the redirect URI in the
   Entra app registration (3.4).

Keep `PROCURETRACK_HOST=127.0.0.1` and `PROCURETRACK_ENV=production`. The app then accepts
connections only from the tunnel on the same machine, and it trusts the
`X-Forwarded-Proto: https` header that `cloudflared` sends, so links and the Microsoft
redirect use https.

Optional extra layer: **Cloudflare Access** (*Zero Trust → Access → Applications*) with
Microsoft Entra ID as the identity provider blocks everyone outside ERIC before they reach
the app. ProcureTrack still does its own Microsoft sign-in, so users may see two sign-ins
(usually silent the second time).

The machine running the app and `cloudflared` must stay on, because that is where the
database is. Back it up as in section 4.

### 3.8 Check it

- `https://<host>/health` returns `{"status": "ok", "version": "..."}`, or HTTP 503 if the
  database can't be reached. Point the uptime monitor here; it doesn't need sign-in.
- The login page shows **Sign in with Microsoft 365**, not a "Sign in as" list.
- Log: `<data folder>/logs/procuretrack.log`

## 4. Backups

- **Automatic:** before every database upgrade.
- **Daily (set this up):** run `flask --app app backup` from the code folder:
  - Linux cron: `0 18 * * * cd /opt/procuretrack && .venv/bin/flask --app app backup`
  - Windows: Task Scheduler running `scripts\backup.bat` (see README)

  The command uses SQLite's online backup API, so it's safe while the app is running. It
  keeps the newest 30 in `<data folder>/backups`, and copies each backup to
  `PROCURETRACK_OFFSITE_BACKUP_DIR` if that is set.
- Also include the data folder in the server's normal backup or VM snapshot.
- **Restore:** stop the service, copy a backup over `<data folder>/procurement.db`, start the service.

## 5. Upgrades

```bash
cd /opt/procuretrack && git pull
.venv/bin/python -m pip install -r requirements.txt
systemctl restart procuretrack
```

On startup the app backs up the database and applies any new migrations (`migrations/versions/`)
automatically. Check the log afterwards. Migrations can't be reversed: to roll back, restore the
pre-migration backup and check out the previous version.

## 6. Go-live checklist

- [ ] HTTPS works and `/health` is OK
- [ ] Microsoft sign-in works; the first admin (from `PROCURETRACK_ADMIN_EMAILS`) can open **Users**
- [ ] **Start with an empty database.** The data on the development PC contains test users,
      vendors and tickets. Copy it over only if the product owner asks for that.
- [ ] Admin fills in **Settings**: company name, address, state, GSTIN, PAN, plant, PO prefix
- [ ] Users page: give Team Leads the **Team Lead** role and only Chirag the **Approver** role,
      give the purchase team **Purchase team**,
      and set everyone's department
- [ ] Purchase team adds vendors and projects
- [ ] Daily backup scheduled; off-site copy set; restore tested once
- [ ] Client-secret expiry date in someone's calendar
- [ ] Remove `PROCURETRACK_ADMIN_EMAILS` once the admin exists (optional; it only promotes on sign-in)

## 7. Security notes

- Sign-in is required for every page except `/login`, `/auth/*`, `/health` and static files.
- Permissions are enforced on the server (`procuretrack/permissions.py`), not only by
  hiding buttons.
- Every form has a CSRF token. Sessions are signed with a key in `<data folder>/secret_key`
  (or `PROCURETRACK_SECRET_KEY`), and expire after 12 hours.
- Never run with `flask run --debug` on the server: the debugger allows running code.
- Uploads are limited to 2 MB (`MAX_CONTENT_LENGTH`). The app doesn't accept file uploads yet.

## 8. Known limitations

- Single instance + SQLite (see section 2).
- No email notifications yet; people check the dashboard. Planned: Microsoft 365 email.
- Goods receipt, payments/invoices, attachments and Excel export are not built yet
  (see [ROADMAP.md](ROADMAP.md)).
- The 15 workflow stages haven't yet been checked against the purchase team's SOPs.

## 9. Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # all tests must pass before deploying a new version
.venv/bin/ruff check .
```
