"""
GST Invoice Extractor — Desktop Application
Tkinter GUI. All PDF processing runs in a background thread so the
UI never freezes on an Intel i3.
"""

import os
import json
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime

import pdf_processor
import extractor
import excel_manager

# Folder where "Flag for Fix" saves a copy of a bill + its extraction, so a
# user hitting a bad extraction can zip this up and attach it to a GitHub
# issue for a new format/pattern to be added.
FLAGGED_DIR = "flagged_bills"

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS / THEME
# ══════════════════════════════════════════════════════════════════════════════

APP_TITLE   = "GST Invoice Extractor"
APP_VERSION = "1.0"

COLORS = {
    "bg":          "#F0F4F8",
    "panel":       "#FFFFFF",
    "header_bg":   "#1F4E79",
    "header_fg":   "#FFFFFF",
    "accent":      "#2E75B6",
    "ok_green":    "#217346",
    "warn_yellow": "#C9A227",
    "err_red":     "#C0392B",
    "border":      "#BDC3C7",
    "row_alt":     "#EBF5FB",
    "editable":    "#FFFDE7",
}

FONT_LABEL  = ("Segoe UI", 10)
FONT_BOLD   = ("Segoe UI", 10, "bold")
FONT_TITLE  = ("Segoe UI", 14, "bold")
FONT_MONO   = ("Consolas", 9)
FONT_STATUS = ("Segoe UI", 9)

# Treeview columns definition: (id, heading, width, anchor)
ITEM_COLS = [
    ("sr",          "Sr",          40,  "center"),
    ("description", "Description", 220, "w"),
    ("hsn",         "HSN/SAC",      80, "center"),
    ("qty",         "Qty",          60, "center"),
    ("unit",        "Unit",         55, "center"),
    ("rate",        "Rate (₹)",     90, "e"),
    ("amount",      "Amount (₹)",   90, "e"),
    ("sgst_rate",   "SGST%",        55, "center"),
    ("sgst_amount", "SGST (₹)",     80, "e"),
    ("cgst_rate",   "CGST%",        55, "center"),
    ("cgst_amount", "CGST (₹)",     80, "e"),
    ("igst_rate",   "IGST%",        55, "center"),
    ("igst_amount", "IGST (₹)",     80, "e"),
    ("total",       "Item Total",   90, "e"),
]


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER WIDGETS
# ══════════════════════════════════════════════════════════════════════════════

def _labeled_entry(parent, label: str, row: int, col: int,
                   width: int = 30, state: str = "normal") -> tk.StringVar:
    tk.Label(parent, text=label, font=FONT_LABEL,
             bg=COLORS["panel"], anchor="w").grid(
        row=row, column=col, sticky="w", padx=(8, 2), pady=3)
    var = tk.StringVar()
    e = ttk.Entry(parent, textvariable=var, width=width, state=state,
                  font=FONT_LABEL)
    e.grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=3)
    return var


def _fmt(value) -> str:
    """Format a numeric value for display."""
    if value is None or value == "":
        return ""
    try:
        f = float(value)
        return f"{f:,.2f}"
    except (ValueError, TypeError):
        return str(value)


# ══════════════════════════════════════════════════════════════════════════════
#  INLINE TREEVIEW EDITOR
# ══════════════════════════════════════════════════════════════════════════════

class TreeviewEditor:
    """Popup Entry widget for inline editing of a Treeview cell."""

    def __init__(self, tree: ttk.Treeview, col_ids: list):
        self.tree    = tree
        self.col_ids = col_ids
        self._entry  = None
        self._item   = None
        self._col    = None
        tree.bind("<Double-1>", self._on_double_click)

    def _on_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col_id  = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)
        if not item_id or not col_id:
            return

        col_num = int(col_id.replace("#", "")) - 1  # 0-based index into ITEM_COLS
        if col_num < 0 or col_num >= len(self.col_ids):
            return

        self._close()
        self._item = item_id
        self._col  = col_num
        col_key    = self.col_ids[col_num]

        # Get current value
        values = self.tree.item(item_id, "values")
        current = values[col_num] if col_num < len(values) else ""

        # Position the Entry over the cell
        x, y, w, h = self.tree.bbox(item_id, col_id)
        self._entry = tk.Entry(self.tree, font=FONT_LABEL,
                               bg=COLORS["editable"],
                               relief="solid", bd=1)
        self._entry.place(x=x, y=y, width=max(w, 60), height=h)
        self._entry.insert(0, current)
        self._entry.select_range(0, "end")
        self._entry.focus_set()
        self._entry.bind("<Return>",  self._commit)
        self._entry.bind("<Escape>",  lambda e: self._close())
        self._entry.bind("<FocusOut>", self._commit)

    def _commit(self, event=None):
        if not self._entry or not self._item:
            return
        new_val = self._entry.get()
        values  = list(self.tree.item(self._item, "values"))
        while len(values) <= self._col:
            values.append("")
        values[self._col] = new_val
        self.tree.item(self._item, values=values)
        self._close()

    def _close(self):
        if self._entry:
            self._entry.destroy()
            self._entry = None


