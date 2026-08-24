"""
Extracts structured invoice data from PDF text + tables.
Supports varied Indian GST invoice formats (digital + OCR output).

Design goals:
  - High accuracy via cascading regex patterns
  - Math validation to catch extraction errors before user saves
  - No ML models — pure regex + heuristics (fast on i3, zero VRAM)
"""

import re
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  REGEX PATTERNS
# ══════════════════════════════════════════════════════════════════════════════

# Indian GSTIN: 2-digit state + 5-char PAN prefix + 4 digits + 1 char + 1 char + Z + 1 char
GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")

# Invoice number — try most specific patterns first
INV_NO_RES = [
    re.compile(r"INVOICE\s*NO\.?\s*[:\.]?\s*([A-Z0-9][A-Z0-9\-/]*\d+)", re.I),
    re.compile(r"Invoice\s*No\.?\s*[:\.]?\s*([A-Z0-9][A-Z0-9\-/]*\d+)", re.I),
    re.compile(r"Bill\s*No\.?\s*[:\.\-]?\s*([A-Z0-9\-/]+\d+)", re.I),
    # Saurashtra: "Invoice No. Date\nSS/446/25-26 03/08/2025"
    re.compile(r"Invoice\s*No\.\s+Date\s*\n\s*([A-Z0-9][A-Z0-9/\-]*\d+)", re.I),
    # MK/Tally-Dated: "Invoice No. Dated\n<addr> 002/25-26 01-09-2025"
    re.compile(r"Invoice\s*No\.?\s+Dated?\s*\n[^\n]*?([A-Z0-9][A-Z0-9/\-]*\d+)\s+\d{1,2}[/\-]\d{1,2}[/\-]\d{4}", re.I),
    # Tally-style: number immediately before a Mon-format date
    re.compile(r"\b(\d{1,6})\s+\d{1,2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2,4}\b", re.I),
]

# Invoice date — ordered from most specific to least specific
INV_DATE_RES = [
    re.compile(r"Invoice\s*Date\s*[:\-]?\s*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})", re.I),
    # Green Electricals: "Inv. Date: 24/01/2025"
    re.compile(r"Inv\.?\s*Date\s*[:\-]?\s*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})", re.I),
    # Saurashtra: "Invoice No. Date\nSS/446/25-26 03/08/2025" — date on same line as invoice no
    re.compile(r"Invoice\s*No\.\s+Date\s*\n\s*[A-Z0-9/\-]+\s+(\d{1,2}/\d{1,2}/\d{4})", re.I),
    # MK/Tally-Dated: "Invoice No. Dated\n<addr> 002/25-26 01-09-2025"
    re.compile(r"Invoice\s*No\.?\s+Dated?\s*\n[^\n]*?[A-Z0-9/\-]+\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})", re.I),
    re.compile(r"(?:^|\s)Date\s*[:\-]\s*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})", re.I | re.M),
    re.compile(r"Date\s*[:\-]\s*(\d{1,2}/\d{1,2}/\d{4})", re.I),
    # Tally: "Invoice No. Dated\n<address>, 74 16-Apr-25"
    re.compile(r"Invoice\s*No\.\s+Dated\s*\n[^\n]*?(\d{1,2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*-\d{2,4})", re.I),
    re.compile(r"Dated?\s+[\d,\w\s.-]*?(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})", re.I),
    # Last resort: Mon-format date, excluding "Printed on" header lines
    re.compile(r"(?<!Printed on\s)(?<!printed on\s)\b(\d{1,2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2,4})\b", re.I),
]

# Totals — multiple fallback patterns for each field
TAXABLE_RES = [
    re.compile(r"Taxable\s*(?:Amount|Value)\s*[:\|=]?\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"Sub\s*[-\s]?[Tt]otal\s*[:\|]?\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"SUB\s*TOTAL\s+([\d,]+\.\d{2})", re.I),
    # Shiv Electricals summary: "24,301.00  9%  2,187.09  9%  2,187.09  4,374.18"
    re.compile(r"Taxable\s+Value\s*\n\s*([\d,]+\.\d{2})", re.I),
]

