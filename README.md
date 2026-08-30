# GST Invoice Extractor

A free, offline desktop tool that reads Indian GST invoice PDFs (and phone-photo bills) and turns them into clean, verified Excel rows — no typing, no cloud, no subscription.

## Why this exists

If you run a small business in India, you know the drill: a folder full of GST invoices in a dozen different layouts — Tally exports, custom vendor formats, scanned bills, phone photos of paper bills — and someone has to type every line item into Excel for accounting. This tool does that in a few seconds per bill, and shows you every extracted value before it touches your spreadsheet so you can catch mistakes instead of finding them during a GST return.

## What it does

- Reads **digital PDFs** (pdfplumber) and **scanned/photographed bills** (Tesseract OCR) — same workflow either way
- Pulls out seller & buyer name/GSTIN/address, invoice number & date, every line item (HSN, qty, rate, GST breakdown), and invoice totals
- Cross-checks the math (do the item amounts add up to the taxable value? does taxable + tax = total?) and flags anything that doesn't
- Lets you review and edit any field before saving — nothing goes to Excel without your say-so
- Exports to Excel with one sheet per party, appending new invoices as rows, and highlighting low-confidence rows in yellow
- Runs entirely on your machine — no internet connection needed, no data ever leaves your PC. Works fine on a low-end laptop (no GPU required)

## Download

**[⬇ Download for Windows](../../releases/latest)** — grab the `.zip` from the latest release, extract it, and run `GST-Invoice-Extractor.exe` inside. No Python or setup required.

If you'll be processing **scanned PDFs or phone photos**, also install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (digital PDFs work fine without it).

> **First run:** Windows will likely show a blue "Windows protected your PC" (SmartScreen) screen the first time you open it — that's normal for any small, unsigned app, not a sign of a problem. Click **More info → Run anyway**. If Windows Defender flags or deletes the file entirely (not just a SmartScreen warning), see [Antivirus false positives](#antivirus-false-positives) below.

<details>
<summary>Running from source instead (for developers)</summary>

**Requirements:** Windows, Python 3.8+

```bat
install.bat
python main.py
```

`install.bat` installs the Python packages. To build your own `.exe`, run `build.bat`.

</details>

## How to use it

1. **Load File** — pick a PDF or a photo (JPG/PNG) of a bill
2. **Extract** — the tool reads it and fills in every field it can find
3. **Review** — check the header fields and double-click any line-item cell to fix it; a confidence badge (HIGH / MEDIUM / LOW) tells you how much to trust the result
4. **Export to Excel** — pick a file and sheet (or create new ones); the invoice is appended as a row per line item

## How extraction works

There's no AI model here — it's a set of hand-written pattern rules, one per invoice layout, tried in order from most specific to most general. That's deliberate: it makes the tool fast and predictable on ordinary hardware, and it means **every new vendor format just needs one example bill** to support — not a training dataset.

## Known limitations

Being upfront about where this still struggles:

- **No GSTIN, no anchored address** — some buyers (a local plumber, a cash customer) don't have a GSTIN, and a lot of the address-matching logic anchors on it. You may need to type the address in by hand.
- **Badly scanned/blurry photos** — if the OCR engine mangles a name (`OMEGA PLAS LIMITED` → `OMEGA PLAS AMITED`), no pattern can un-mangle it. Review OCR'd bills a little more carefully.
- **Layouts the tool hasn't seen yet** — a brand-new vendor template may extract poorly or not at all until a pattern is added for it (see below).

## How you can help improve it

The single most useful thing you can do is send in the bills that extract badly — that's literally how every existing format was added.

1. Load the problem bill and hit **Extract**
2. Fix whatever's wrong directly in the UI (name, address, an item row — anything)
3. Click **🚩 Flag for Fix** — this saves a copy of the bill plus your corrections into a local `flagged_bills/` folder
4. Zip that folder and [open an issue](https://github.com/daksh692/GST-Bill-Extract/issues) with it attached, describing what was wrong

That gives a maintainer everything needed to write a new pattern: the exact bill layout and what the correct values should have been — no guesswork.

Other ways to help: report bugs, suggest formats you'd like supported, or open a PR if you're comfortable with Python and regex — the pattern lists live in `extractor.py` and are grouped by field (invoice number, dates, names, addresses, line items).

## Antivirus false positives

Some antivirus tools — including Windows Defender — may flag this app as something like `Trojan:Win32/Wacatac.B!ml`. The `!ml` suffix means it's a **machine-learning heuristic guess, not a real detection** — it's a well-known false positive that hits small, unsigned Python apps built with PyInstaller, because the tool that packages Python apps into a `.exe` uses the same kind of self-extracting behaviour that real malware droppers use. It is **not actually a virus**; the source code is public in this repo for anyone to check.

If it happens to you:
- If Defender only shows a warning, choose "Allow on device" / restore the file from quarantine.
- If you want independent confirmation before trusting that, you can scan the downloaded file on [VirusTotal](https://www.virustotal.com/) — expect a small number of heuristic engines to flag it and the well-known antivirus engines to show it as clean.
- If it keeps getting deleted, add an exclusion for the extracted folder in Windows Security → Virus & threat protection → Manage settings → Exclusions.

This is a limitation of not having a paid code-signing certificate, not of the app itself — will be resolved if/when the project gets one.

## Privacy

Your invoices never leave your machine. There's no server, no API calls, no telemetry. The only thing that leaves your PC is what you explicitly zip up and attach to a GitHub issue yourself.