# ══════════════════════════════════════════════════════════════════════════════
#  EXCEL EXPORT DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class ExcelExportDialog(tk.Toplevel):
    def __init__(self, parent, data: dict):
        super().__init__(parent)
        self.data    = data
        self.result  = None  # set to (filepath, sheet_name, new_sheet, suffix) on OK

        self.title("Export to Excel")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])
        self.grab_set()

        self._filepath_var   = tk.StringVar()
        self._sheet_var      = tk.StringVar(value=excel_manager.suggest_sheet_name(data))
        self._new_sheet_var  = tk.BooleanVar(value=False)
        self._suffix_var     = tk.StringVar(value=datetime.now().strftime("%d-%m-%Y"))
        self._existing_sheets: list = []

        self._build()
        self._center()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        pad = dict(padx=12, pady=6)

        # ── File selection ───────────────────────────────────────────────────
        frm_file = tk.LabelFrame(self, text=" Excel File ", font=FONT_BOLD,
                                 bg=COLORS["bg"], padx=8, pady=6)
        frm_file.pack(fill="x", padx=14, pady=(14, 6))

        tk.Entry(frm_file, textvariable=self._filepath_var, width=44,
                 font=FONT_LABEL, state="readonly").grid(row=0, column=0,
                 padx=(0, 6), sticky="ew")
        ttk.Button(frm_file, text="Browse…",
                   command=self._browse_file).grid(row=0, column=1)
        ttk.Button(frm_file, text="New File",
                   command=self._new_file).grid(row=0, column=2, padx=(4, 0))

        # ── Sheet selection ──────────────────────────────────────────────────
        frm_sheet = tk.LabelFrame(self, text=" Sheet ", font=FONT_BOLD,
                                  bg=COLORS["bg"], padx=8, pady=6)
        frm_sheet.pack(fill="x", padx=14, pady=6)

        tk.Label(frm_sheet, text="Sheet name:", font=FONT_LABEL,
                 bg=COLORS["bg"]).grid(row=0, column=0, sticky="w")

        self._sheet_combo = ttk.Combobox(frm_sheet, textvariable=self._sheet_var,
                                         width=28, font=FONT_LABEL)
        self._sheet_combo.grid(row=0, column=1, sticky="ew",
                               padx=(6, 0), columnspan=2)

        chk = tk.Checkbutton(
            frm_sheet, text="Create separate sheet with date suffix:",
            variable=self._new_sheet_var,
            command=self._toggle_suffix,
            font=FONT_LABEL, bg=COLORS["bg"]
        )
        chk.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._suffix_entry = ttk.Entry(frm_sheet, textvariable=self._suffix_var,
                                       width=14, font=FONT_LABEL, state="disabled")
        self._suffix_entry.grid(row=1, column=2, padx=(6, 0), sticky="w", pady=(6, 0))

        # ── Confidence warning ───────────────────────────────────────────────
        conf = self.data.get("validation", {}).get("confidence", "HIGH")
        errs = self.data.get("validation", {}).get("errors", [])
        if conf != "HIGH":
            color = COLORS["warn_yellow"] if conf == "MEDIUM" else COLORS["err_red"]
            warn_text = f"⚠  Confidence: {conf}\n" + "\n".join(f"• {e}" for e in errs)
            tk.Label(self, text=warn_text, font=FONT_STATUS,
                     bg=color, fg="black", justify="left",
                     wraplength=360, padx=8, pady=4).pack(
                fill="x", padx=14, pady=(0, 6))

        # ── Buttons ──────────────────────────────────────────────────────────
        frm_btn = tk.Frame(self, bg=COLORS["bg"])
        frm_btn.pack(fill="x", padx=14, pady=(4, 14))
        ttk.Button(frm_btn, text="Export", command=self._export).pack(side="right", padx=(6, 0))
        ttk.Button(frm_btn, text="Cancel", command=self.destroy).pack(side="right")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select Excel File",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
        )
        if path:
            self._filepath_var.set(path)
            self._load_sheets(path)

    def _new_file(self):
        path = filedialog.asksaveasfilename(
            title="Create New Excel File",
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")]
        )
        if path:
            self._filepath_var.set(path)
            self._existing_sheets = []
            self._sheet_combo["values"] = []

    def _load_sheets(self, path: str):
        self._existing_sheets = excel_manager.get_sheet_names(path)
        self._sheet_combo["values"] = self._existing_sheets
        suggested = excel_manager.suggest_sheet_name(self.data)
        if suggested in self._existing_sheets:
            self._sheet_var.set(suggested)
        else:
            self._sheet_var.set(suggested)

    def _toggle_suffix(self):
        if self._new_sheet_var.get():
            self._suffix_entry.configure(state="normal")
        else:
            self._suffix_entry.configure(state="disabled")

    def _export(self):
        filepath = self._filepath_var.get().strip()
        if not filepath:
            messagebox.showwarning("No file selected",
                                   "Please select or create an Excel file.",
                                   parent=self)
            return

        sheet = self._sheet_var.get().strip()
        if not sheet:
            messagebox.showwarning("No sheet name",
                                   "Please enter a sheet name.", parent=self)
            return

        self.result = (
            filepath,
            sheet,
            self._new_sheet_var.get(),
            self._suffix_var.get().strip(),
        )
        self.destroy()

    def _center(self):
        self.update_idletasks()
        pw = self.master.winfo_x() + self.master.winfo_width() // 2
        ph = self.master.winfo_y() + self.master.winfo_height() // 2
        x  = pw - self.winfo_width() // 2
        y  = ph - self.winfo_height() // 2
        self.geometry(f"+{x}+{y}")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class InvoiceApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        self.root.geometry("1200x780")
        self.root.minsize(900, 600)
        self.root.configure(bg=COLORS["bg"])

        self._pdf_path   = None
        self._data       = None        # last extracted invoice dict
        self._processing = False

        # StringVars for header fields
        self._sv = {}

        self._build_ui()
        self._set_status("Ready — load a PDF or photo (JPG/PNG) to begin.", "normal")

    # ══════════════════════════════════════════════════════════════════════════
    #  UI CONSTRUCTION
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        self._build_toolbar()
        self._build_main_area()
        self._build_statusbar()

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=COLORS["header_bg"], pady=6)
        bar.pack(fill="x")

        tk.Label(bar, text=f"  {APP_TITLE}", font=FONT_TITLE,
                 bg=COLORS["header_bg"], fg=COLORS["header_fg"]).pack(side="left")

        btn_style = {"relief": "flat", "cursor": "hand2",
                     "bg": COLORS["accent"], "fg": "white",
                     "font": FONT_BOLD, "padx": 12, "pady": 4}

        self._btn_load = tk.Button(bar, text="⊕  Load File",
                                   command=self._browse_pdf, **btn_style)
        self._btn_load.pack(side="left", padx=(20, 4))

        self._btn_extract = tk.Button(bar, text="⚡  Extract",
                                      command=self._start_extraction,
                                      state="disabled", **btn_style)
        self._btn_extract.pack(side="left", padx=4)

        self._btn_clear = tk.Button(bar, text="✕  Clear",
                                    command=self._clear_all,
                                    state="disabled", **btn_style)
        self._btn_clear.pack(side="left", padx=4)

        # File label
        self._file_label_var = tk.StringVar(value="No file loaded")
        tk.Label(bar, textvariable=self._file_label_var,
                 font=FONT_STATUS, bg=COLORS["header_bg"],
                 fg="#AED6F1").pack(side="left", padx=16)

        # Progress bar (hidden until processing)
        self._progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self._progress.pack(side="right", padx=16)
        self._progress.pack_forget()

    # ── Main area (scrollable) ────────────────────────────────────────────────

    def _build_main_area(self):
        # Use a PanedWindow to allow vertical resize between sections
        paned = ttk.PanedWindow(self.root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        # Top pane: header + type
        top_frame = tk.Frame(paned, bg=COLORS["bg"])
        paned.add(top_frame, weight=1)
        self._build_header_section(top_frame)

        # Middle pane: items table
        mid_frame = tk.Frame(paned, bg=COLORS["bg"])
        paned.add(mid_frame, weight=4)
        self._build_items_section(mid_frame)

        # Bottom pane: totals + actions
        bot_frame = tk.Frame(paned, bg=COLORS["bg"])
        paned.add(bot_frame, weight=1)
        self._build_totals_section(bot_frame)

    # ── Header section ────────────────────────────────────────────────────────

    def _build_header_section(self, parent):
        frm = tk.LabelFrame(parent, text=" Invoice Header ",
                             font=FONT_BOLD, bg=COLORS["panel"],
                             padx=6, pady=6)
        frm.pack(fill="both", expand=True, padx=4, pady=(4, 2))
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=1)
        frm.columnconfigure(5, weight=0)   # GSTIN ok label — fixed width
        frm.columnconfigure(6, weight=2)   # Address entry — stretches

        # Row 0: Bill type | Invoice No | Invoice Date
        tk.Label(frm, text="Bill Type:", font=FONT_LABEL,
                 bg=COLORS["panel"]).grid(row=0, column=0, sticky="w", padx=(8, 2), pady=4)
        self._sv["bill_type"] = tk.StringVar(value="Sales")
        type_combo = ttk.Combobox(frm, textvariable=self._sv["bill_type"],
                                  values=["Sales", "Purchase"],
                                  width=12, state="readonly", font=FONT_LABEL)
        type_combo.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=4)

        self._sv["invoice_no"]   = _labeled_entry(frm, "Invoice No.:", 0, 2, 18)
        self._sv["invoice_date"] = _labeled_entry(frm, "Date:",        0, 4, 18)

        # Row 1: Seller Name | Seller GSTIN | [ok] | Seller Address
        self._sv["seller_name"]    = _labeled_entry(frm, "Seller Name:",    1, 0, 26)
        self._sv["seller_gstin"]   = _labeled_entry(frm, "Seller GSTIN:",   1, 2, 20)

        self._seller_gstin_ok = tk.Label(frm, text="", font=FONT_STATUS,
                                         bg=COLORS["panel"])
        self._seller_gstin_ok.grid(row=1, column=5, sticky="w")

        self._sv["seller_address"] = _labeled_entry(frm, "Address:", 1, 6, 36)

        # Row 2: Buyer Name | Buyer GSTIN | [ok] | Buyer Address
        self._sv["buyer_name"]     = _labeled_entry(frm, "Buyer Name:",     2, 0, 26)
        self._sv["buyer_gstin"]    = _labeled_entry(frm, "Buyer GSTIN:",    2, 2, 20)

        self._buyer_gstin_ok  = tk.Label(frm, text="", font=FONT_STATUS,
                                         bg=COLORS["panel"])
        self._buyer_gstin_ok.grid(row=2, column=5, sticky="w")

        self._sv["buyer_address"]  = _labeled_entry(frm, "Address:", 2, 6, 36)

        self._sv["seller_gstin"].trace_add("write", lambda *_: self._validate_gstin("seller"))
        self._sv["buyer_gstin"].trace_add("write",  lambda *_: self._validate_gstin("buyer"))

    def _validate_gstin(self, party: str):
        import re
        val   = self._sv[f"{party}_gstin"].get().strip().upper()
        label = self._seller_gstin_ok if party == "seller" else self._buyer_gstin_ok
        if not val:
            label.config(text="", fg="black")
        elif re.match(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", val):
            label.config(text="✓ Valid", fg=COLORS["ok_green"])
        else:
            label.config(text="✗ Invalid format", fg=COLORS["err_red"])

    # ── Items section (editable treeview) ─────────────────────────────────────

    def _build_items_section(self, parent):
        frm = tk.LabelFrame(parent, text=" Line Items (double-click a cell to edit) ",
                             font=FONT_BOLD, bg=COLORS["panel"], padx=4, pady=4)
        frm.pack(fill="both", expand=True, padx=4, pady=2)

        # Treeview + scrollbars
        tree_frame = tk.Frame(frm, bg=COLORS["panel"])
        tree_frame.pack(fill="both", expand=True)

        v_scroll = ttk.Scrollbar(tree_frame, orient="vertical")
        h_scroll = ttk.Scrollbar(tree_frame, orient="horizontal")

        col_ids = [c[0] for c in ITEM_COLS]
        self.tree = ttk.Treeview(
            tree_frame,
            columns=col_ids,
            show="headings",
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set,
            selectmode="browse",
            height=8,
        )

        for col_id, heading, width, anchor in ITEM_COLS:
            self.tree.heading(col_id, text=heading, anchor=anchor)
            self.tree.column(col_id, width=width, anchor=anchor, minwidth=40)

        v_scroll.config(command=self.tree.yview)
        h_scroll.config(command=self.tree.xview)

        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # Alternate row colors
        self.tree.tag_configure("alt",     background=COLORS["row_alt"])
        self.tree.tag_configure("normal",  background="white")
        self.tree.tag_configure("warning", background="#FFEB9C")

        # Inline editing
        self._editor = TreeviewEditor(self.tree, col_ids)

        # Row buttons
        btn_row = tk.Frame(frm, bg=COLORS["panel"])
        btn_row.pack(anchor="w", pady=(4, 0))

        ttk.Button(btn_row, text="+ Add Row",
                   command=self._add_item_row).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="− Delete Row",
                   command=self._delete_item_row).pack(side="left")

    # ── Totals section ────────────────────────────────────────────────────────

    def _build_totals_section(self, parent):
        frm = tk.LabelFrame(parent, text=" Totals & Validation ",
                             font=FONT_BOLD, bg=COLORS["panel"], padx=8, pady=6)
        frm.pack(fill="both", expand=True, padx=4, pady=(2, 4))

        # Totals row
        tot_frm = tk.Frame(frm, bg=COLORS["panel"])
        tot_frm.pack(fill="x")

        fields = [
            ("taxable",       "Taxable Amount (₹):"),
            ("sgst",          "Total SGST (₹):"),
            ("cgst",          "Total CGST (₹):"),
            ("igst",          "Total IGST (₹):"),
            ("round_off",     "Round Off (₹):"),
            ("invoice_total", "Invoice Total (₹):"),
        ]
        for i, (key, label) in enumerate(fields):
            tk.Label(tot_frm, text=label, font=FONT_LABEL,
                     bg=COLORS["panel"]).grid(row=0, column=i*2, sticky="e",
                                              padx=(8, 2))
            self._sv[key] = tk.StringVar()
            e = ttk.Entry(tot_frm, textvariable=self._sv[key],
                          width=13, font=FONT_LABEL)
            e.grid(row=0, column=i*2+1, sticky="ew", padx=(0, 8))
            e.bind("<FocusOut>", self._revalidate)

        # Validation status
        self._validation_label = tk.Label(
            frm, text="", font=FONT_BOLD,
            bg=COLORS["panel"], anchor="w"
        )
        self._validation_label.pack(fill="x", pady=(6, 2))

        # Action buttons
        btn_row = tk.Frame(frm, bg=COLORS["panel"])
        btn_row.pack(anchor="e", pady=(4, 0))

        self._btn_revalidate = ttk.Button(btn_row, text="↻ Re-validate",
                                          command=self._revalidate,
                                          state="disabled")
        self._btn_revalidate.pack(side="left", padx=(0, 8))

        self._btn_copy = ttk.Button(btn_row, text="⎘  Copy Table (paste into Excel)",
                                    command=self._copy_to_clipboard,
                                    state="disabled")
        self._btn_copy.pack(side="left", padx=(0, 8))

        self._btn_export = ttk.Button(btn_row, text="💾  Export to Excel",
                                      command=self._export_to_excel,
                                      state="disabled")
        self._btn_export.pack(side="left", padx=(0, 8))

        self._btn_flag = ttk.Button(btn_row, text="🚩  Flag for Fix",
                                    command=self._flag_bill,
                                    state="disabled")
        self._btn_flag.pack(side="left")

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=COLORS["border"], height=1)
        bar.pack(fill="x")
        self._status_var = tk.StringVar()
        self._status_lbl = tk.Label(
            self.root, textvariable=self._status_var,
            font=FONT_STATUS, anchor="w",
            bg=COLORS["bg"], fg="#555555"
        )
        self._status_lbl.pack(fill="x", padx=10, pady=2)

    # ══════════════════════════════════════════════════════════════════════════
    #  ACTIONS
    # ══════════════════════════════════════════════════════════════════════════

    # ── Load PDF ──────────────────────────────────────────────────────────────

    def _browse_pdf(self):
        path = filedialog.askopenfilename(
            title="Select Invoice — PDF or Photo",
            filetypes=[
                ("All supported", "*.pdf *.jpg *.jpeg *.png *.bmp *.tiff *.tif"),
                ("PDF files",     "*.pdf"),
                ("Images",        "*.jpg *.jpeg *.png *.bmp *.tiff *.tif"),
                ("All files",     "*.*"),
            ]
        )
        if not path:
            return
        self._pdf_path = path
        fname = os.path.basename(path)
        size  = os.path.getsize(path) / 1024
        self._file_label_var.set(f"{fname}  ({size:.0f} KB)")
        self._btn_extract.config(state="normal")
        self._btn_clear.config(state="normal")
        kind = "photo" if pdf_processor.is_image_file(path) else "PDF"
        self._set_status(f"Loaded {kind}: {fname} — click Extract to process.", "normal")

    # ── Extract ───────────────────────────────────────────────────────────────

    def _start_extraction(self):
        if not self._pdf_path or self._processing:
            return
        self._processing = True
        self._btn_extract.config(state="disabled")
        self._progress.pack(side="right", padx=16)
        self._progress.start(12)
        self._set_status("Extracting — please wait…", "normal")

        thread = threading.Thread(target=self._extract_worker, daemon=True)
        thread.start()

    def _extract_worker(self):
        """Runs in background thread. Posts result back to main thread via after()."""
        try:
            if pdf_processor.is_image_file(self._pdf_path):
                pdf_result = pdf_processor.extract_from_image(self._pdf_path)
            else:
                pdf_result = pdf_processor.extract_from_pdf(self._pdf_path)
            if pdf_result.get("error"):
                self.root.after(0, self._extraction_error, pdf_result["error"])
                return
            data = extractor.extract_invoice_data(pdf_result)
            self.root.after(0, self._extraction_done, data)
        except Exception as exc:
            self.root.after(0, self._extraction_error, str(exc))

    def _extraction_done(self, data: dict):
        self._progress.stop()
        self._progress.pack_forget()
        self._processing = False
        self._data = data
        self._populate_ui(data)
        self._btn_extract.config(state="normal")
        scanned_note = " [OCR — check accuracy]" if data.get("is_scanned") else ""
        conf = data.get("validation", {}).get("confidence", "HIGH")
        self._set_status(
            f"Extraction complete{scanned_note}. "
            f"Confidence: {conf}. Review and export.", "normal"
        )

    def _extraction_error(self, msg: str):
        self._progress.stop()
        self._progress.pack_forget()
        self._processing = False
        self._btn_extract.config(state="normal")
        messagebox.showerror("Extraction Error", msg, parent=self.root)
        self._set_status(f"Error: {msg}", "error")

    # ── Populate UI from data dict ─────────────────────────────────────────────

    def _populate_ui(self, data: dict):
        inv    = data.get("invoice", {})
        seller = data.get("seller", {})
        buyer  = data.get("buyer",  {})
        totals = data.get("totals", {})
        items  = data.get("items",  [])

        # Header
        if inv.get("type"):
            self._sv["bill_type"].set(inv["type"].capitalize())
        self._sv["invoice_no"].set(inv.get("number", ""))
        self._sv["invoice_date"].set(inv.get("date", ""))
        self._sv["seller_name"].set(seller.get("name", ""))
        self._sv["seller_gstin"].set(seller.get("gstin", ""))
        self._sv["seller_address"].set(seller.get("address", ""))
        self._sv["buyer_name"].set(buyer.get("name", ""))
        self._sv["buyer_gstin"].set(buyer.get("gstin", ""))
        self._sv["buyer_address"].set(buyer.get("address", ""))

        # Totals
        self._sv["taxable"].set(_fmt(totals.get("taxable_amount")))
        self._sv["sgst"].set(_fmt(totals.get("sgst")))
        self._sv["cgst"].set(_fmt(totals.get("cgst")))
        self._sv["igst"].set(_fmt(totals.get("igst")))
        self._sv["round_off"].set(_fmt(totals.get("round_off")))
        self._sv["invoice_total"].set(_fmt(totals.get("invoice_total")))

        # Items
        for row in self.tree.get_children():
            self.tree.delete(row)

        for i, item in enumerate(items):
            tag = "alt" if i % 2 else "normal"
            self.tree.insert("", "end", tags=(tag,), values=(
                item.get("sr", ""),
                item.get("description", ""),
                item.get("hsn", ""),
                _fmt(item.get("qty")),
                item.get("unit", ""),
                _fmt(item.get("rate")),
                _fmt(item.get("amount")),
                _fmt(item.get("sgst_rate")),
                _fmt(item.get("sgst_amount")),
                _fmt(item.get("cgst_rate")),
                _fmt(item.get("cgst_amount")),
                _fmt(item.get("igst_rate")),
                _fmt(item.get("igst_amount")),
                _fmt(item.get("total")),
            ))

        # Validation display
        self._show_validation(data.get("validation", {}))

        # Enable buttons
        self._btn_revalidate.config(state="normal")
        self._btn_copy.config(state="normal")
        self._btn_export.config(state="normal")
        self._btn_flag.config(state="normal")

    # ── Treeview row management ───────────────────────────────────────────────

    def _add_item_row(self):
        i = len(self.tree.get_children())
        tag = "alt" if i % 2 else "normal"
        # 14 columns: sr, desc, hsn, qty, unit, rate, amount,
        #              sgst_rate, sgst_amount, cgst_rate, cgst_amount, igst_rate, igst_amount, total
        self.tree.insert("", "end", tags=(tag,),
                         values=("", "", "", "", "", "", "", "", "", "", "", "", "", ""))

    def _delete_item_row(self):
        selected = self.tree.selection()
        if selected:
            self.tree.delete(selected[0])

    # ── Validation ────────────────────────────────────────────────────────────

    def _revalidate(self, *_):
        """Re-run validation from current UI values (after user edits)."""
        if not self._data:
            return
        data = self._collect_current_data()
        v    = extractor._validate(
            data["totals"]["taxable_amount"],
            data["totals"]["sgst"],
            data["totals"]["cgst"],
            data["totals"]["igst"],
            data["totals"]["round_off"],
            data["totals"]["invoice_total"],
            data["items"],
        )
        self._show_validation(v)

    def _show_validation(self, v: dict):
        conf   = v.get("confidence", "HIGH")
        errors = v.get("errors", [])

        if conf == "HIGH":
            text  = "✓  All math checks passed — data looks correct."
            color = COLORS["ok_green"]
        elif conf == "MEDIUM":
            text  = f"⚠  Minor discrepancy: {errors[0]}" if errors else "⚠  Check values."
            color = COLORS["warn_yellow"]
        else:
            text  = "✗  Issues found:\n" + "\n".join(f"   • {e}" for e in errors)
            color = COLORS["err_red"]

        self._validation_label.config(text=text, fg=color)

    # ── Collect current data from UI ──────────────────────────────────────────

    def _collect_current_data(self) -> dict:
        """Gather edited values from all UI fields into a data dict."""
        def fval(key):
            try:
                raw = self._sv.get(key, tk.StringVar()).get().replace(",", "").strip()
                return float(raw) if raw else 0.0
            except ValueError:
                return 0.0

        items = []
        for row_id in self.tree.get_children():
            vals = self.tree.item(row_id, "values")
            def v(idx):
                raw = vals[idx] if idx < len(vals) else ""
                try:
                    return float(str(raw).replace(",", "")) if raw else None
                except ValueError:
                    return str(raw)

            # ITEM_COLS order: sr(0) desc(1) hsn(2) qty(3) unit(4) rate(5) amount(6)
            #                  sgst_rate(7) sgst_amount(8) cgst_rate(9) cgst_amount(10)
            #                  igst_rate(11) igst_amount(12) total(13)
            items.append({
                "sr":          vals[0] if vals else "",
                "description": vals[1] if len(vals) > 1 else "",
                "hsn":         vals[2] if len(vals) > 2 else "",
                "qty":         v(3),
                "unit":        vals[4] if len(vals) > 4 else "",
                "rate":        v(5),
                "amount":      v(6),
                "sgst_rate":   v(7),
                "sgst_amount": v(8),
                "cgst_rate":   v(9),
                "cgst_amount": v(10),
                "igst_rate":   v(11),
                "igst_amount": v(12),
                "total":       v(13),
            })

        return {
            "invoice": {
                "type":   self._sv.get("bill_type", tk.StringVar()).get(),
                "number": self._sv.get("invoice_no", tk.StringVar()).get(),
                "date":   self._sv.get("invoice_date", tk.StringVar()).get(),
            },
            "seller": {
                "name":    self._sv.get("seller_name", tk.StringVar()).get(),
                "gstin":   self._sv.get("seller_gstin", tk.StringVar()).get(),
                "address": self._sv.get("seller_address", tk.StringVar()).get(),
            },
            "buyer": {
                "name":    self._sv.get("buyer_name", tk.StringVar()).get(),
                "gstin":   self._sv.get("buyer_gstin", tk.StringVar()).get(),
                "address": self._sv.get("buyer_address", tk.StringVar()).get(),
            },
            "items": items,
            "totals": {
                "taxable_amount": fval("taxable"),
                "sgst":           fval("sgst"),
                "cgst":           fval("cgst"),
                "igst":           fval("igst"),
                # GST rate % and total tax are read-only derived fields —
                # carry them forward from the original extraction if available
                "cgst_pct":       (self._data or {}).get("totals", {}).get("cgst_pct"),
                "sgst_pct":       (self._data or {}).get("totals", {}).get("sgst_pct"),
                "igst_pct":       (self._data or {}).get("totals", {}).get("igst_pct"),
                "total_tax":      round(fval("sgst") + fval("cgst") + fval("igst"), 2),
                "round_off":      fval("round_off"),
                "invoice_total":  fval("invoice_total"),
            },
            "validation": self._data.get("validation", {}) if self._data else {},
            "is_scanned": self._data.get("is_scanned", False) if self._data else False,
        }

    # ── Copy to clipboard ─────────────────────────────────────────────────────

    def _copy_to_clipboard(self):
        if not self._data:
            return
        data = self._collect_current_data()
        tsv  = excel_manager.get_tab_separated(data)
        self.root.clipboard_clear()
        self.root.clipboard_append(tsv)
        self._set_status(
            "Table copied to clipboard — press Ctrl+V in Excel to paste.",
            "ok"
        )

    # ── Export to Excel ───────────────────────────────────────────────────────

    def _export_to_excel(self):
        if not self._data:
            return
        data   = self._collect_current_data()
        dialog = ExcelExportDialog(self.root, data)
        self.root.wait_window(dialog)

        if dialog.result is None:
            return  # user cancelled

        filepath, sheet_name, new_sheet, suffix = dialog.result
        ok, msg = excel_manager.export_to_excel(
            filepath, sheet_name, data,
            create_new_sheet=new_sheet,
            new_sheet_suffix=suffix,
        )
        if ok:
            messagebox.showinfo("Export Successful", msg, parent=self.root)
            self._set_status(msg, "ok")
        else:
            messagebox.showerror("Export Failed", msg, parent=self.root)
            self._set_status(msg, "error")

    # ── Flag for fix ──────────────────────────────────────────────────────────

    def _flag_bill(self):
        """
        Save a copy of the loaded PDF/photo plus what was extracted (and any
        corrections made in the UI) into a local folder. The user zips that
        folder and attaches it to a GitHub issue so a new pattern/vendor
        format can be added.
        """
        if not self._pdf_path or not self._data:
            return
        try:
            stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
            base   = os.path.splitext(os.path.basename(self._pdf_path))[0]
            folder = os.path.join(FLAGGED_DIR, f"{stamp}_{base}")
            os.makedirs(folder, exist_ok=True)
            shutil.copy2(self._pdf_path, folder)

            report = {
                "original_extraction": self._data,
                "corrected_by_user":   self._collect_current_data(),
            }
            report_path = os.path.join(folder, "extraction_report.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        except OSError as exc:
            messagebox.showerror("Flag Failed", str(exc), parent=self.root)
            return

        messagebox.showinfo(
            "Bill Flagged",
            f"Saved to:\n{os.path.abspath(folder)}\n\n"
            f"To report it: zip the '{FLAGGED_DIR}' folder and attach it to "
            "a GitHub issue on the project page.",
            parent=self.root,
        )
        self._set_status(f"Flagged — saved to {folder}", "ok")

    # ── Clear all ─────────────────────────────────────────────────────────────

    def _clear_all(self):
        self._pdf_path = None
        self._data     = None
        for sv in self._sv.values():
            sv.set("")
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._validation_label.config(text="", fg="black")
        self._file_label_var.set("No file loaded")
        self._btn_extract.config(state="disabled")
        self._btn_clear.config(state="disabled")
        self._btn_revalidate.config(state="disabled")
        self._btn_copy.config(state="disabled")
        self._btn_export.config(state="disabled")
        self._btn_flag.config(state="disabled")
        self._sv["bill_type"] = tk.StringVar(value="Sales")
        self._set_status("Cleared — load a new PDF or photo.", "normal")

    # ── Status bar ────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, level: str = "normal"):
        colors = {"normal": "#555555", "ok": COLORS["ok_green"],
                  "error": COLORS["err_red"], "warn": COLORS["warn_yellow"]}
        self._status_var.set(f"  {msg}")
        self._status_lbl.config(fg=colors.get(level, "#555555"))

    # ══════════════════════════════════════════════════════════════════════════
    #  RUN
    # ══════════════════════════════════════════════════════════════════════════

    def run(self):
        self.root.mainloop()