SGST_TOTAL_RES = [
    re.compile(r"Total\s*SGST\s*[:\|]?\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"SGST\s*(?:PAYBLE|PAYABLE)\s*[:\|]?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"SGST\s*PAYBL[EY]\s+([\d,]+\.\d{2})", re.I),
    # Shiv Electricals: "Output SGST@ 9%  9 %  2,187.09"
    re.compile(r"Output\s*SGST@?\s*[\d.]+\s*%\s+[\d.]+\s*%\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"Output\s*SGST@\s*\d+%\s+\d+\s*%\s*([\d,]+\.\d{2})", re.I),
    # Green Electricals: "SGST : 9873.81"
    re.compile(r"\bSGST\s*:\s*([\d,]+\.\d{2})", re.I),
    # MK Power System: "SGST 9 % 1,000.24"
    re.compile(r"^SGST\s+\d+\s*%\s+([\d,]+\.\d{2})", re.I | re.M),
    # Saurashtra: standalone "SGST 698.01" at start of line
    re.compile(r"^SGST\s+([\d,]+\.\d{2})", re.I | re.M),
]

CGST_TOTAL_RES = [
    re.compile(r"Total\s*CGST\s*[:\|]?\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"CGST\s*(?:PAYBLE|PAYABLE)\s*[:\|]?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"CGST\s*PAYBL[EY]\s+([\d,]+\.\d{2})", re.I),
    re.compile(r"Output\s*CGST@?\s*[\d.]+\s*%\s+[\d.]+\s*%\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"Output\s*CGST@\s*\d+%\s+\d+\s*%\s*([\d,]+\.\d{2})", re.I),
    # Green Electricals: "CGST : 9873.81"
    re.compile(r"\bCGST\s*:\s*([\d,]+\.\d{2})", re.I),
    # MK Power System: "CGST 9 % 1,000.24"
    re.compile(r"^CGST\s+\d+\s*%\s+([\d,]+\.\d{2})", re.I | re.M),
    # Saurashtra: standalone "CGST 698.01" (own line or end-of-line)
    re.compile(r"^CGST\s+([\d,]+\.\d{2})", re.I | re.M),
    re.compile(r"\bCGST\s+([\d,]+\.\d{2})\s*$", re.I | re.M),
]

IGST_TOTAL_RES = [
    re.compile(r"Total\s*IGST\s*[:\|]?\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"IGST\s*(?:PAYBLE|PAYABLE)\s*[:\|]?\s*([\d,]+\.\d{2})", re.I),
]

INVOICE_TOTAL_RES = [
    re.compile(r"Invoice\s*Total\s*[:\|]?\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"Grand\s*Total\s*[:\|]?\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})", re.I),
    re.compile(r"GRAND\s*TOTAL\s+([\d,]+\.\d{2})", re.I),
    # Green Electricals: "Invoice Amt: 129457.00"
    re.compile(r"Invoice\s*Amt\s*[:\-]?\s*([\d,]+\.\d{2})", re.I),
    # Shiv Electricals: "Total ₹ 28,675.00" or "Total (cid:299) 28,675.00"
    re.compile(r"\bTotal\s+(?:₹|(?:\(cid:\d+\)))\s*([\d,]+\.\d{2})", re.I),
    # Kiran Crystalflow E&OE line: last Rs. amount = invoice total
    re.compile(r"E\.?\s*&\s*O\.?E\.?\s*Total[^\n]*?Rs\.([\d,]+\.\d{2})\s*$", re.I | re.M),
    re.compile(r"E\.?\s*&\s*O\.?E\.?\s*Total[^\n]*?Rs\.([\d,]+\.\d{2})", re.I),
    # MK Power System: "Total 13,114.26" (plain Total at line start)
    re.compile(r"^Total\s+([\d,]+\.\d{2})\s*$", re.I | re.M),
]

ROUND_OFF_RES = [
    re.compile(r"Round\s*[-\s]?[Oo]ff\s*[:\|]?\s*\(?\-?([\d,]+\.\d{2})\)?", re.I),
    re.compile(r"Less\s*:?\s*Round\s*[Oo]ff\.?\s*\(?\-?([\d,]+\.\d{2})\)?", re.I),
    re.compile(r"Round\s*[Oo]ff\s*Amount\s*[:\|]?\s*([\d,]+\.\d{2})", re.I),
    # Shiv Electricals: "(-)0.18"
    re.compile(r"\(-\)\s*([\d,]+\.\d{2})", re.I),
]

# Buyer name patterns
# NOTE: the naive "Buyer (Bill to)\n<next line>" pattern was removed — Tally PDFs
# merge the right-column headers ("Dispatched through Destination", "Terms of
# Delivery", "Bill of Lading...") onto the lines right after the label, so a
# blind next-line grab was matching that noise instead of the name. See
# _extract_buyer_name_after_label(), which skips those lines and is tried first.
BUYER_NAME_RES = [
    re.compile(r"Customer\s*Name\s*[=:\-]\s*(.{3,60})", re.I),
    # Green Electricals 2-column layout: "SELLER NAME [BRANCHCODE] BUYER NAME\n"
    # — must come before the generic "Bill To:\n<line>" pattern below, which
    # would otherwise grab this whole merged seller+buyer line as one name.
    re.compile(r"\[[\w\s]+\]\s+([A-Z][A-Za-z][A-Za-z0-9 &.,'\-]{2,50})\s*\n"),
    # Sangita / formal: "Bill To Party,\nD N CORPORATION"
    re.compile(r"Bill\s+[Tt]o\s+[Pp]arty[^\n]*\n\s*([A-Z][A-Za-z0-9 &.,'\-]{2,60})", re.I),
    # "BILL TO:\n<name>" or "Buyer\n<name>" — allow optional colon/dash before newline
    re.compile(r"(?:Buyer|Bill\s*[Tt]o)\s*[:\-]?\s*\n\s*(.{3,60})", re.I | re.M),
    re.compile(r"To[,:]?\s*\n\s*(.{3,60})", re.I | re.M),
    # MK Power System: buyer identified by "GST TIN-" label preceding address block
    re.compile(r"^([A-Z][A-Z &.']{3,50})\s*\n[^\n]*GST\s*TIN", re.M),
    # Saurashtra / Shivam Steel: "M/s. : D N CORPORATION" or "M/s : GULSHAN PLUMBER"
    re.compile(r"M/s\.?\s*[:\-]?\s*(.+?)(?=\s+Invoice|\s+GSTIN|\s+Ph\b|\s*$)", re.I | re.M),
]

SELLER_NAME_RES = [
    # "For GREEN ELECTRICALS PVT LTD\n" — all-caps, stops at newline (no \s in class)
    re.compile(r"for\s+([A-Z][A-Z &.']{2,50})\s*\n", re.M),
    # MK style: "TAX INVOICE\nM K POWER SYSTEM Invoice No. Dated"
    re.compile(r"TAX\s+INVOICE\s*\n\s*([A-Z][A-Za-z0-9 &.']+?)\s+Invoice\s+No\.", re.I),
    # Bank a/c holder name (often the seller, used in OCR'd bills)
    re.compile(r"A/?c\s*Holder.{0,15}Name\s*[:=]?\s*([A-Z][A-Z0-9 &.']{2,50})", re.I),
    # OCR'd footer: "For SHIVSHAKTI" or "D MAN - For SHIVSHAKTI"
    re.compile(r"\bFor\s+([A-Z][A-Z0-9 &.']{2,40})\b", re.M),
]

# ══════════════════════════════════════════════════════════════════════════════
#  COLUMN HEADER ALIASES (for adaptive table parsing)
# ══════════════════════════════════════════════════════════════════════════════

_COL_MAP = {
    "sr":          ["sr", "sl", "sno", "s.no", "#", "no.", "sl no", "sl.no"],
    "description": ["description", "desc", "product", "goods", "item",
                    "particulars", "of goods", "services"],
    "hsn":         ["hsn", "sac", "hsn/sac", "hsnsac", "hsn code", "hsncode", "hsn sac"],
    "qty":         ["qty", "quantity", "qnty", "qntty"],
    "unit":        ["unit", "uom", "per", "uqc"],
    "rate":        ["rate", "price", "unit rate", "unit price"],
    "mrp":         ["mrp"],
    "amount":      ["amount", "amt", "taxable value", "taxable amt",
                    "basic amount", "taxable"],
    "sgst":        ["sgst"],
    "cgst":        ["cgst"],
    "igst":        ["igst"],
    "total":       ["total", "net total", "item total", "net amount", "net amt"],
    "disc":        ["disc", "discount", "dis", "disc%", "dis%"],
    "sch":         ["sch", "scheme", "sch%"],
    "free":        ["free"],
}

# Descriptions that indicate a summary/total row — skip these
_SKIP_DESC_RE = re.compile(
    r"^(e\.?\s*&\s*o\.?e|total|sub.?total|grand\s*total|taxable|sgst|cgst|igst"
    r"|round|amount\s*chargeable|tax\s*amount|declaration|remarks|output\s*[sc]gst)",
    re.I
)

# Unit labels found in Tally-style PDFs
_UNITS_PAT = r"(?:NOS?\.?|PCS?\.?|KGS?\.?|KG\.?|LTR?\.?|MTR?S?\.?|SQM\.?|ROLLS?\.?|BOX\.?|SET\.?|PAIR\.?|METER\.?|MTRS?\.?)"

# Compiled unit pattern for text-extraction parsers (used to grab unit from Tally lines)
_UNIT_RE = re.compile(
    r"\b(NOS?\.?|PCS?\.?|KGS?\.?|KG\.?|LTRS?\.?|LTR?\.?|MTRS?\.?|MTR?\.?|"
    r"SQM\.?|ROLLS?\.?|BOX\.?|SETS?\.?|PAIRS?\.?|METERS?\.?|FT\.?|FEET\.?|"
    r"BAGS?\.?|BDLS?\.?)\b",
    re.I
)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def clean_amount(s) -> Optional[float]:
    """
    Parse Indian invoice amount strings to float.

    Handles: 'Rs.1,586.44' / '₹20,800.00' / '(0.18)' / '1,250.00' / 'Rs.450'
    IMPORTANT: We remove only currency prefixes, NOT the decimal point.
    """
    if s is None:
        return None
    s = str(s).strip()
    # Remove currency prefix (Rs. / Rs / ₹ / INR) — but leave the decimal!
    s = re.sub(r"(?:Rs\.?|₹|INR)\s*", "", s, flags=re.I)
    # Remove commas and leading/trailing spaces
    s = s.replace(",", "").strip()
    # Parentheses = negative: (0.18) → -0.18
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _first_match(patterns: list, text: str) -> Optional[str]:
    """Return first capturing group from the first matching pattern."""
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


def _map_col(header: str) -> Optional[str]:
    """Map a raw column header string to a standard field name."""
    if not header:
        return None
    h = str(header).lower().strip().replace("\n", " ")
    for field, aliases in _COL_MAP.items():
        if any(alias in h for alias in aliases):
            return field
    return None


def _parse_gst_cell(cell: str):
    """
    Handle cells like '1586.44\n@9%' or '202.50\n@9%' or just '57.24'.
    Returns (amount: float|None, rate: float|None).
    """
    if not cell:
        return None, None
    cell = str(cell).strip()
    rate_m = re.search(r"@\s*(\d+(?:\.\d+)?)\s*%", cell)
    rate = float(rate_m.group(1)) if rate_m else None
    amt_str = re.sub(r"@\s*[\d.]+\s*%", "", cell).strip()
    return clean_amount(amt_str), rate


def _cell(row, col_map: dict, field: str) -> str:
    """Safely get a cell value from a table row using a column map."""
    idx = col_map.get(field)
    if idx is not None and idx < len(row):
        return str(row[idx]) if row[idx] is not None else ""
    return ""


def _extract_round_off(text: str) -> float:
    """
    Extract round-off amount WITH correct sign.
    Tally format: "(-)0.18" means -0.18 (a deduction).
    """
    # Pattern capturing the (-) negative indicator explicitly
    m = re.search(
        r"(?:Less\s*:?\s*)?Round\s*[Oo]ff\.?\s*\([-–]\)\s*([\d,]+\.\d{2})",
        text, re.I
    )
    if m:
        return -abs(clean_amount(m.group(1)) or 0.0)

    # Saurashtra: "Round Off -0.48" (plain minus, no parentheses)
    m2 = re.search(r"Round\s*[Oo]ff\s+-\s*([\d,]+\.\d{2})", text, re.I)
    if m2:
        return -abs(clean_amount(m2.group(1)) or 0.0)

    # Kiran Crystalflow summary: "Round off Amount | 0"
    m = re.search(r"Round\s*[Oo]ff\s*Amount\s*[:\|]?\s*(\d+)", text, re.I)
    if m:
        return clean_amount(m.group(1)) or 0.0

    val = clean_amount(_first_match(ROUND_OFF_RES, text))
    return val or 0.0


def _blank_item() -> dict:
    return {
        "sr": "", "description": "", "hsn": "",
        "qty": None, "unit": "",
        "rate": None, "amount": None,
        "sgst_rate": None,  "sgst_amount": None,
        "cgst_rate": None,  "cgst_amount": None,
        "igst_rate": None,  "igst_amount": None,
        "discount": None,   "total": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  TABLE ITEM EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _is_tally_merged_table(table: list) -> bool:
    """
    Detect Tally-style tables where all item rows are merged into one cell.
    Sign: the sr column contains 2+ newline-separated standalone digits.
    """
    for row in table:
        if not row:
            continue
        for cell in row[:3]:  # Check first 3 columns
            if cell:
                lines = [l.strip() for l in str(cell).split("\n")]
                digit_count = sum(1 for l in lines if re.match(r"^\d{1,4}$", l))
                if digit_count >= 2:
                    return True
    return False


def _items_from_tables(tables: list) -> list:
    """
    Try to find and parse the items table from pdfplumber table output.
    Skips Tally-style merged tables (those are handled by text extraction).
    """
    for table in tables:
        if not table or len(table) < 2:
            continue

        # Skip Tally merged tables
        if _is_tally_merged_table(table):
            continue

        # Detect header row within first 6 rows
        header_idx = None
        col_map = {}

        for i, row in enumerate(table[:6]):
            if not row:
                continue
            mapped = {_map_col(str(c)): j for j, c in enumerate(row)
                      if c and _map_col(str(c))}
            numeric_fields = {"amount", "rate", "total", "qty"}
            if "description" in mapped and bool(mapped.keys() & numeric_fields):
                header_idx = i
                col_map = mapped
                break

        if header_idx is None:
            continue

        items = []
        for row in table[header_idx + 1:]:
            if not row or all(not c or str(c).strip() in ("", "None") for c in row):
                continue

            raw_desc = _cell(row, col_map, "description")
            if not raw_desc or _SKIP_DESC_RE.match(raw_desc.strip()):
                continue

            item = _blank_item()
            item["sr"]          = _cell(row, col_map, "sr").strip()
            item["description"] = raw_desc.strip()

            # HSN — own column or embedded in description
            hsn_cell = _cell(row, col_map, "hsn").strip()
            if hsn_cell and re.match(r"\d{4,8}", hsn_cell):
                item["hsn"] = re.search(r"\d{4,8}", hsn_cell).group()
            else:
                hsn_m = re.search(r"HSN\s*(?:Code)?\s*[:\-]?\s*(\d{4,8})", raw_desc, re.I)
                if hsn_m:
                    item["hsn"] = hsn_m.group(1)
                    item["description"] = re.sub(
                        r"\s*HSN\s*(?:Code)?\s*[:\-]?\s*\d{4,8}", "",
                        raw_desc, flags=re.I
                    ).strip()

            # Qty
            qty_raw = _cell(row, col_map, "qty")
            if qty_raw:
                m = re.search(r"([\d,]+(?:\.\d+)?)", qty_raw)
                if m:
                    item["qty"] = clean_amount(m.group(1))

            item["rate"]   = clean_amount(_cell(row, col_map, "rate"))
            item["amount"] = clean_amount(_cell(row, col_map, "amount"))

            item["sgst_amount"], item["sgst_rate"] = _parse_gst_cell(_cell(row, col_map, "sgst"))
            item["cgst_amount"], item["cgst_rate"] = _parse_gst_cell(_cell(row, col_map, "cgst"))
            item["igst_amount"], item["igst_rate"] = _parse_gst_cell(_cell(row, col_map, "igst"))

            item["total"]    = clean_amount(_cell(row, col_map, "total"))
            item["discount"] = clean_amount(_cell(row, col_map, "disc"))

            # Derive amount from rate × qty when missing
            if item["amount"] is None and item["rate"] and item["qty"]:
                item["amount"] = round(item["rate"] * item["qty"], 2)

            # Derive total from amount + taxes
            if item["total"] is None and item["amount"] is not None:
                taxes = ((item["sgst_amount"] or 0) +
                         (item["cgst_amount"] or 0) +
                         (item["igst_amount"] or 0))
                item["total"] = round(item["amount"] + taxes, 2)

            items.append(item)

        if items:
            return items

    return []


# ══════════════════════════════════════════════════════════════════════════════
#  TEXT-BASED ITEM EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

# ── Kiran Crystalflow OCR format ─────────────────────────────────────────────
# "1 KENT GRAND STAR ZWW 1.0 Rs.17,627.12 Rs.17,627.12 1586.44 1586.44 Rs.20,800.00"
# "HSN Code : 842121 @9% @9%"  (next line)
_KIRAN_ITEM_RE = re.compile(
    r"^(\d+)\s+"                                   # Sr
    r"(.+?)\s+"                                    # Description (non-greedy)
    r"([\d.]+)\s+"                                 # Qty
    r"Rs\.([\d,]+(?:\.\d+)?)\s+"                  # Rate (Rs.450 or Rs.17,627.12)
    r"Rs\.([\d,]+(?:\.\d+)?)\s+"                  # Amount
    r"([\d,]+(?:\.\d+)?)\s+"                      # SGST amount
    r"([\d,]+(?:\.\d+)?)\s+"                      # CGST amount
    r"Rs\.([\d,]+\.\d{2})",                        # Total
    re.I
)
_KIRAN_HSN_RE = re.compile(r"HSN\s*(?:Code)?\s*[:\-]?\s*(\d{4,8})\s*@(\d+)%\s*@(\d+)%", re.I)

# ── Tally format ─────────────────────────────────────────────────────────────
# "1KRISHNA MAKE TW CT'S 8504 3 NOS 1,250.00 NOS 3,750.00"
# Note: sr and description are concatenated with no space in Tally text
_TALLY_ITEM_RE = re.compile(
    r"^(\d+)\s*"                                   # Sr (immediately before desc)
    r"([A-Z][^0-9\n]*?)\s+"                        # Description (starts capital)
    r"(\d{4,8})\s+"                                # HSN (mandatory in Tally)
    r"([\d,]+(?:\.\d+)?)\s+"                       # Qty
    + _UNITS_PAT + r"\s*"                          # Unit label (optional)
    r"([\d,]+\.\d{2})\s+"                          # Rate
    + _UNITS_PAT + r"\s*"                          # per-unit label
    r"(?:[\d,]+\.\d{2}\s+)?"                       # Disc% (optional)
    r"([\d,]+\.\d{2})\s*$",                        # Amount
    re.I
)

# Fallback Tally: no HSN, simpler format
_TALLY_NOHSN_RE = re.compile(
    r"^(\d+)\s*"
    r"([A-Z][^0-9\n]*?)\s+"
    r"([\d,]+(?:\.\d+)?)\s+"                       # Qty
    + _UNITS_PAT + r"\s+"                          # Unit
    r"([\d,]+\.\d{2})\s+"                          # Rate
    + _UNITS_PAT + r"\s+"                          # per-unit
    r"([\d,]+\.\d{2})\s*$",
    re.I
)

# ── Green Electricals / cable-distributor format ─────────────────────────────
# "1 85446090-INDUSTRIAL CABLE 6 SQMM X 4 CORE 85446090 650.00 MTR 64.00 41600.00 3744.00 3744.00 0.00 49088.00"
# Desc always starts with HSN code + dash; HSN is then repeated as its own column.
_GREEN_ELEC_ITEM_RE = re.compile(
    r"^\s*(\d+)\s+"                # Sr        - group(1)
    r"(\d{4,8}-.+?)\s+"           # Desc       - group(2)
    r"(\d{4,8})\s+"               # HSN        - group(3)
    r"([\d.]+)\s+"                # Qty        - group(4)
    r"([A-Za-z]+)\s+"             # Unit       - group(5)
    r"([\d,.]+)\s+"               # Rate       - group(6)
    r"([\d,]+\.\d{2})\s+"         # Taxable    - group(7)
    r"([\d,]+\.\d{2})\s+"         # CGST       - group(8)
    r"([\d,]+\.\d{2})\s+"         # SGST       - group(9)
    r"([\d,.]+)\s+"               # IGST       - group(10)
    r"([\d,]+\.\d{2})\s*$",       # Total      - group(11)
    re.I | re.M
)

# ── MK Power System / plain-columns format ────────────────────────────────────
# "1 BRASS SC CABLE GLAND 63MM 85381090 22 344.49 NOS 7578.78"
# Columns: Sr  Desc  HSN  Qty  Rate  Unit  [Disc%]  Amount
_MK_ITEM_RE = re.compile(
    r"^\s*(\d+)\s+"                       # Sr          - group(1)
    r"(.+?)\s+"                           # Description - group(2)
    r"(\d{4,8})\s+"                       # HSN         - group(3)
    r"([\d.]+)\s+"                        # Qty         - group(4)
    r"([\d,.]+)\s+"                       # Rate        - group(5)
    r"([A-Za-z]+)\s+"                     # Unit        - group(6)
    r"(?:[\d.]+\s+)?"                     # Optional Disc%
    r"([\d,]+(?:\.\d{1,2})?)\s*$",        # Amount      - group(7)
    re.I | re.M
)

# ── OCR / simple-columns format ──────────────────────────────────────────────
# "1. HAUSER XO RT BALL PEN 9608101 2000 6.36 15009.60"
# "2. |HAU XO GEL PEN 9608 2000 6.36 15009.60"
# Format: Sr.[|] Description HSN Qty Rate Amount  (no unit, no GST cols)
_OCR_SIMPLE_ITEM_RE = re.compile(
    r"^\s*(\d+)\s*[.\)]\s*"               # Sr (mandatory . or )) - group(1)
    r"\|?\s*"                              # Optional pipe (OCR artifact)
    r"(.+?)\s+"                            # Description - group(2)
    r"(\d{4,8})\s+"                        # HSN - group(3)
    r"([\d,]+(?:\.\d+)?)\s+"              # Qty - group(4)
    r"([\d,]+\.\d{1,2})\s+"               # Rate - group(5)
    r"([\d,]+\.\d{1,2})\s*$",             # Amount - group(6)
    re.I | re.M
)

# ── Flexible / generic format (phone photo OCR fallback) ─────────────────────
# Handles formats seen in phone-photo bills where the item columns are:
#   Sr  Description  HSN  Qty  [Unit]  Rate  [per-unit]  Amount
# Examples:
#   Shivam Steel:  "1 C R SHEET (720916) 720916 37.00 61.00 2257.00"
#   Sangita Engg:  "1 TUBES SEAMLESS OF IRON 7304 59.00 Kg 100.00 5900.00"
#   Pipe House:    "1 73.0X5.16 SA106 Seamless PIPE 73041910 42.510 Mtr. 717.00 Mtr. 30,479.67"
#   Krishna Metals: "1 MS ROUND/SQUARE/HEX/FLAT 721550 48.75 KG 60.00 KG 2,925.00"
# Description may contain digits/slashes/brackets — anchored by first standalone 4-8 digit HSN.
# Unit is optional and may appear once (after qty) or twice (after qty and after rate).
_FLEXIBLE_ITEM_RE = re.compile(
    r"^\s*(\d+)\s+"                            # Sr            - group(1)
    r"(.+?)\s+"                                # Description   - group(2)  (non-greedy)
    r"\b(\d{4,8})\b\s+"                        # HSN           - group(3)
    r"([\d,]+(?:\.\d+)?)\s+"                  # Qty           - group(4)
    r"(?:([A-Za-z]+\.?)\s+)?"                 # Unit (opt.)   - group(5)
    r"([\d,]+\.\d{2})\s+"                     # Rate          - group(6)
    r"(?:[A-Za-z]+\.?\s+)?"                   # per-unit (opt, no capture)
    r"([\d,]+\.\d{2})\s*$",                   # Amount        - group(7)
    re.I | re.M
)

# ── Saurashtra Sanatary Stores format ────────────────────────────────────────
# "1 SIKKA LATEX POWER 5KG 3824 1.00 NOS 1550.00 18.0 1313.56"
# Columns: Sr  Description  HSN  Qty  Unit  Rate(MRP)  GST%  TaxableAmount
_SAURASHTRA_ITEM_RE = re.compile(
    r"^\s*(\d+)\s+"                  # Sr
    r"(.+?)\s+"                      # Description (non-greedy)
    r"(\d{4,8})\s+"                  # HSN (4–8 digits)
    r"([\d.]+)\s+"                   # Qty
    r"([A-Z]+)\s+"                   # Unit
    r"([\d,]+\.\d{2})\s+"            # Rate (MRP including tax)
    r"(\d{1,2}(?:\.\d+)?)\s+"        # GST%
    r"([\d,]+\.\d{2})\s*$",          # Taxable Amount
    re.I | re.M
)

_TEXT_STOP_RE = re.compile(
    r"^(total|sub.?total|taxable|sgst|cgst|igst|grand|round|"
    r"e\.?\s*&\s*o\.?e|output\s+[sc]gst|amount\s+chargeable|"
    r"tax\s+amount|declaration|bank|scan\s+to\s+pay|company)", re.I
)


def _items_from_text(text: str) -> list:
    """
    Line-by-line extraction for PDFs where pdfplumber gives no clean table.
    Handles multi-page bills by scanning for every table header occurrence,
    so carry-forward (B/F) pages are also processed.

    Formats:
      - Kiran Crystalflow (OCR): Rs.prefix amounts, @9% GST rates on next line
      - Tally-style (digital):   plain amounts with unit labels
      - Saurashtra Sanatary:     Sr Desc HSN Qty Unit Rate GST% TaxableAmt
    """
    lines = text.split("\n")
    items = []
    seen_sr: set = set()  # dedup across repeated pages

    # Collect all positions of table header lines (multi-page bills repeat headers)
    header_positions = []
    for idx, line in enumerate(lines):
        ll = line.lower()
        if ("description" in ll or "goods" in ll or "product" in ll) and \
           ("amount" in ll or "qty" in ll or "rate" in ll or "total" in ll):
            header_positions.append(idx)

    # If no header is found (common in heavily OCR'd bills), fall back to
    # scanning from the very start — item patterns are strict enough that
    # header/metadata lines won't false-match, and _TEXT_STOP_RE will halt
    # us at the totals block.
    if not header_positions:
        header_positions = [-1]   # sentinel: parse from line 0

    def _parse_section(start: int) -> None:
        """Process item lines starting from `start`, stopping at a totals/footer line."""
        nonlocal items
        i = start
        while i < len(lines):
            line = lines[i].strip()
            i += 1

            if not line:
                continue
            if _TEXT_STOP_RE.match(line):
                break

            # ── Kiran Crystalflow OCR format ──────────────────────────────
            m = _KIRAN_ITEM_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                item["description"] = m.group(2).strip()
                item["qty"]         = clean_amount(m.group(3))
                item["rate"]        = clean_amount(m.group(4))
                item["amount"]      = clean_amount(m.group(5))
                item["sgst_amount"] = clean_amount(m.group(6))
                item["cgst_amount"] = clean_amount(m.group(7))
                item["total"]       = clean_amount(m.group(8))
                # HSN and GST rate on the next line
                if i < len(lines):
                    next_line = lines[i].strip()
                    hsn_m = _KIRAN_HSN_RE.match(next_line)
                    if hsn_m:
                        item["hsn"]       = hsn_m.group(1)
                        item["sgst_rate"] = float(hsn_m.group(2))
                        item["cgst_rate"] = float(hsn_m.group(3))
                        i += 1
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)
                continue

            # ── Tally-style format ────────────────────────────────────────
            m = _TALLY_ITEM_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                item["description"] = m.group(2).strip().rstrip(",. ")
                item["hsn"]         = m.group(3)
                item["qty"]         = clean_amount(m.group(4))
                item["rate"]        = clean_amount(m.group(5))
                item["amount"]      = clean_amount(m.group(6))
                u = _UNIT_RE.search(line)
                item["unit"]        = u.group(1).upper() if u else ""
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)
                continue

            m = _TALLY_NOHSN_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                item["description"] = m.group(2).strip()
                item["qty"]         = clean_amount(m.group(3))
                item["rate"]        = clean_amount(m.group(4))
                item["amount"]      = clean_amount(m.group(5))
                u = _UNIT_RE.search(line)
                item["unit"]        = u.group(1).upper() if u else ""
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)
                continue

            # ── Green Electricals: Sr HSN-Desc HSN Qty Unit Rate Taxable CGST SGST IGST Total ──
            m = _GREEN_ELEC_ITEM_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                raw_desc            = m.group(2).strip()
                # Strip leading HSN prefix from description (e.g. "85446090-CABLE..." → "CABLE...")
                item["description"] = re.sub(r"^\d{4,8}-", "", raw_desc).strip().rstrip("- ")
                item["hsn"]         = m.group(3)
                item["qty"]         = clean_amount(m.group(4))
                item["unit"]        = m.group(5).upper()          # Unit - group(5)
                item["rate"]        = clean_amount(m.group(6))    # Rate - group(6)
                item["amount"]      = clean_amount(m.group(7))    # Taxable - group(7)
                item["cgst_amount"] = clean_amount(m.group(8))    # CGST - group(8)
                item["sgst_amount"] = clean_amount(m.group(9))    # SGST - group(9)
                item["igst_amount"] = clean_amount(m.group(10))   # IGST - group(10)
                item["total"]       = clean_amount(m.group(11))   # Total - group(11)
                # Derive GST rates from amounts
                if item["amount"] and item["cgst_amount"]:
                    item["cgst_rate"] = round(item["cgst_amount"] / item["amount"] * 100, 2)
                if item["amount"] and item["sgst_amount"]:
                    item["sgst_rate"] = round(item["sgst_amount"] / item["amount"] * 100, 2)
                if item["amount"] and item["igst_amount"]:
                    item["igst_rate"] = round(item["igst_amount"] / item["amount"] * 100, 2)
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)
                continue

            # ── MK Power System: Sr Desc HSN Qty Rate Unit [Disc%] Amount ──
            m = _MK_ITEM_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                item["description"] = m.group(2).strip()
                item["hsn"]         = m.group(3)
                item["qty"]         = clean_amount(m.group(4))
                item["rate"]        = clean_amount(m.group(5))   # Rate - group(5)
                item["unit"]        = m.group(6).upper()          # Unit - group(6)
                item["amount"]      = clean_amount(m.group(7))   # Amount - group(7)
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)
                continue

            # ── OCR/simple format: "1. PRODUCT HSN QTY RATE AMOUNT" ─────────
            m = _OCR_SIMPLE_ITEM_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                item["description"] = m.group(2).strip().lstrip("|").strip()
                item["hsn"]         = m.group(3)
                item["qty"]         = clean_amount(m.group(4))
                item["rate"]        = clean_amount(m.group(5))
                item["amount"]      = clean_amount(m.group(6))
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)
                continue

            # ── Flexible / phone-photo format (Shivam Steel, Sangita, Pipe House, Krishna) ──
            m = _FLEXIBLE_ITEM_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                item["description"] = m.group(2).strip()
                item["hsn"]         = m.group(3)
                item["qty"]         = clean_amount(m.group(4))
                item["unit"]        = m.group(5).upper().rstrip(".") if m.group(5) else ""
                item["rate"]        = clean_amount(m.group(6))
                item["amount"]      = clean_amount(m.group(7))
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)
                continue

            # ── Saurashtra format: Sr Desc HSN Qty Unit Rate GST% TaxableAmt ──
            m = _SAURASHTRA_ITEM_RE.match(line)
            if m:
                item = _blank_item()
                item["sr"]          = m.group(1)
                item["description"] = m.group(2).strip()
                item["hsn"]         = m.group(3)
                item["qty"]         = clean_amount(m.group(4))
                item["unit"]        = m.group(5).upper()          # Unit - group(5)
                item["rate"]        = clean_amount(m.group(6))   # MRP (inc. tax)
                gst_pct             = float(m.group(7))
                item["amount"]      = clean_amount(m.group(8))   # taxable amount
                # Derive SGST/CGST (intra-state: equal split)
                if item["amount"] and gst_pct:
                    half_rate = round(gst_pct / 2, 2)
                    half_amt  = round(item["amount"] * half_rate / 100, 2)
                    item["sgst_rate"]   = half_rate
                    item["cgst_rate"]   = half_rate
                    item["sgst_amount"] = half_amt
                    item["cgst_amount"] = half_amt
                    item["total"]       = round(item["amount"] + half_amt * 2, 2)
                if item["amount"] and item["sr"] not in seen_sr:
                    seen_sr.add(item["sr"])
                    items.append(item)

    for hdr in header_positions:
        _parse_section(hdr + 1)

    return items


