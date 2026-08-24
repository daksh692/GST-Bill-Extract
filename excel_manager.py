"""
Excel export manager.

Rules:
  - Each party (vendor/customer) gets their own sheet named after them.
  - Multiple invoices from the same party append rows to the same sheet.
  - User can also force a new dated sheet if they want a separate one.
  - One row per line item; invoice header columns repeat on each row.
  - Auto-formats amounts as Indian number format, dates as DD-MM-YYYY.
"""

import os
import re
from datetime import datetime
from typing import Optional

import openpyxl
from openpyxl.styles import (Font, PatternFill, Alignment,
                              Border, Side, numbers)
from openpyxl.utils import get_column_letter

# ── Column definitions (order matters — this is the Excel column order) ──────

COLUMNS = [
    ("Bill Type",       14),
    ("Invoice No.",     14),
    ("Invoice Date",    14),
    ("Seller Name",     28),
    ("Seller GSTIN",    20),
    ("Seller Address",  36),   # address block before seller GSTIN
    ("Buyer Name",      28),
    ("Buyer GSTIN",     20),
    ("Buyer Address",   36),   # address block before buyer GSTIN
    ("Sr",               6),
    ("Item Description",40),
    ("HSN/SAC",         12),
    ("Qty",             10),
    ("Unit",             8),   # unit of measure per item
    ("Rate (₹)",        14),
    ("Amount (₹)",      14),
    ("SGST %",           9),
    ("SGST Amt (₹)",    14),
    ("CGST %",           9),
    ("CGST Amt (₹)",    14),
    ("IGST %",           9),
    ("IGST Amt (₹)",    14),
    ("Item Total (₹)",  16),
    ("Taxable Total",   14),   # repeated per invoice block
    ("Total SGST",      12),
    ("Total CGST",      12),
    ("Total IGST",      12),
    ("SGST Rate %",      9),   # invoice-level GST rates
    ("CGST Rate %",      9),
    ("IGST Rate %",      9),
    ("Total Tax Amt",   14),   # SGST + CGST + IGST combined
    ("Round Off",       10),
    ("Invoice Total",   14),
]

# ── Styles ───────────────────────────────────────────────────────────────────

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

_ALT_FILL  = PatternFill("solid", fgColor="DCE6F1")
_NUM_FMT   = '#,##0.00'
_BORDER    = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"),  bottom=Side(style="thin")
)

_WARN_FILL = PatternFill("solid", fgColor="FFEB9C")  # yellow for low-confidence


def _safe_sheet_name(name: str) -> str:
    """Excel sheet names: max 31 chars, no special chars."""
    name = re.sub(r"[\\/*?:\[\]]", "_", name).strip()
    return name[:31] if name else "Sheet"


def get_sheet_names(filepath: str) -> list:
    """Return list of existing sheet names in the workbook, or []."""
    if not os.path.exists(filepath):
        return []
    try:
        wb = openpyxl.load_workbook(filepath, read_only=True)
        names = wb.sheetnames
        wb.close()
        return names
    except Exception:
        return []


def _write_header_row(ws, row: int = 1):
    for col_idx, (col_name, col_width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=row, column=col_idx, value=col_name)
        cell.font      = _HEADER_FONT
        cell.fill      = _HEADER_FILL
        cell.alignment = _HEADER_ALIGN
        cell.border    = _BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = col_width
    ws.row_dimensions[row].height = 32


def _write_data_row(ws, row_num: int, values: list, alt: bool, low_conf: bool):
    fill = _WARN_FILL if low_conf else (_ALT_FILL if alt else None)
    for col_idx, value in enumerate(values, start=1):
        cell = ws.cell(row=row_num, column=col_idx, value=value)
        cell.border    = _BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if fill:
            cell.fill = fill
        # Format numeric columns
        if isinstance(value, float):
            cell.number_format = _NUM_FMT


