# Builds dist\TrackPay\TrackPay.exe (UI build + PyInstaller). Run from the project root.
$ErrorActionPreference = "Stop"
Push-Location ui; npm install; npm run build; Pop-Location
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --name TrackPay `
    --add-data "ui/dist;ui/dist" --hidden-import xlrd main.py
Write-Host "Done: dist\TrackPay\TrackPay.exe"