# ══════════════════════════════════════════════════════════════════════════════
#  ADDRESS EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

# Tally PDFs merge left+right columns into one long line; the address follows
# one of these column-header labels.
_ADDR_TALLY_PREFIX_RE = re.compile(
    r"(?:Destination\s*,?\s+|Address\s*:\s*|Bill\s+To\s*:?\s*\n?)",
    re.I
)

# Trailing noise to strip once we've found the address: phone, email, GSTIN labels,
# and common Tally column-header words that appear after the address in merged lines.
_ADDR_TRAIL_RE = re.compile(
    r"[,.\s]+(?:Mo\.?\s*(?:No\.?)?\s*:|M\s*:|Ph(?:one)?\s*:?|Tel\s*:?|"
    r"Email(?:\s*Id\.?)?\s*:?|GSTIN\s*(?:ID\s*:?|:)?|CIN\s*:|PAN\s*:|"
    r"Buyer['’s]+\s+Order|Mode\s*/\s*Terms|NEFT\b|RTGS\b|"
    r"Terms\s+of\s+Delivery|Dispatch(?:ed)?(?:\s+through)?|Destination|"
    r"Bill\s+of\s+Lading.*?(?=$)|Reference\s*No\.?\s*&?\s*Date\.?|Other\s+References|"
    r"Consignee|Delivery\s+Note(?:\s+Date)?|Invoice\s*(?:No\.?|Dt\.?)\s*:?).*$",
    re.I
)

