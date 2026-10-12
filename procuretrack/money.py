"""
Money: amounts, GST and formatting. Pure functions, no database.

All amounts are Decimal (never float) and rounded half-up to 2 decimals
per line, as on a printed PO.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .constants import BASE_CURRENCY, CURRENCIES, TAX_INTER, TAX_INTRA

CENT = Decimal("0.01")
ZERO = Decimal("0")


def round2(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class LineAmounts:
    taxable: Decimal  # quantity x unit price
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def tax(self):
        return self.cgst + self.sgst + self.igst

    @property
    def total(self):
        return self.taxable + self.tax


def line_amounts(quantity, unit_price, gst_rate, tax_type):
    """Amounts for one PO line. tax_type is TAX_INTRA / TAX_INTER / TAX_IMPORT."""
    taxable = round2(Decimal(quantity) * unit_price)
    if tax_type == TAX_INTRA:
        half = round2(taxable * gst_rate / 2 / 100)
        return LineAmounts(taxable, half, half, ZERO)
    if tax_type == TAX_INTER:
        return LineAmounts(taxable, ZERO, ZERO, round2(taxable * gst_rate / 100))
    return LineAmounts(taxable, ZERO, ZERO, ZERO)


@dataclass
class Totals:
    taxable: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    igst: Decimal = ZERO

    @property
    def tax(self):
        return self.cgst + self.sgst + self.igst

    @property
    def total(self):
        return self.taxable + self.tax

    def add(self, line):
        self.taxable += line.taxable
        self.cgst += line.cgst
        self.sgst += line.sgst
        self.igst += line.igst


# ---- formatting ----


def group_indian(integer_digits):
    """'12345678' -> '1,23,45,678' (lakh / crore grouping)."""
    if len(integer_digits) <= 3:
        return integer_digits
    head, tail = integer_digits[:-3], integer_digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs) + "," + tail


def format_amount(value, currency=BASE_CURRENCY, symbol=True):
    """₹1,23,456.00 for INR (Indian grouping); $1,234.56 / JPY 1,234.00 otherwise."""
    if value is None:
        return "-"
    value = round2(value)
    sign = "-" if value < 0 else ""
    integer, fraction = f"{abs(value):.2f}".split(".")
    grouped = group_indian(integer) if currency == BASE_CURRENCY else f"{int(integer):,}"
    prefix = CURRENCIES.get(currency, currency + " ") if symbol else ""
    return f"{sign}{prefix}{grouped}.{fraction}"


# ---- amount in words ----

_ONES = (
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
    "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen"
).split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_thousand(n):
    words = []
    if n >= 100:
        words += [_ONES[n // 100], "Hundred"]
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10])
        n %= 10
    if n:
        words.append(_ONES[n])
    return words


def _indian_words(n):
    if n == 0:
        return ["Zero"]
    words = []
    for size, name in ((10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand")):
        if n >= size:
            words += (
                _indian_words(n // size) if n // size >= 100 else _below_thousand(n // size)
            ) + [name]
            n %= size
    return words + _below_thousand(n)


def _international_words(n):
    if n == 0:
        return ["Zero"]
    words = []
    for size, name in ((10**9, "Billion"), (10**6, "Million"), (10**3, "Thousand")):
        if n >= size:
            words += _below_thousand(n // size) + [name]
            n %= size
    return words + _below_thousand(n)


_CURRENCY_WORDS = {
    "INR": ("Rupees", "Paise"),
    "USD": ("US Dollars", "Cents"),
    "EUR": ("Euros", "Cents"),
    "GBP": ("Pounds", "Pence"),
    "SGD": ("Singapore Dollars", "Cents"),
    "AED": ("Dirhams", "Fils"),
}


def amount_in_words(value, currency=BASE_CURRENCY):
    """'Rupees One Lakh Twenty Thousand and Fifty Paise Only' (as printed on POs)."""
    value = round2(value)
    whole = int(value)
    fraction = int((value - whole) * 100)
    major, minor = _CURRENCY_WORDS.get(currency, (currency, "Cents"))
    to_words = _indian_words if currency == BASE_CURRENCY else _international_words

    text = f"{major} {' '.join(to_words(whole))}"
    if fraction:
        text += f" and {' '.join(_below_thousand(fraction))} {minor}"
    return text + " Only"
