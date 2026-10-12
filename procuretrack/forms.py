"""
Form validation.

Each validate_* function takes request.form and returns (data, errors):
  data   - cleaned values ready to save (dates parsed, numbers converted)
  errors - {field_name: message}; empty when the form is valid
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from .constants import (
    BASE_CURRENCY,
    CATEGORIES,
    CURRENCIES,
    FULFILMENT_STATUSES,
    INDIA,
    INDIAN_STATES,
    MAX_COMMENT,
    MAX_DESCRIPTION,
    MAX_ITEM,
    MAX_PO,
    MAX_QUANTITY,
    MAX_SHORT,
    MAX_VENDOR,
    PAYMENT_STATUSES,
    PRIORITIES,
    PROCUREMENT_TYPES,
    UNITS,
)
from .extensions import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 2-digit state code, PAN (5 letters, 4 digits, letter), entity number, 'Z', check character
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
HSN_RE = re.compile(r"^\d{4,8}$")


# ---- field helpers ----


def _text(form, name, label, max_len, errors, required=False):
    value = (form.get(name) or "").strip()
    if required and not value:
        errors[name] = f"{label} is required."
    elif len(value) > max_len:
        errors[name] = f"{label} must be at most {max_len} characters."
    return value


def _choice(form, name, label, options, errors, required=True):
    value = form.get(name) or ""
    if not value and not required:
        return None
    if value not in options:
        errors[name] = f"Choose a valid {label.lower()}."
    return value


def _date(form, name, label, errors):
    value = (form.get(name) or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[name] = f"{label} must be a valid date."
        return None


def _int(form, name, label, errors, minimum, maximum):
    raw = (form.get(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        errors[name] = f"{label} must be a whole number."
        return None
    if not minimum <= value <= maximum:
        errors[name] = f"{label} must be between {minimum:,} and {maximum:,}."
    return value


def _decimal(form, name, label, errors, minimum, maximum, places):
    raw = (form.get(name) or "").strip().replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors[name] = f"{label} must be a number."
        return None
    if not value.is_finite() or not Decimal(minimum) <= value <= Decimal(maximum):
        errors[name] = f"{label} must be between {minimum} and {maximum}."
        return None
    if value.as_tuple().exponent < -places:
        errors[name] = f"{label} can have at most {places} decimal places."
        return None
    return value


def _email(form, name, errors):
    value = _text(form, name, "Email", 254, errors).lower()
    if value and not EMAIL_RE.match(value):
        errors[name] = "Enter a valid email address."
    return value


def _upper(form, name):
    return (form.get(name) or "").strip().upper().replace(" ", "")


def _gstin_and_state(form, errors, state_required):
    """GSTIN must be well-formed and start with the selected state's code."""
    state_code = form.get("state_code") or None
    if state_code is None:
        if state_required:
            errors["state_code"] = "State is required."
    elif state_code not in INDIAN_STATES:
        errors["state_code"] = "Choose a valid state."
    gstin = _upper(form, "gstin")
    if gstin:
        if not GSTIN_RE.match(gstin):
            errors["gstin"] = "GSTIN must be 15 characters, e.g. 29ABCDE1234F1Z5."
        elif state_code and gstin[:2] != state_code:
            errors["gstin"] = (
                f"This GSTIN is registered in {INDIAN_STATES.get(gstin[:2], 'another state')}, "
                "not the selected state."
            )
    return state_code, gstin


def _pan(form, errors, gstin=None):
    """
    PAN must be well-formed and match characters 3-12 of the GSTIN (a GSTIN
    contains the PAN). Left empty, it is taken from a valid GSTIN.
    """
    pan = _upper(form, "pan")
    gstin_pan = gstin[2:12] if gstin and "gstin" not in errors else None
    if not pan:
        return gstin_pan
    if not PAN_RE.match(pan):
        errors["pan"] = "PAN must be 10 characters, e.g. ABCDE1234F."
    elif gstin_pan and pan != gstin_pan:
        errors["pan"] = f"PAN doesn't match the GSTIN, which contains the PAN {gstin_pan}."
    return pan