# Lines to skip in the fallback scan (matched against line START)
_ADDR_SKIP_RE = re.compile(
    r"^\s*(gstin|gst\s*(?:no|number|tin|id)\.?|tax\s+invoice|e-?mail|"
    r"phone|mob(?:ile)?|tel(?:ephone)?|fax|state\s+(?:name|code)|"
    r"dispatch|delivery\s+note|mode\s*/\s*terms|neft|rtgs|cin\b|pan\b|tan\b|"
    r"terms\s+of\s+delivery|bill\s+of\s+lading|reference\s*no|other\s+references|"
    r"consignee|buyer|to\s*,|"
    r"M\s*:|Mo\.?\s*(?:No\.?)?\s*:|Ph\s*:|Tel\s*:"          # phone at line start
    r")\s*",
    re.I
)


def _extract_address_before_gstin(text: str, gstin: str, after: int = 0,
                                   party_name: str = "") -> str:
    """
    Extract the postal address that appears just before `gstin` in the text.

    `after`: search for `gstin` starting from this text position onward, so
    callers can anchor to a specific section (e.g. the "Buyer (Bill to)"
    block) when the same GSTIN is printed more than once (Consignee + Buyer,
    or repeated in a bank-details footer).
    `party_name`: the already-extracted name for this party — lines that are
    just that name (no address content) are skipped instead of being pulled
    into the address.

    Strategy 1 — pincode-anchored (preferred):
      Scan backwards for a line containing an Indian 6-digit pincode.
      Strip any Tally column-header prefix ("Destination, …") and trim
      trailing phone / email / GSTIN label noise.

    Strategy 2 — fallback:
      Take the last 1-2 non-header lines immediately before the GSTIN.
    """
    if not gstin:
        return ""
    pos = text.find(gstin, after)
    if pos < 0:
        return ""

    block = text[:pos]
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    if not lines:
        return ""

    name_lc = party_name.strip().lower()

    def _is_name_line(line: str) -> bool:
        # Exact match, or the name followed by merged-column noise
        # ("J K POWER SYSTEM D i s p a t c h e d T h r o u g h Destination").
        if not name_lc:
            return False
        stripped = line.strip(".,:;- ").lower()
        return stripped == name_lc or stripped.startswith(name_lc)

    # ── Strategy 1: pincode-anchored ─────────────────────────────────────────
    window = lines[-20:]
    for rel_idx, line in reversed(list(enumerate(window))):
        if not re.search(r"\b\d{6}\b", line):
            continue

        # Tally merged lines: address comes after "Destination, " etc.
        tally_m = _ADDR_TALLY_PREFIX_RE.search(line)
        addr_part = line[tally_m.end():] if tally_m else line

        # Strip trailing phone / email / GSTIN label noise
        addr_part = _ADDR_TRAIL_RE.sub("", addr_part).strip().rstrip(".,")

        # If still too long (un-stripped garbage before house number), trim forward
        if len(addr_part) > 120:
            m = re.search(
                r"(?<![A-Z\d])(?:\d+\s*[-/]\s*\w|\b(?:SHOP|FLAT|PLOT|GF\b|FF\b|SF\b"
                r"|[A-Z]\s*-\s*\d))",
                addr_part, re.I
            )
            if m:
                addr_part = addr_part[m.start():]

        if addr_part and len(addr_part) > 5:
            # Addresses often span multiple lines (house/street on earlier lines,
            # area+pincode on this one). Prepend up to 2 preceding non-label lines.
            lead_parts = []
            for j in range(rel_idx - 1, max(rel_idx - 3, -1), -1):
                prev = window[j]
                if _ADDR_SKIP_RE.match(prev) or _is_name_line(prev):
                    break
                if re.match(r"^[A-Z][A-Z &.\'-]{4,}$", prev) and not re.search(r"\d|,", prev):
                    break  # looks like a company name line, not part of the address
                cleaned = _ADDR_TRAIL_RE.sub("", prev).strip().rstrip(".,")
                if not cleaned:
                    break
                lead_parts.insert(0, cleaned)
            return ", ".join(lead_parts + [addr_part]).strip()

    # ── Strategy 2: last non-metadata lines before GSTIN ─────────────────────
    addr_lines = []
    for line in reversed(lines[-6:]):
        if _ADDR_SKIP_RE.match(line) or _is_name_line(line):
            continue
        # Stop at what looks like a company name (all-caps letters only, no digits)
        if re.match(r"^[A-Z][A-Z &.\'-]{4,}$", line) and not re.search(r"\d|,", line):
            break
        # Strip trailing phone / email / Tally noise from this line too
        cleaned = _ADDR_TRAIL_RE.sub("", line).strip().rstrip(".,")
        if cleaned:
            addr_lines.insert(0, cleaned)
        if len(addr_lines) >= 2:
            break

    return ", ".join(addr_lines) if addr_lines else ""


