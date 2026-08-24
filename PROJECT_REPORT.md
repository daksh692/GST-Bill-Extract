# GST Invoice Extractor — Project Report

**Version:** 1.0
**Report date:** 2026-05-16
**Project location:** `G:\PDF data extruder\`
**Sample bills:** `C:\Tally\Bills\`, `E:\New folder\Bills\`

---

## 1. Project Overview

### What it does

A desktop Windows application that reads Indian GST invoice PDFs and extracts every field needed for accounting into a structured Excel sheet. It handles both **digital PDFs** (Tally exports, etc.) and **scanned/OCR'd PDFs**.

### Why it exists

Indian GST invoices come in dozens of formats — Tally, custom templates, scanned bills, multi-page bills, photo-of-paper bills. Typing them into Excel by hand is slow and error-prone. This tool converts a PDF into a verified Excel row (or rows, one per line item) in a few seconds.

### Target user

A business user (non-developer) who receives or issues a steady stream of GST invoices and needs them in Excel for accounting.

### Design constraints

- **Runs on a low-end Intel i3** — no GPU, no large ML models
- **Offline** — no API calls, no data leaves the machine
- **Fast** — under 5 seconds per digital PDF, ~10-15 sec per OCR'd page
- **Verifiable** — every extracted value is shown in a UI for review before export

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      app.py (Tkinter UI)                  │
│   Load PDF → Extract → Review/Edit → Export to Excel      │
└────────────┬──────────────────────────────┬──────────────┘
             │                              │
   ┌─────────▼────────┐         ┌───────────▼──────────┐
   │ pdf_processor.py │         │   excel_manager.py   │
   │ pdfplumber + OCR │         │      openpyxl        │
   └─────────┬────────┘         └──────────────────────┘
             │
   ┌─────────▼────────┐
   │   extractor.py   │
   │   Regex engine   │
   │ (the heart of    │
   │   the tool)      │
   └──────────────────┘
```

### Files

| File | Lines | Purpose |
|---|---|---|
| `main.py` | ~25 | Entry point — launches the Tk window |
| `app.py` | ~1000 | Tkinter UI (header, item table, totals, buttons) |
| `pdf_processor.py` | ~145 | pdfplumber wrapper; OCR fallback if text < 30 words |
| `extractor.py` | ~1100 | All regex patterns and parsing logic |
| `excel_manager.py` | ~270 | Writes to `.xlsx` — one sheet per party, one row per item |
| `requirements.txt` | 5 | Python deps: pdfplumber, PyMuPDF, pytesseract, Pillow, openpyxl |
| `install.bat` | — | One-click installer for end users |

### Extraction pipeline (per PDF)

1. **`pdf_processor.extract_from_pdf(path)`**
   - Tries `pdfplumber` first (digital text + tables)
   - If `< 30 words` extracted → falls back to **Tesseract OCR** at 300 DPI
   - Returns `{text, tables, is_scanned, page_count}`

2. **`extractor.extract_invoice_data(result)`**
   - Runs cascading regex patterns for each field (most-specific first)
   - For items: tries table extraction → falls back to text-line extraction
   - Cross-validates math (item sum = taxable; taxable + tax = total)
   - Returns a structured dict + a `confidence` rating (HIGH / MEDIUM / LOW)

3. **UI review**
   - User sees every field, can edit any cell
   - Math validation runs again on save

4. **`excel_manager.export_to_excel(...)`**
   - Each party gets their own sheet, named after them
   - Multiple invoices from the same party append rows to that sheet
   - Low-confidence rows are highlighted yellow

---

## 3. Supported Bill Formats

Each format has dedicated regex patterns tuned for that vendor's layout. Adding a new format = adding one new pattern.

| Format | Bill example | Items extracted | Notes |
|---|---|---|---|
| **Tally (generic digital)** | SHIV ELECTRICALS, MK Power System | ✓ | The most common format. Uses HSN + unit labels. |
| **Saurashtra Sanatary Stores** | `SalesBill_SS_*.PDF` | ✓ (multi-page, 27+ items) | Custom format: `Sr Desc HSN Qty Unit Rate GST% Taxable`. Multi-page support with B/F handling. |
| **Green Electricals** | `Purchase Bill - 24012025.pdf` | ✓ | 2-column merged layout: `Sr HSN-Desc HSN Qty Unit Rate Taxable CGST SGST IGST Total` |
| **MK Power System** | `MK 0002.pdf` | ✓ | Tally variant: `Sr Desc HSN Qty Rate Unit [Disc%] Amount` |
| **Kiran Crystalflow (OCR)** | `KENT ZWW INVOICE.pdf` | ✓ | OCR with `Rs.` prefix on amounts, GST rate on next line via `@9%` |
| **OCR/simple** | `Sales Bill A0041.pdf` | ✓ | New: handles `1. PRODUCT HSN QTY RATE AMT` from heavily OCR'd scans |