# ---- tickets ----

MAX_ITEMS = 50  # item rows per ticket


def categories_for(ticket=None):
    """Category options; an old category already on the ticket stays selectable."""
    if ticket is not None and ticket.category and ticket.category not in CATEGORIES:
        return [*CATEGORIES, ticket.category]
    return CATEGORIES


def validate_request_form(form, with_requisition=True, ticket=None):
    """
    The New Request / Edit form. With with_requisition=False (the vendor and
    items are locked after approval) only the general fields are read.
    Returns (data, items, errors); items is a list of dicts with keys
    id (None for a new row), description, quantity, unit.
    """
    from .models import Project

    errors = {}
    data = {
        "title": _text(form, "title", "Title", MAX_ITEM, errors),
        "owner": _text(form, "owner", "Request owner", MAX_SHORT, errors),
        "category": _choice(form, "category", "Category", categories_for(ticket), errors),
        "procurement_type": _choice(
            form, "procurement_type", "Procurement type", PROCUREMENT_TYPES, errors
        ),
        "priority": _choice(form, "priority", "Priority", PRIORITIES, errors),
        "required_by": _date(form, "required_by", "Required by date", errors),
        "description": _text(form, "description", "Description", MAX_DESCRIPTION, errors),
        "project_id": None,
    }

    project_id = form.get("project_id") or ""
    if project_id:
        project = db.session.get(Project, int(project_id)) if project_id.isdigit() else None
        if project is None:
            errors["project_id"] = "Choose a valid project."
        else:
            data["project_id"] = project.id

    items = None
    if with_requisition:
        if form.get("recommended_vendor_id") == NEW_VENDOR:
            # "+ Add a new vendor": fields new_vendor_<name> hold a short vendor form
            data["recommended_vendor_id"] = None
            data["new_vendor"] = _new_vendor(form, errors)
        else:
            data["recommended_vendor_id"] = _vendor_id(form, "recommended_vendor_id", errors)
        items = _item_rows(form, errors)
    return data, items, errors


NEW_VENDOR = "new"
NEW_VENDOR_PREFIX = "new_vendor_"


def _new_vendor(form, errors):
    """A vendor added from the request form; errors are keyed new_vendor_<field>."""
    fields = {
        key[len(NEW_VENDOR_PREFIX) :]: value
        for key, value in form.items()
        if key.startswith(NEW_VENDOR_PREFIX)
    }
    fields.setdefault("default_currency", BASE_CURRENCY)
    vendor_data, vendor_errors = validate_vendor_form(fields)
    for field, message in vendor_errors.items():
        errors[NEW_VENDOR_PREFIX + field] = message
    return vendor_data


def _vendor_id(form, name, errors):
    """Optional vendor choice; must be an existing vendor."""
    from .models import Vendor

    value = form.get(name) or ""
    if not value:
        return None
    vendor = db.session.get(Vendor, int(value)) if value.isdigit() else None
    if vendor is None:
        errors[name] = "Choose a valid vendor."
        return None
    return vendor.id