# ══════════════════════════════════════════════════════════════════════════════
#  PARTY NAME — GSTIN-anchored fallback
# ══════════════════════════════════════════════════════════════════════════════

# Lines that are clearly NOT a name (labels, addresses, contact info)
_NAME_SKIP_RE = re.compile(
    r"^\s*(gstin|gst\s*(?:no|number|tin|id)\.?|tax\s+invoice|invoice|"
    r"bill\s+to|ship\s+to|consignee|buyer|seller|customer|party|"
    r"state\s+(?:name|code)|e-?mail|phone|mob(?:ile)?|tel(?:ephone)?|"
    r"fax|cin\b|pan\b|tan\b|m\s*:|mo\.?\s*no|contact|"
    r"dispatch|delivery|mode\s*/\s*terms|neft|rtgs|terms\s+of\s+delivery|"
    r"reference|other\s+ref|page|continued)\b",
    re.I
)

# Address indicators — if a line has these, it's an address, not a name
_NAME_ADDR_RE = re.compile(
    r"\b(road|street|nagar|colony|society|sector|plot|shop|flat|"
    r"opp\.?|near|behind|bldg|building|industrial|estate|gidc|"
    r"complex|tower|chambers|apartment|state|district|ta\.|tal\.|dist\.|"
    r"\d{6})\b",
    re.I
)

