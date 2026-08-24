"""
PDF/image text and table extraction.
Strategy: pdfplumber first (digital PDFs), Tesseract OCR fallback (scanned).
Also supports JPG/PNG phone photos directly via extract_from_image().
Designed for low CPU usage on Intel i3 — no ML models loaded.
"""

import io
import os
import pdfplumber


# ── Table extraction strategies to try in order ──────────────────────────────
_TABLE_STRATEGIES = [
    {"vertical_strategy": "lines",        "horizontal_strategy": "lines"},
    {"vertical_strategy": "lines_strict", "horizontal_strategy": "lines_strict"},
    {"vertical_strategy": "lines",        "horizontal_strategy": "text"},
    {"vertical_strategy": "text",         "horizontal_strategy": "text"},
]


def extract_from_pdf(pdf_path: str) -> dict:
    """
    Main entry point. Tries digital extraction first, OCR fallback if needed.

    Returns:
        {
            'text':      str   — full page text joined with newlines,
            'tables':    list  — list of tables (each table is list of rows),
            'is_scanned': bool,
            'page_count': int,
            'error':     str or None
        }
    """
    result = _try_digital(pdf_path)

    if result.get("error"):
        return result

    word_count = len(result["text"].split())
    if word_count < 30:
        # Very little text extracted → probably a scanned PDF
        ocr_result = _try_ocr(pdf_path)
        if ocr_result["text"].strip():
            return ocr_result

    return result


# ── Digital (pdfplumber) ──────────────────────────────────────────────────────

def _try_digital(pdf_path: str) -> dict:
    text_parts = []
    all_tables = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)

            for page in pdf.pages:
                # Text extraction
                text = page.extract_text(x_tolerance=3, y_tolerance=3)
                if text:
                    text_parts.append(text)

                # Table extraction: try multiple strategies
                for strategy in _TABLE_STRATEGIES:
                    try:
                        tables = page.extract_tables(strategy)
                        if tables and any(len(t) > 1 for t in tables):
                            all_tables.extend(tables)
                            break
                    except Exception:
                        continue

        return {
            "text":       "\n".join(text_parts),
            "tables":     all_tables,
            "is_scanned": False,
            "page_count": page_count,
            "error":      None,
        }

    except Exception as exc:
        return {
            "text": "", "tables": [], "is_scanned": False,
            "page_count": 0, "error": str(exc),
        }


# ── OCR (Tesseract via PyMuPDF) ───────────────────────────────────────────────

def _try_ocr(pdf_path: str) -> dict:
    """
    Render each page to a 300 DPI image and run Tesseract.
    300 DPI gives good accuracy for typical invoice fonts without
    being too heavy for an i3 CPU (~5-15 sec per page).
    """
    try:
        import fitz                        # PyMuPDF
        import pytesseract
        from PIL import Image, ImageFilter
    except ImportError as exc:
        return {
            "text": "", "tables": [], "is_scanned": True,
            "page_count": 0,
            "error": (
                f"OCR libraries missing ({exc}). "
                "Run install.bat and install Tesseract from "
                "https://github.com/UB-Mannheim/tesseract/wiki"
            ),
        }

    try:
        doc = fitz.open(pdf_path)
        pages_text = []
        dpi_scale = 300 / 72  # 300 DPI

        for page in doc:
            mat = fitz.Matrix(dpi_scale, dpi_scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # Grayscale + mild sharpening improves OCR on low-contrast scans
            img = img.convert("L")
            img = img.filter(ImageFilter.SHARPEN)

            text = pytesseract.image_to_string(
                img, lang="eng",
                config="--psm 6 --oem 3"
            )
            pages_text.append(text)

        doc.close()
        return {
            "text":       "\n".join(pages_text),
            "tables":     [],
            "is_scanned": True,
            "page_count": len(pages_text),
            "error":      None,
        }

    except Exception as exc:
        return {
            "text": "", "tables": [], "is_scanned": True,
            "page_count": 0, "error": str(exc),
        }


# ── Image (phone photo) ───────────────────────────────────────────────────────

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTS


def extract_from_image(image_path: str) -> dict:
    """
    Run Tesseract OCR directly on a phone photo (JPG/PNG/etc.).

    Pre-processing steps tuned for camera photos of printed invoices:
      1. Grayscale — removes colour noise from uneven lighting
      2. Contrast boost (2×) — phone photos often have low local contrast
      3. Sharpen — recovers edge detail lost by camera optics
    """
    try:
        import pytesseract
        from PIL import Image, ImageFilter, ImageEnhance
    except ImportError as exc:
        return {
            "text": "", "tables": [], "is_scanned": True,
            "page_count": 0,
            "error": (
                f"OCR libraries missing ({exc}). "
                "Run install.bat and install Tesseract from "
                "https://github.com/UB-Mannheim/tesseract/wiki"
            ),
        }

    try:
        img = Image.open(image_path)

        # Resize very large photos down to ~2400px on the long edge so Tesseract
        # doesn't time out on 12-MP phone shots — text is still legible at this size.
        max_dim = 2400
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        img = img.convert("L")                              # grayscale
        img = ImageEnhance.Contrast(img).enhance(2.0)      # boost contrast
        img = img.filter(ImageFilter.SHARPEN)               # sharpen edges

        text = pytesseract.image_to_string(
            img, lang="eng",
            config="--psm 6 --oem 3"
        )
        return {
            "text":       text,
            "tables":     [],
            "is_scanned": True,
            "page_count": 1,
            "error":      None,
        }

    except Exception as exc:
        return {
            "text": "", "tables": [], "is_scanned": True,
            "page_count": 0, "error": str(exc),
        }