def _item_rows(form, errors):
    """
    Item rows come as parallel lists: item_id, item_description, item_quantity,
    item_unit. Completely empty rows are skipped. Errors are keyed
    "item-<row>-<field>" (row counts from 0), plus "items" for the whole table.
    """
    ids = form.getlist("item_id")
    descriptions = form.getlist("item_description")
    quantities = form.getlist("item_quantity")
    units = form.getlist("item_unit")
    count = max(len(ids), len(descriptions), len(quantities), len(units))

    def at(values, i):
        return (values[i] if i < len(values) else "").strip()

    rows = []
    for i in range(count):
        row_form = {
            "description": at(descriptions, i),
            "quantity": at(quantities, i),
            "unit": at(units, i) or "Nos",
        }
        if not row_form["description"] and not row_form["quantity"]:
            continue
        row_errors = {}
        description = _text(
            row_form, "description", "Item description", MAX_ITEM, row_errors, required=True
        )
        quantity = _int(row_form, "quantity", "Quantity", row_errors, 1, MAX_QUANTITY)
        unit = _choice(row_form, "unit", "Unit", UNITS, row_errors)
        for field, message in row_errors.items():
            errors[f"item-{i}-{field}"] = message
        item_id = at(ids, i)
        rows.append(
            {
                "id": int(item_id) if item_id.isdigit() else None,
                "description": description,
                "quantity": quantity,
                "unit": unit,
            }
        )

    if not rows:
        errors["items"] = "Add at least one item."
    elif len(rows) > MAX_ITEMS:
        errors["items"] = f"A ticket can have at most {MAX_ITEMS} items."
    return rows


def validate_tracking_form(form, include_vendor_fields=True):
    errors = {}
    data = {
        "payment_status": _choice(
            form, "payment_status", "Payment status", PAYMENT_STATUSES, errors
        ),
        "fulfilment_status": _choice(
            form, "fulfilment_status", "Fulfilment status", FULFILMENT_STATUSES, errors
        ),
        "expected_delivery": _date(form, "expected_delivery", "Expected delivery date", errors),
    }
    if include_vendor_fields:  # only for tickets without POs (before V0.6)
        data["selected_vendor"] = _text(form, "selected_vendor", "Vendor", MAX_VENDOR, errors)
        data["po_number"] = _text(form, "po_number", "PO number", MAX_PO, errors)
    comment = _text(form, "tracking_comment", "Comment", MAX_COMMENT, errors)
    return data, comment, errors


def validate_comment(form, required):
    """Comment / reason field used by the workflow action forms."""
    errors = {}
    comment = _text(
        form, "comment", "Reason" if required else "Comment", MAX_COMMENT, errors, required=required
    )
    return comment, errors


def validate_pricing_form(form, ticket):
    """
    Estimated price and GST per item (purchase team), fields price-<item id>
    and gst-<item id>. A price may be left empty to fill in later.
    Returns ({item: (price, gst)}, errors).
    """
    errors = {}
    prices = {}
    for item in ticket.items:
        price_field, gst_field = f"price-{item.id}", f"gst-{item.id}"
        if not (form.get(price_field) or "").strip():
            prices[item] = (None, None)
            continue
        price = _decimal(
            form, price_field, f"Price of item {item.position}", errors, "0", "1000000000", 2
        )
        gst = _decimal(form, gst_field, f"GST of item {item.position}", errors, "0", "40", 2)
        prices[item] = (price, gst)
    return prices, errors


# ---- projects ----


def validate_project_form(form, existing=None):
    from .models import Project

    errors = {}
    name = _text(form, "name", "Project name", MAX_SHORT, errors, required=True)
    if name and "name" not in errors:
        clash = db.session.scalar(select(Project).where(func.lower(Project.name) == name.lower()))
        if clash is not None and clash is not existing:
            errors["name"] = "A project with this name already exists."
    data = {
        "name": name,
        "description": _text(form, "description", "Description", MAX_DESCRIPTION, errors),
    }
    return data, errors


# ---- vendors ----