# Trailing Tally column-header noise that gets merged into name lines:
#   "J K POWER SYSTEM D i s p a t c h e d T h r o u g h Destination"
#                    ^^^ strip from here ^^^
_NAME_TRAIL_RE = re.compile(
    r"\s+(?:Dispatch(?:ed)?|D(?:\s+\w){3,}|"               # "Dispatched" or letter-spaced
    r"Destination|Through|T(?:\s+\w){2,}|"
    r"Mode\s*/\s*Terms|Buyer['’]?s?\s+Order|"
    r"Delivery\s+Note|Reference\s+No|Other\s+Ref|"
    r"Terms\s+of\s+Delivery).*$",
    re.I
)


# Leading label noise that sometimes gets captured along with the name itself
# (e.g. "To, PALAK TRADING" on a single line)
_NAME_LEAD_RE = re.compile(r"^(?:To|M/s\.?)\s*[,:\-]?\s*", re.I)


def _clean_name(name: str) -> str:
    """Strip leading/trailing Tally-column noise and surrounding punctuation."""
    if not name:
        return ""
    cleaned = _NAME_TRAIL_RE.sub("", name).strip()
    cleaned = _NAME_LEAD_RE.sub("", cleaned).strip()
    return cleaned.strip(".,:;-").strip()


def _extract_name_before_gstin(text: str, gstin: str, after: int = 0) -> str:
    """
    Find a company-name-like line within ~8 lines before `gstin`.
    Skips labels, addresses, and digit-heavy lines.

    `after`: search for `gstin` starting from this text position onward —
    see _extract_address_before_gstin for why (a GSTIN can repeat, e.g. once
    under "Consignee (Ship to)" and again under "Buyer (Bill to)").
    """
    if not gstin:
        return ""
    pos = text.find(gstin, after)
    if pos < 0:
        return ""
    block = text[:pos]
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    if not lines:
        return ""

    for line in reversed(lines[-8:]):
        if _NAME_SKIP_RE.match(line):
            continue
        if _NAME_ADDR_RE.search(line):
            continue
        # Skip digit-heavy lines (likely an address with pincode/phone, not a name)
        digits = sum(c.isdigit() for c in line)
        if digits > 3:
            continue
        # Must start with a letter and be a reasonable length
        if not re.match(r"^[A-Za-z]", line):
            continue
        if not (3 <= len(line) <= 120):
            continue
        # Must contain at least one letter cluster (avoid stray punctuation)
        if not re.search(r"[A-Za-z]{3,}", line):
            continue
        return _clean_name(line)

    return ""


