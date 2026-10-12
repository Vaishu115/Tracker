# ProcureTrack Roadmap

What's done, and what was agreed for the next versions. Decisions below were made
with the product owner; talk to them before changing direction.

## Decisions that shape everything

- **Internal only.** Company staff use it; no clients or vendors log in.
- **Stay with Flask + server-rendered templates.** A React/API rewrite was considered and
  rejected: it would double the code for a small team. Add HTMX for interactive bits if needed.
- **Keep the requirement → PO flow.** Engineers raise needs; purchase turns them into POs.
  It isn't a PO-only system.
- **Microsoft 365** is the company's identity provider (sign-in, later email).
- **Approval:** only the Approver role (Chirag sir) approves; every ticket needs approval
  (no amount threshold yet); approvers may approve their own tickets (marked "Self-approved").
- **SQLite** in a data folder outside the code; PostgreSQL only if needed.

## Done

| Version | Summary |
|---|---|
| V0.3 | Original prototype: tickets, 15-stage flow, history, archive |
| V0.4 | Restructured into a package, migrations, validation, edit, hold/cancel/send back, backups, CSRF, production server |
| V0.5 | Sign-in (Microsoft 365 + local test mode), roles, approval gate, Users page, "who did it" in the timeline |
| V0.6 | Vendors, projects, company settings; purchase orders with full GST, foreign currency, split/combined tickets, printable PO |
| V0.6.1 | Requisition in ERIC's paper format, printable requisition form |
| V0.6.2 | Several items per ticket; engineers choose vendor + items, purchase adds prices; "add all items" to a PO |

Details: [versionhistory.md](../versionhistory.md).

## Next

**Going live:** handed to the FSD team with [DEPLOYMENT.md](DEPLOYMENT.md).

**Waiting on IT:** a Microsoft Entra app registration (tenant id, client id, secret) to switch
on Microsoft 365 sign-in. The steps are in the README.

**Purchasing decisions (V0.6, revised in V0.6.2):** a ticket is one purchase requisition:
one recommended vendor and several items (quantity and unit each). **Engineers choose the
vendor and items; the purchase team adds prices** and can change vendor/items until approval.
A PO can combine items from several tickets and an item can be split across vendors; full GST
(CGST+SGST / IGST by vendor state, HSN per line); INR and foreign currencies with an
exchange rate. Printed forms follow ERIC's paper format exactly (Operations Manager box
stays for a manual signature).

**Waiting on the purchase team:** their SOPs, to check the 15 stages and approval rules
against how ERIC actually buys.

**V0.6.x: small follow-ups**
- Item catalog: code, name, unit, HSN, default GST, last price, preferred vendor
- Vendor bank details (encrypted, admin-only)
- Freight / other charges on a PO

**V0.7: Approvals, receiving, money**
- Optional approval by amount (thresholds, second approver)
- Goods receipt (GRN): partial / full receipt, QC pass/fail
- Payments (advance / partial / full) and invoices; PO = received = invoiced check
- Budgets per department / project, budget vs actual
- Dashboard charts: spend by vendor / category, on-time delivery

**V0.8: Notifications and reporting**
- Email via Microsoft 365: approval needed, rejected, overdue
- CSV import, Excel export (openpyxl is already installed), reports page
- Attachments (quotes, POs, invoices): files stored in the data folder, paths in the database
- Queue search / filters, saved views
