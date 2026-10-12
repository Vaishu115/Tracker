"""
Fixed lists used across the app: workflow stages, dropdown options and
event types. Change them here and every page picks up the change.
"""

PROCUREMENT_FLOW = [
    "Requirement Raised",
    "Specification Finalized",
    "Vendor Identification",
    "RFQ Sent",
    "Quotation Received",
    "Quotation Comparison",
    "Vendor Selected",
    "Approval Pending",
    "PO Released",
    "Payment Processing",
    "Vendor Lead Time",
    "In Transit",
    "Received",
    "Inspection",
    "Closed",
]

FINAL_STAGE = PROCUREMENT_FLOW[-1]

# Display wording can change without changing saved workflow values.
STAGE_DISPLAY_NAMES = {"Received": "Received/Inverding"}

# Only the eligible approver for the ticket amount can move past this stage.
APPROVAL_STAGE = "Approval Pending"

# Stages from PO release onwards, where vendor / PO / delivery details apply.
TRACKING_STAGES = PROCUREMENT_FLOW[PROCUREMENT_FLOW.index("PO Released") :]

# Tickets created before V0.6.2 may have older categories (Sensor, Compute, ...);
# they keep them and can still be edited (see categories_for in forms.py).
CATEGORIES = [
    "Electronics",
    "Mechanical",
    "Software",
    "Service",
]

PROCUREMENT_TYPES = [
    "New Purchase",
    "Replacement",
    "Trial / Development",
    "Repeat Purchase",
    "Service",
    "Other",
]

PRIORITIES = ["Low", "Normal", "High", "Critical"]

PAYMENT_STATUSES = ["Pending", "Partial", "Completed", "On Hold"]

FULFILMENT_STATUSES = [
    "Not Ordered",
    "Order Confirmed",
    "Vendor Lead Time",
    "Ready for Dispatch",
    "In Transit",
    "Delivered",
    "Delayed",
]

# Case state is separate from the workflow stage, so a ticket put on hold
# or cancelled still remembers which stage it was at.
STATE_ACTIVE = "Active"
STATE_ON_HOLD = "On Hold"
STATE_CANCELLED = "Cancelled"

EVENT_LABELS = {
    "STATUS_CHANGE": "Stage advanced",
    "APPROVED": "Approved",
    "REJECTED": "Rejected. Sent back for rework",
    "SENT_BACK": "Sent back",
    "ON_HOLD": "Put on hold",
    "RESUMED": "Resumed",
    "CANCELLED": "Cancelled",
    "REOPENED": "Reopened",
    "EDITED": "Details edited",
    "TRACKING_UPDATE": "Tracking updated",
    "ARCHIVED": "Archived",
    "RESTORED": "Restored",
    "MIGRATION_NOTE": "Data migration note",
    "REQUISITION": "Requisition updated",
    "PO_ISSUED": "PO issued",
    "PO_CANCELLED": "PO cancelled",
}

UNITS = [
    "Nos",
    "Set",
    "Pair",
    "Box",
    "Pack",
    "Roll",
    "Sheet",
    "Lot",
    "m",
    "cm",
    "mm",
    "kg",
    "g",
    "L",
    "mL",
    "Hour",
    "Day",
]

# ---- purchase orders ----

PO_DRAFT = "Draft"
PO_ISSUED = "Issued"
PO_CANCELLED = "Cancelled"

CURRENCIES = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "JPY ",
    "CNY": "CNY ",
    "SGD": "SGD ",
    "AED": "AED ",
}
BASE_CURRENCY = "INR"

# GST rates offered as suggestions on PO lines (any rate from 0 to 40 is accepted).
GST_RATES = ["0", "0.25", "3", "5", "12", "18", "28", "40"]

# How GST applies to a PO, decided by where the vendor is:
TAX_INTRA = "intra"  # vendor in ERIC's state: CGST + SGST (half each)
TAX_INTER = "inter"  # vendor in another Indian state: IGST
TAX_IMPORT = "import"  # vendor outside India: no GST on the PO (customs duty is separate)

TAX_LABELS = {
    TAX_INTRA: "CGST + SGST",
    TAX_INTER: "IGST",
    TAX_IMPORT: "Import (no GST on PO)",
}

# GST state codes: the first two digits of a GSTIN.
INDIAN_STATES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
}
INDIA = "India"

# Field limits, shared by validation and the database schema.
MAX_ITEM = 200
MAX_SHORT = 100
MAX_VENDOR = 200
MAX_PO = 50
MAX_DESCRIPTION = 5000
MAX_COMMENT = 1000
MAX_QUANTITY = 1_000_000