# Words to exclude when scanning for a standalone all-caps name in the text
_STANDALONE_NAME_EXCLUDE_RE = re.compile(
    r"^(TAX\s+INVOICE|INVOICE|ORIGINAL|DUPLICATE|COPY|BILL|"
    r"GSTIN|HSN|GST|SUB\s*TOTAL|TOTAL|GRAND|TERMS|"
    r"DECLARATION|REMARKS|PAID|UNPAID|REGD\.?|REGISTERED|"
    r"AUTHORISED\s+SIGNATORY|CASH|CHEQUE|NEFT|RTGS|"
    r"VADODARA|AHMEDABAD|GUJARAT|MUMBAI|DELHI|INDIA|"
    r"CGST|SGST|IGST|UTGST|THANK\s+YOU|"
    r"ORIGINAL|DUPLICATE|FOR\s+RECIPIENT|TAX\s+INVOICE|"
    r"IRON|STEEL|PIPE|TUBES?|FITTING|PLUMBING|HARDWARE)\b",
    re.I
)


def _extract_standalone_caps_name(text: str, exclude: tuple = ()) -> str:
    """
    Last-resort name fallback: find a standalone all-caps company-name-looking
    line anywhere in the text. Useful for OCR'd bills where the seller name
    appears only in the footer (e.g., KENT ZWW INVOICE → "KIRAN CRYSTALFLOW").
    """
    exclude_lc = {e.strip().lower() for e in exclude if e}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Must be all-caps letters / spaces / dots / & / apostrophe / hyphen
        if not re.match(r"^[A-Z][A-Z &.'\-]{4,40}$", line):
            continue
        # Reject single short words ("INDIA", "VADODARA")
        if " " not in line and len(line) < 6:
            continue
        if _STANDALONE_NAME_EXCLUDE_RE.match(line):
            continue
        if line.lower() in exclude_lc:
            continue
        return line
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  GREEN ELECTRICALS — dedicated "BILL FROM: BILL TO:" 2-column parser
# ══════════════════════════════════════════════════════════════════════════════
# pdfplumber merges this vendor's left (seller) and right (buyer) columns onto
# the same text lines. Name/GSTIN extraction handle the merged lines directly;
# the buyer address needs its own reconstruction since it's split across
# several partially-merged lines with no other reliable anchor.

_BILL_FROM_TO_RE = re.compile(r"BILL\s+FROM\s*:\s*BILL\s+TO\s*:", re.I)


def _green_elec_buyer_address(text: str, seller_gstn: str, buyer_gstn: str) -> str:
    m = _BILL_FROM_TO_RE.search(text)
    if not m or not seller_gstn or not buyer_gstn:
        return ""

    buyer_pos = text.find(buyer_gstn, m.end())
    if buyer_pos < 0:
        return ""

    # The line shared by both GSTINs reads like:
    #   "...GSTIN ID: <seller_gstin> Vadodara 390023 GSTIN ID: <buyer_gstin>"
    # The text between the two GSTINs is the buyer's city + pincode.
    city_part = ""
    seller_pos = text.find(seller_gstn, m.end())
    if 0 <= seller_pos < buyer_pos:
        segment = text[seller_pos + len(seller_gstn):buyer_pos]
        city_part = re.sub(r"GSTIN\s*ID\s*:?", "", segment, flags=re.I).strip()

    # The first buyer address line is the one right after the merged
    # "SELLER [CODE] BUYER" name line.
    lines = [l.strip() for l in text[m.end():buyer_pos].split("\n") if l.strip()]
    first_line = lines[1].rstrip(",") if len(lines) > 1 else ""

    parts = [p for p in (first_line, city_part) if p]
    return ", ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
#  PARTY NAME EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

_BUYER_LABEL_RE = re.compile(r"Buyer\s*\([Bb]ill\s*[Tt]o\)", re.I)

# Right-column header text that Tally merges onto the lines right after the
# "Buyer (Bill to)" label — must be skipped when hunting for the actual name.
_RIGHT_COL_NOISE_RE = re.compile(
    r"^(dispatch(?:ed)?(?:\s+through)?|destination|terms\s+of\s+delivery|"
    r"bill\s+of\s+lading|delivery\s+note|mode\s*/\s*terms|"
    r"buyer['’]?s?\s+order|reference\s*no|other\s+references?|"
    r"dispatch\s+doc\s+no|consignee|ship\s+to)\b",
    re.I
)


def _extract_buyer_name_after_label(text: str) -> str:
    """
    Find the buyer name right after a "Buyer (Bill to)" label, skipping any
    merged right-column header lines (Tally 2-column layouts print those on
    the same lines that follow the label).
    """
    m = _BUYER_LABEL_RE.search(text)
    if not m:
        return ""
    for line in text[m.end():].split("\n"):
        line = line.strip()
        if not line:
            continue
        if _RIGHT_COL_NOISE_RE.match(line):
            continue
        if re.match(r"^[A-Z][A-Za-z0-9 &.,'\-]{2,60}$", line):
            return _clean_name(line)
        return ""  # first real line doesn't look like a name — bail to other fallbacks
    return ""


def _extract_parties(text: str, gstins: list):
    buyer  = _extract_buyer_name_after_label(text)
    seller = ""

    if not buyer:
        for pat in BUYER_NAME_RES:
            m = pat.search(text)
            if m:
                candidate = m.group(1).strip().strip(".:,")
                if len(candidate) > 2 and not re.match(r"^[\d\s,.:/-]+$", candidate):
                    buyer = candidate
                    break

    for pat in SELLER_NAME_RES:
        m = pat.search(text)
        if m:
            seller = m.group(1).strip()
            break

    # Fallback: first prominent ALL-CAPS or Title Case line before first GSTIN
    if not seller and gstins:
        first_pos = text.find(gstins[0])
        before = text[:first_pos] if first_pos > 0 else ""
        for line in before.split("\n"):
            line = line.strip()
            if (len(line) > 4
                    and not re.match(r"^(GST|TAX|INVOICE|BILL|EMAIL|PHONE|MOB|FF\s|GF\s|\d)", line, re.I)
                    and re.search(r"[A-Z]{3}", line)
                    and "@" not in line):
                seller = line
                break

    return seller, buyer


# ══════════════════════════════════════════════════════════════════════════════
#  POST-PROCESSING: FILL MISSING ITEM GST FROM INVOICE TOTALS
# ══════════════════════════════════════════════════════════════════════════════

def _fill_item_gst(items: list, sgst: float, cgst: float, igst: float):
    """
    Proportionally distribute invoice-level GST totals to items
    that have amounts but no GST breakdown.
    """
    if not items or (sgst == 0 and cgst == 0 and igst == 0):
        return

    total_amt = sum(i["amount"] or 0 for i in items)
    if total_amt == 0:
        return

    for item in items:
        amt = item.get("amount") or 0
        if amt == 0:
            continue
        ratio = amt / total_amt
        if item["sgst_amount"] is None and sgst:
            item["sgst_amount"] = round(sgst * ratio, 2)
        if item["cgst_amount"] is None and cgst:
            item["cgst_amount"] = round(cgst * ratio, 2)
        if item["igst_amount"] is None and igst:
            item["igst_amount"] = round(igst * ratio, 2)
        if item["total"] is None:
            taxes = ((item["sgst_amount"] or 0) +
                     (item["cgst_amount"] or 0) +
                     (item["igst_amount"] or 0))
            item["total"] = round(amt + taxes, 2)


