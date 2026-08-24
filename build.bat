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
python -m PyInstaller --noconfirm --onefile --windowed --name "GST-Invoice-Extractor" main.py

echo.
echo Done. Find it at: dist\GST-Invoice-Extractor.exe
pause