def validate_vendor_form(form, existing=None):
    from .models import Vendor

    errors = {}
    name = _text(form, "name", "Vendor name", MAX_VENDOR, errors, required=True)
    if name and "name" not in errors:
        clash = db.session.scalar(select(Vendor).where(func.lower(Vendor.name) == name.lower()))
        if clash is not None and clash is not existing:
            errors["name"] = "A vendor with this name already exists."

    country = _text(form, "country", "Country", MAX_SHORT, errors) or INDIA
    in_india = country.lower() == INDIA.lower()
    if in_india:
        country = INDIA
        state_code, gstin = _gstin_and_state(form, errors, state_required=True)
    else:
        state_code, gstin = None, None

    data = {
        "name": name,
        "contact_person": _text(form, "contact_person", "Contact person", MAX_SHORT, errors),
        "email": _email(form, "email", errors),
        "phone": _text(form, "phone", "Phone", 50, errors),
        "address": _text(form, "address", "Address", MAX_DESCRIPTION, errors),
        "city": _text(form, "city", "City", MAX_SHORT, errors),
        "country": country,
        "state_code": state_code,
        "gstin": gstin or None,
        "pan": _pan(form, errors, gstin) or None,
        "payment_terms": _text(form, "payment_terms", "Payment terms", 200, errors),
        "default_currency": _choice(form, "default_currency", "Currency", CURRENCIES, errors),
        "notes": _text(form, "notes", "Notes", MAX_DESCRIPTION, errors),
    }
    return data, errors


# ---- company settings ----


def validate_settings_form(form):
    errors = {}
    state_code, gstin = _gstin_and_state(form, errors, state_required=True)
    prefix = _text(form, "po_prefix", "PO number prefix", 30, errors, required=True)
    if prefix and not re.match(r"^[A-Za-z0-9/_-]+$", prefix):
        errors["po_prefix"] = "Use only letters, numbers, / - and _."
    data = {
        "name": _text(form, "name", "Company name", MAX_VENDOR, errors, required=True),
        "address": _text(form, "address", "Address", MAX_DESCRIPTION, errors, required=True),
        "state_code": state_code,
        "gstin": gstin or None,
        "pan": _pan(form, errors, gstin) or None,
        "email": _email(form, "email", errors),
        "phone": _text(form, "phone", "Phone", 50, errors),
        "delivery_address": _text(
            form, "delivery_address", "Delivery address", MAX_DESCRIPTION, errors
        ),
        "plant": _text(form, "plant", "Plant", MAX_VENDOR, errors),
        "po_prefix": prefix,
        "default_terms": _text(form, "default_terms", "Terms", MAX_DESCRIPTION, errors),
    }
    return data, errors


# ---- purchase orders ----


def validate_po_header_form(form):
    from .models import Vendor

    errors = {}
    vendor = None
    vendor_id = form.get("vendor_id") or ""
    if vendor_id.isdigit():
        vendor = db.session.get(Vendor, int(vendor_id))
    if vendor is None:
        errors["vendor_id"] = "Choose a vendor."

    currency = _choice(form, "currency", "Currency", CURRENCIES, errors)
    exchange_rate = Decimal(1)
    if currency and currency != BASE_CURRENCY:
        exchange_rate = _decimal(
            form, "exchange_rate", "Exchange rate", errors, "0.0001", "100000", 4
        )

    data = {
        "vendor": vendor,
        "currency": currency,
        "exchange_rate": exchange_rate,
        "delivery_date": _date(form, "delivery_date", "Delivery date", errors),
        "delivery_address": _text(
            form, "delivery_address", "Delivery address", MAX_DESCRIPTION, errors
        ),
        "payment_terms": _text(form, "payment_terms", "Payment terms", 200, errors),
        "terms": _text(form, "terms", "Terms and conditions", MAX_DESCRIPTION, errors),
        "notes": _text(form, "notes", "Notes", MAX_DESCRIPTION, errors),
    }
    return data, errors


def validate_po_line_form(form):
    errors = {}
    hsn = (form.get("hsn_sac") or "").strip()
    if hsn and not HSN_RE.match(hsn):
        errors["hsn_sac"] = "HSN/SAC code must be 4 to 8 digits."
    data = {
        "description": _text(form, "description", "Description", MAX_ITEM, errors),
        "hsn_sac": hsn or None,
        "quantity": _int(form, "quantity", "Quantity", errors, 1, MAX_QUANTITY),
        "unit_price": _decimal(form, "unit_price", "Unit price", errors, "0", "1000000000", 2),
        "gst_rate": _decimal(form, "gst_rate", "GST rate", errors, "0", "40", 2),
    }
    return data, errors