def _build_rows(data: dict) -> list:
    """
    Convert extracted invoice data into a list of row-value-lists.
    Each item becomes one row; invoice header fields repeat.
    """
    inv    = data.get("invoice", {})
    seller = data.get("seller", {})
    buyer  = data.get("buyer",  {})
    totals = data.get("totals", {})
    items  = data.get("items",  [])

    # Invoice-level columns (repeated per item row)
    inv_prefix = [
        inv.get("type", ""),
        inv.get("number", ""),
        inv.get("date", ""),
        seller.get("name", ""),
        seller.get("gstin", ""),
        seller.get("address", ""),   # NEW: seller address
        buyer.get("name", ""),
        buyer.get("gstin", ""),
        buyer.get("address", ""),    # NEW: buyer address
    ]

    # Invoice-level totals (repeated per item row)
    inv_suffix = [
        totals.get("taxable_amount") or "",
        totals.get("sgst") or "",
        totals.get("cgst") or "",
        totals.get("igst") or "",
        totals.get("sgst_pct") or "",    # NEW: SGST rate %
        totals.get("cgst_pct") or "",    # NEW: CGST rate %
        totals.get("igst_pct") or "",    # NEW: IGST rate %
        totals.get("total_tax") or "",   # NEW: total tax amount
        totals.get("round_off") or "",
        totals.get("invoice_total") or "",
    ]

    rows = []
    if not items:
        # No items extracted — still write one row with header + totals
        # 14 item placeholders: sr, desc, hsn, qty, unit, rate, amount,
        #                        sgst%, sgst_amt, cgst%, cgst_amt, igst%, igst_amt, total
        row = inv_prefix + ["", "", "", "", "", "", "", "", "", "", "", "", "", ""] + inv_suffix
        rows.append(row)
    else:
        for item in items:
            row = inv_prefix + [
                item.get("sr", ""),
                item.get("description", ""),
                item.get("hsn", ""),
                item.get("qty") or "",
                item.get("unit", ""),        # NEW: unit
                item.get("rate") or "",
                item.get("amount") or "",
                item.get("sgst_rate") or "",
                item.get("sgst_amount") or "",
                item.get("cgst_rate") or "",
                item.get("cgst_amount") or "",
                item.get("igst_rate") or "",
                item.get("igst_amount") or "",
                item.get("total") or "",
            ] + inv_suffix
            rows.append(row)

    return rows


def export_to_excel(
    filepath: str,
    sheet_name: str,
    data: dict,
    create_new_sheet: bool = False,
    new_sheet_suffix: str = "",
) -> tuple[bool, str]:
    """
    Append invoice data to the given Excel file + sheet.

    Args:
        filepath:         Path to .xlsx file (created if not exists).
        sheet_name:       Target sheet name.
        data:             Extracted invoice dict from extractor.
        create_new_sheet: If True, create a new dated sheet instead of appending.
        new_sheet_suffix: Suffix to add when creating a new sheet (e.g. date string).

    Returns:
        (success: bool, message: str)
    """
    try:
        if os.path.exists(filepath):
            wb = openpyxl.load_workbook(filepath)
        else:
            wb = openpyxl.Workbook()
            # Remove default blank sheet
            if "Sheet" in wb.sheetnames:
                del wb["Sheet"]

        # Determine actual sheet name
        target = _safe_sheet_name(sheet_name)
        if create_new_sheet and new_sheet_suffix:
            target = _safe_sheet_name(f"{sheet_name} - {new_sheet_suffix}")

        if target in wb.sheetnames and not create_new_sheet:
            ws = wb[target]
            # Append mode: find next empty row
            next_row = ws.max_row + 1
        else:
            ws = wb.create_sheet(title=_safe_sheet_name(target))
            _write_header_row(ws, row=1)
            next_row = 2

        low_conf = data.get("validation", {}).get("confidence", "HIGH") == "LOW"
        rows = _build_rows(data)

        for i, row_values in enumerate(rows):
            alt = (next_row + i) % 2 == 0
            _write_data_row(ws, next_row + i, row_values, alt, low_conf)

        # Freeze panes at row 2 (header stays visible when scrolling)
        ws.freeze_panes = "A2"

        wb.save(filepath)
        n = len(rows)
        return True, f"Saved {n} row(s) to sheet '{ws.title}' in {os.path.basename(filepath)}"

    except PermissionError:
        return False, (
            f"Cannot write to {os.path.basename(filepath)} — "
            "close the file in Excel first."
        )
    except Exception as exc:
        return False, f"Export failed: {exc}"


def get_tab_separated(data: dict) -> str:
    """
    Return the invoice data as TSV (tab-separated values) so the user
    can paste it directly into any spreadsheet.
    """
    headers = [col for col, _ in COLUMNS]
    rows    = _build_rows(data)

    lines = ["\t".join(str(h) for h in headers)]
    for row in rows:
        lines.append("\t".join(str(v) if v != "" else "" for v in row))
    return "\n".join(lines)


def suggest_sheet_name(data: dict) -> str:
    """
    Suggest a sheet name based on the counterparty name.
    For a sales bill → buyer name; for a purchase bill → seller name.
    Falls back to GSTIN if name is empty.
    """
    bill_type = data.get("invoice", {}).get("type", "").lower()
    if "purchase" in bill_type:
        party = data.get("seller", {})
    else:
        party = data.get("buyer", {})

    name  = party.get("name", "").strip()
    gstin = party.get("gstin", "").strip()

    return _safe_sheet_name(name or gstin or "Unknown Party")
