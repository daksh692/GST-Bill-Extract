@echo off
:: Builds a standalone Windows .exe (dist\GST-Invoice-Extractor.exe) that
:: users can run without installing Python.
::
:: Uses its own isolated virtual environment so unrelated packages on this
:: machine don't get bundled into the exe and bloat it.

echo Creating isolated build environment...
python -m venv build_venv
call build_venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller

echo Building exe...
:: --onedir (not --onefile) and --noupx: --onefile self-extracts to a temp
:: folder on every launch, and UPX compression is heavily associated with
:: malware packers -- both are the #1 triggers for Windows Defender flagging
:: PyInstaller apps as "Trojan:Win32/Wacatac...!ml" (a heuristic false
:: positive, not a real detection). --onedir avoids that pattern entirely.
python -m PyInstaller --noconfirm --onedir --noupx --windowed --name "GST-Invoice-Extractor" main.py

echo.
echo Done. Find it at: dist\GST-Invoice-Extractor\GST-Invoice-Extractor.exe
echo Zip the whole "GST-Invoice-Extractor" folder before distributing it.
pause