---

## 4. Fields Extracted

### Per-invoice (header)

| Field | Status | Source patterns |
|---|---|---|
| Bill Type (Sales/Purchase) | User-selected | Combobox in UI |
| Invoice No. | ✓ HIGH | 7 regex patterns covering all formats |
| Invoice Date | ✓ HIGH | 9 patterns (DD/MM/YYYY, DD-Mon-YY, "Inv. Date", etc.) |
| **Seller Name** | ✓ Good | 4 patterns + GSTIN-anchored fallback + standalone all-caps fallback |
| Seller GSTIN | ✓ HIGH | Single robust pattern (Indian GSTIN format) |
| **Seller Address** | ✓ Improving | Pincode-anchored + fallback (last lines before GSTIN, with Tally-noise stripping) |
| **Buyer Name** | ✓ Improving | 6 patterns + GSTIN-anchored fallback + Tally `Buyer (Bill to)` pattern |
| Buyer GSTIN | ✓ HIGH | Same GSTIN regex, deduplicated against seller |
| **Buyer Address** | ✓ Improving | Same as seller address |

### Per-item (line)

| Field | Status | Notes |
|---|---|---|
| Sr No. | ✓ | Auto-assigned if missing |
| Description | ✓ | Strips HSN prefix in Green Electricals format |
| HSN/SAC | ✓ | 4-8 digit code |
| Qty | ✓ | |
| **Unit** | ✓ | NEW: now extracted (NOS, MTR, KG, PCS, etc.) |
| Rate | ✓ | |
| Amount (Taxable) | ✓ | |
| SGST % / SGST Amt | ✓ | |
| CGST % / CGST Amt | ✓ | |
| IGST % / IGST Amt | ✓ | |
| Item Total | ✓ | Derived if missing: amount + taxes |

### Totals

| Field | Status | Notes |
|---|---|---|
| Taxable Total | ✓ | Falls back to sum of item amounts if regex misses |
| Total SGST | ✓ | |
| Total CGST | ✓ | |
| Total IGST | ✓ | |
| **SGST Rate %** | ✓ | NEW: invoice-level rate, derived from most common item rate |
| **CGST Rate %** | ✓ | NEW: same |
| **IGST Rate %** | ✓ | NEW: same |
| **Total Tax Amt** | ✓ | NEW: SGST + CGST + IGST |
| Round Off | ✓ | Handles `(-)0.18`, `Round Off -0.48`, etc. |
| Invoice Total | ✓ | |

### Confidence Ratings

Each extraction returns one of:
- **HIGH** — All math checks pass, all key fields present
- **MEDIUM** — One issue (e.g., items not parsed but totals found)
- **LOW** — Multiple issues, requires manual review

Low-confidence rows are highlighted yellow in the exported Excel sheet.

---

## 5. What Has Been Done (Recent Improvements)

### Item extraction
- ✅ Added Saurashtra format (`Sr Desc HSN Qty Unit Rate GST% Taxable`) with multi-page support — bills with 27+ items now extract cleanly across 2-3 pages
- ✅ Added Green Electricals format (2-column layout)
- ✅ Added MK Power System format
- ✅ Added OCR/simple format (`1. PRODUCT HSN QTY RATE AMT`)
- ✅ Multi-header scanning — if a header row repeats per page (B/F bills), all sections are parsed and duplicates removed via `seen_sr`
- ✅ Header-fallback parsing — if no header is detected (common in OCR), parses from line 0 with strict patterns

