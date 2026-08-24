"""
GST Invoice Extractor — Entry Point
Run: python main.py
"""

import sys
import os

# Ensure Tesseract is findable on Windows (default install path)
_TESSERACT_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(_TESSERACT_DEFAULT):
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_DEFAULT
    except ImportError:
        pass  # Tesseract not installed — OCR simply won't be available


def main():
    try:
        from app import InvoiceApp
    except ImportError as e:
        print(f"\nMissing dependency: {e}")
        print("Please run: install.bat  (or: pip install -r requirements.txt)\n")
        sys.exit(1)

    InvoiceApp().run()


if __name__ == "__main__":
    main()