# ══════════════════════════════════════════════════════════════════════════════
#  VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def _validate(taxable, sgst, cgst, igst, round_off, invoice_total, items) -> dict:
    errors = []

    # 1. If nothing extracted at all → LOW confidence
    if invoice_total == 0 and taxable == 0:
        return {
            "errors": ["No totals could be extracted — check the PDF manually."],
            "confidence": "LOW"
        }

    # 2. No items extracted but we have a total → MEDIUM confidence
    if not items and invoice_total > 0:
        errors.append("Line items could not be extracted — totals present but items not verified.")

    # 3. Sum of item amounts should equal taxable amount
    if items:
        item_sum = sum(i["amount"] or 0 for i in items)
        if item_sum > 0 and taxable > 0 and abs(item_sum - taxable) > 1.0:
            errors.append(
                f"Item amounts sum to ₹{item_sum:,.2f} but taxable shown as ₹{taxable:,.2f}"
            )

    # 4. Taxable + taxes + round_off should equal invoice total
    if invoice_total > 0 and taxable > 0:
        computed = taxable + sgst + cgst + igst + round_off
        if abs(computed - invoice_total) > 1.0:
            errors.append(
                f"Computed total ₹{computed:,.2f} ≠ Invoice total ₹{invoice_total:,.2f}"
            )

    # 5. GST cross-check on items (when rates are known)
    for i, item in enumerate(items, 1):
        amt = item.get("amount") or 0
        if amt > 0:
            for gst_type, rate_key, amt_key in [
                ("SGST", "sgst_rate", "sgst_amount"),
                ("CGST", "cgst_rate", "cgst_amount"),
                ("IGST", "igst_rate", "igst_amount"),
            ]:
                rate = item.get(rate_key)
                extracted_gst = item.get(amt_key)
                if rate and extracted_gst:
                    expected = round(amt * rate / 100, 2)
                    if abs(expected - extracted_gst) > 0.51:
                        errors.append(
                            f"Item {i}: {gst_type} expected ₹{expected:.2f} "
                            f"({rate}% of ₹{amt:.2f}), got ₹{extracted_gst:.2f}"
                        )

    confidence = "HIGH" if not errors else ("MEDIUM" if len(errors) == 1 else "LOW")
    return {"errors": errors, "confidence": confidence}


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS FOR INVOICE-LEVEL RATES
# ══════════════════════════════════════════════════════════════════════════════

def _most_common_rate(items: list, rate_key: str):
    """Return the most frequently occurring non-None rate value across items."""
    from collections import Counter
    rates = [i[rate_key] for i in items if i.get(rate_key) is not None]
    if not rates:
        return None
    return Counter(rates).most_common(1)[0][0]


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def extract_invoice_data(pdf_result: dict) -> dict:
    """
    Full extraction pipeline.
    Input:  result dict from pdf_processor.extract_from_pdf()
    Output: structured invoice dict
    """
    text   = pdf_result.get("text", "")
    tables = pdf_result.get("tables", [])

    # ── GSTINs ──────────────────────────────────────────────────────────────
    gstins      = GSTIN_RE.findall(text)
    seller_gstn = gstins[0] if len(gstins) >= 1 else ""
    # Only use second GSTIN as buyer if it differs from seller (avoids picking up
    # the seller's GSTIN when it repeats on page headers and buyer has none)
    buyer_gstn  = gstins[1] if len(gstins) >= 2 and gstins[1] != seller_gstn else ""

    # ── Party names & addresses ──────────────────────────────────────────────
    # Anchor buyer lookups to the "Buyer (Bill to)" block when present — Tally
    # bills often print the buyer's GSTIN twice (once under "Consignee (Ship
    # to)", once under "Buyer (Bill to)"); searching from here finds the
    # occurrence that's actually inside the Buyer block instead of the first
    # (wrong) one.
    buyer_label_m  = _BUYER_LABEL_RE.search(text)
    buyer_anchor   = buyer_label_m.start() if buyer_label_m else 0

    seller_name, buyer_name = _extract_parties(text, gstins)
    seller_name = _clean_name(seller_name)
    buyer_name  = _clean_name(buyer_name)

    # GSTIN-anchored fallback when the regex patterns missed the name
    if not seller_name and seller_gstn:
        seller_name = _extract_name_before_gstin(text, seller_gstn)
    if not buyer_name and buyer_gstn:
        buyer_name = _extract_name_before_gstin(text, buyer_gstn, after=buyer_anchor)

    # Last-resort fallback for seller: standalone all-caps name anywhere in text
    # (helps OCR bills where the seller name is only in the footer)
    if not seller_name:
        seller_name = _extract_standalone_caps_name(text, exclude=(buyer_name,))

    seller_addr = _extract_address_before_gstin(text, seller_gstn, party_name=seller_name)
    buyer_addr  = (_green_elec_buyer_address(text, seller_gstn, buyer_gstn)
                   or _extract_address_before_gstin(text, buyer_gstn, after=buyer_anchor,
                                                      party_name=buyer_name))

    # ── Invoice header ───────────────────────────────────────────────────────
    invoice_no   = _first_match(INV_NO_RES,   text) or ""
    invoice_date = _first_match(INV_DATE_RES, text) or ""

    # ── Items ────────────────────────────────────────────────────────────────
    items = _items_from_tables(tables)
    if not items:
        items = _items_from_text(text)

    # Assign sr numbers if missing
    for i, item in enumerate(items, 1):
        if not item["sr"]:
            item["sr"] = str(i)

    # ── Totals ───────────────────────────────────────────────────────────────
    taxable       = clean_amount(_first_match(TAXABLE_RES,       text)) or 0.0
    sgst          = clean_amount(_first_match(SGST_TOTAL_RES,    text)) or 0.0
    cgst          = clean_amount(_first_match(CGST_TOTAL_RES,    text)) or 0.0
    igst          = clean_amount(_first_match(IGST_TOTAL_RES,    text)) or 0.0
    invoice_total = clean_amount(_first_match(INVOICE_TOTAL_RES, text)) or 0.0

    round_off = _extract_round_off(text)

    # Fallback: derive taxable from item sum when regex failed or grabbed wrong number
    if items:
        item_sum = round(sum(i["amount"] or 0 for i in items), 2)
        if item_sum > 0:
            # If taxable is 0 OR clearly wrong (item sum is >2× the extracted taxable)
            if taxable == 0.0 or (taxable > 0 and item_sum > taxable * 1.5):
                taxable = item_sum

    # Fallback: derive SGT/CGST from items if totals missing
    if sgst == 0.0 and items:
        s = sum(i["sgst_amount"] or 0 for i in items)
        if s > 0:
            sgst = round(s, 2)
    if cgst == 0.0 and items:
        c = sum(i["cgst_amount"] or 0 for i in items)
        if c > 0:
            cgst = round(c, 2)

    # Fill missing item-level GST from invoice totals
    _fill_item_gst(items, sgst, cgst, igst)

    # ── Invoice-level GST rates (derived from most common item rate) ──────────
    cgst_pct  = _most_common_rate(items, "cgst_rate")
    sgst_pct  = _most_common_rate(items, "sgst_rate")
    igst_pct  = _most_common_rate(items, "igst_rate")
    total_tax = round(sgst + cgst + igst, 2)

    # ── Validation ────────────────────────────────────────────────────────────
    validation = _validate(taxable, sgst, cgst, igst, round_off, invoice_total, items)

    return {
        "seller": {"name": seller_name, "gstin": seller_gstn, "address": seller_addr},
        "buyer":  {"name": buyer_name,  "gstin": buyer_gstn,  "address": buyer_addr},
        "invoice": {
            "number": invoice_no,
            "date":   invoice_date,
            "type":   "",
        },
        "items": items,
        "totals": {
            "taxable_amount": taxable,
            "sgst":           sgst,
            "cgst":           cgst,
            "igst":           igst,
            "cgst_pct":       cgst_pct,
            "sgst_pct":       sgst_pct,
            "igst_pct":       igst_pct,
            "total_tax":      total_tax,
            "round_off":      round_off,
            "invoice_total":  invoice_total,
        },
        "validation":  validation,
        "is_scanned":  pdf_result.get("is_scanned", False),
    }