### Field additions
- ✅ Added **Unit** per item (NOS, MTR, KG, etc.) — captured from regex group or via `_UNIT_RE` keyword search
- ✅ Added **Seller Address** and **Buyer Address** with two-strategy extraction:
  1. **Pincode-anchored:** find the line containing a 6-digit Indian pincode near the GSTIN, strip Tally column-header prefixes (`Destination, …`) and trailing noise (phone, email, `NEFT`, `Buyer's Order No.`)
  2. **Fallback:** last 1-2 non-header lines before GSTIN, with the same trailing-noise stripping
- ✅ Added invoice-level **CGST %, SGST %, IGST %** (derived from most common item rate)
- ✅ Added **Total Tax Amount** (SGST + CGST + IGST)

### Name extraction
- ✅ Added Tally `Buyer (Bill to) …\nNAME` pattern → fixes SHIV ELECTRICALS buyer
- ✅ Added GSTIN-anchored name fallback — walks 8 lines back from a GSTIN, skipping labels/addresses
- ✅ Added trailing Tally-column noise stripper (`Dispatched Through Destination`, `Mode/Terms`, etc.)
- ✅ Added standalone all-caps name fallback — finds seller name anywhere in the text (KENT ZWW seller from footer)
- ✅ Added `A/c Holder's Name` pattern (extracts seller from bank-details block)
- ✅ Added looser `For NAME` pattern (works mid-line for OCR'd bills)

### Bug fixes
- ✅ Fixed `clean_amount()` stripping negative signs from parenthesized amounts
- ✅ Fixed buyer GSTIN being incorrectly populated with seller GSTIN on multi-page bills
- ✅ Fixed `_TEXT_STOP_RE` trailing `|` that caused every line to match stop
- ✅ Fixed seller-name regex capturing across newlines (e.g., grabbing invoice number)
- ✅ Fixed Round Off plain-minus pattern (Saurashtra: `Round Off -0.48`)

### UI / Excel
- ✅ Added Address fields next to GSTIN inputs (rows 1 and 2 of header section)
- ✅ Added Unit column to item treeview (4th column, after Qty)
- ✅ Added 7 new columns to Excel export: Seller Address, Buyer Address, Unit, SGST Rate %, CGST Rate %, IGST Rate %, Total Tax Amt

---

## 6. Current Accuracy (Tested Bills)

| Bill | Seller Name | Seller Addr | Buyer Name | Buyer Addr | Items |
|---|---|---|---|---|---|
| Saurashtra SS_446 | ✅ | ✅ | ✅ | ⚠️ no GSTIN | ✅ 27/27 |
| MK Power System | ✅ | ⚠️ no pincode in text | ✅ | ✅ Perfect | ✅ 2/2 |
| SHIV ELECTRICALS | ✅ | ✅ | ✅ (fixed) | ✅ | ✅ 6/6 |
| KENT ZWW (OCR) | ✅ (fixed) | ✅ | ✅ | ⚠️ no GSTIN | ✅ 1/1 |
| Sales Bill A0041 (OCR) | ✅ (fixed) | ⚠️ OCR noise | ❌ no GSTIN | — | ✅ 2/2 (was 0) |
| Green Electricals | ✅ (fixed) | ⚠️ partial | ⚠️ merged with seller | ⚠️ partial | ✅ 3/3 |

Legend: ✅ correct · ⚠️ partial / format-dependent · ❌ requires manual input

---

## 7. Known Limitations

### A. When the bill has no buyer/seller GSTIN
Most extraction logic anchors on GSTIN. If a small customer doesn't have one (e.g., Saurashtra customers like `GULSHAN PLUMBER`, `MARUTBHAI`), the buyer address can't be auto-extracted. **Workaround:** type it in manually before export.

### B. Heavily-OCR'd PDFs with corrupted text
When OCR mangles characters (`OMEGA PLAS LIMITED` → `OMEGA PLAS AMITED`), no regex can fix that. The name extraction may also pick up garbled lines from elsewhere on the page. **Workaround:** review and correct in the UI before export.

### C. 2-column merged layouts (Green Electricals)
pdfplumber sometimes merges the left (seller) and right (buyer) columns into one long line of text. The seller name detection works (via `For GREEN ELECTRICALS PVT LTD` in the footer), but buyer name often comes out merged. **Workaround:** manual correction.

### D. Bills with name only in the footer
For bills like KENT ZWW where the seller name appears only at the bottom of the page (not near the GSTIN), the standalone-caps fallback works, but it may occasionally pick up a stray heading on noisier bills. **Risk:** low — exclusions are tuned.

### E. Multi-line addresses split awkwardly
Tally PDFs sometimes split addresses across 3-4 lines mixed with right-column metadata. Address extraction captures the line containing the pincode, but earlier address parts (flat number, street) may be lost. **Workaround:** edit address field before export.

---

## 8. What Needs to Be Done (Roadmap)

### 🎯 Immediate priorities (user-stated: focus on names + addresses)

- [ ] **Collect more sample bills** for vendors where name/address is wrong, along with the *correct* expected values. This is the biggest accuracy-multiplier — each new format I see, I can pattern-match.
- [ ] **Improve Green Electricals buyer extraction** — needs a targeted pattern to split the merged left/right column data
- [ ] **Buyer name without GSTIN** — currently relies on GSTIN as anchor. Could add: "scan for capitalized name on line after `Buyer (Bill to)` even when no GSTIN follows"
- [ ] **Address concatenation** — when address spans multiple lines, try to combine them (currently only one line is captured)

### 🛠 Nice-to-have improvements

- [ ] **OCR pre-processing** — try Tesseract with `--psm 4` (single column) for column-heavy bills; deskew and remove noise before OCR
- [ ] **Address auto-complete from past bills** — if buyer GSTIN matches a previously-saved row, suggest that address
- [ ] **Vendor profile system** — let user save "this vendor uses this layout" so the right pattern set is tried first
- [ ] **Bulk PDF processing** — drop a folder of PDFs, extract all into one Excel run
- [ ] **PDF preview pane** — show the PDF alongside the editable fields, so the user can verify visually
- [ ] **Keyboard shortcuts** — Ctrl+E to extract, Ctrl+S to export, Tab navigation through item cells

### 🧹 Code-quality / maintenance

- [ ] **Test suite** — currently no automated tests. Add `tests/` with one test per supported format using saved PDFs
- [ ] **Refactor `extractor.py`** — at ~1100 lines, the patterns could be split into a `patterns/` package: one file per vendor format
- [ ] **Logging** — add a debug log so when extraction fails, we can see which regex matched what
- [ ] **Versioning** — tag releases so users on different versions can be supported

### 📋 Documentation

- [ ] **User guide** with screenshots — "How to extract your first invoice", "What the yellow rows mean", "How to add a new vendor format"
- [ ] **Quick-reference card** — single-page PDF showing the extraction flow and field names
- [ ] **Installation video** — for non-technical users, a 2-minute screen recording

### 🚀 Possible future features

- [ ] **Auto-classify Sales vs Purchase** based on whether the seller GSTIN matches a saved "my GSTIN" setting
- [ ] **GSTR-1 / GSTR-3B export** — format the data directly for GST return filing
- [ ] **E-invoice (IRN) parsing** — handle the JSON inside the QR code on e-invoices for 100% accurate extraction
- [ ] **Tally export format** — write out an XML that imports directly into Tally
- [ ] **Cloud sync** — encrypted backup of extracted invoices to user's own Drive/OneDrive

---

## 9. How to Run

```bat
:: First time only — install dependencies
install.bat

:: Run the app
python main.py
```

If OCR is needed, also install Tesseract from:
https://github.com/UB-Mannheim/tesseract/wiki

---

## 10. How to Add a New Bill Format

When a new bill layout doesn't extract well, this is the workflow:

1. **Get the PDF text:** look at the raw `text` output from `pdfplumber` for that bill
2. **Find a unique anchor:** an exact phrase or column pattern unique to that vendor
3. **Write one new regex** in `extractor.py` and add it to the appropriate list (e.g., `BUYER_NAME_RES`) or as a new item-pattern (e.g., `_NEW_VENDOR_ITEM_RE`)
4. **Wire it into `_items_from_text()`** if it's an item pattern, with the other format-specific parsers
5. **Test against all known bills** to make sure nothing regressed
6. **Document the format** in section 3 of this report

The cascade order matters: most-specific patterns first, fallbacks last. The first matching pattern wins.

---

## 11. Files Touched in Current Session

- `extractor.py` — added ~250 lines: address extraction, name fallback, OCR item pattern, GST rate derivation, unit extraction in all item parsers
- `excel_manager.py` — added 7 new columns
- `app.py` — added address input fields, unit column in item table, updated populate/collect methods for new fields

---

*End of report.*
