# Builds dist\Klipp\Klipp.exe and a release zip, dist\Klipp-<version>-windows.zip
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = if ($env:KLIPP_PYTHON) { $env:KLIPP_PYTHON } else { ".\.venv\Scripts\python.exe" }

New-Item -ItemType Directory -Force assets | Out-Null
& $py -c "import sys; from PySide6.QtWidgets import QApplication; from klipp.icons import app_icon; a = QApplication(sys.argv); app_icon().pixmap(256, 256).save('assets/icon.ico')"
& $py -m PyInstaller --noconfirm --windowed --name Klipp --icon assets\icon.ico `
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
    run.pyw
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# PyInstaller bundles Qt parts Klipp never loads. Dropping them roughly halves the download.
$qt = "dist\Klipp\_internal\PySide6"
$unused = @(
    "opengl32sw.dll",                         # software OpenGL fallback; Klipp doesn't use OpenGL
    "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll", "Qt6QmlWorkerScript.dll",
    "Qt6Pdf.dll", "Qt6Network.dll", "Qt6OpenGL.dll", "Qt6Svg.dll", "Qt6VirtualKeyboard.dll",
    "translations",                           # Qt's own UI translations; Klipp is English-only
    "plugins\platforminputcontexts",          # virtual keyboard, pulls in Qt Quick
    "plugins\generic", "plugins\iconengines",
    "plugins\platforms\qdirect2d.dll", "plugins\platforms\qminimal.dll", "plugins\platforms\qoffscreen.dll"
)
foreach ($item in $unused) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $qt $item) }
# Keep only the image formats Klipp reads or writes (PNG is built in).
Get-ChildItem "$qt\plugins\imageformats" | Where-Object { $_.Name -notin @("qjpeg.dll", "qico.dll") } | Remove-Item -Force

$version = (& $py -c "import klipp; print(klipp.__version__)").Trim()
$zip = "dist\Klipp-$version-windows.zip"
Remove-Item -Force -ErrorAction SilentlyContinue $zip
Compress-Archive -Path dist\Klipp -DestinationPath $zip
"Built $zip ({0:N1} MB)" -f ((Get-Item $zip).Length / 1MB)
