# Builds dist\Klipp\Klipp.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force assets | Out-Null
& $py -c "import sys; from PySide6.QtWidgets import QApplication; from klipp.icons import app_icon; a = QApplication(sys.argv); app_icon().pixmap(256, 256).save('assets/icon.ico')"
& $py -m PyInstaller --noconfirm --windowed --name Klipp --icon assets\icon.ico `
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
    run.pyw
