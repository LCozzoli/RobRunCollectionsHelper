<#
.SYNOPSIS
  Builds Collections Helper: tests (optional), the one-file executable, and the installer.

.EXAMPLE
  .\build.ps1                    # executable only  -> dist\RobRunCollections.exe
  .\build.ps1 -Test              # run the tests first
  .\build.ps1 -Version 1.2.0     # also build dist\RobRunCollections-Setup-1.2.0.exe (needs NSIS 3)
#>
param(
    [string]$Version,
    [switch]$Test
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot

# Python to use: the project's virtual environment when there is one, otherwise the one on the PATH.
$python = @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { $python = 'python' }
Write-Host "== Python: $python" -ForegroundColor Cyan

if ($Test) {
    foreach ($script in 'test_detection.py', 'test_folder.py', 'test_interface.py') {
        Write-Host "== tests\$script" -ForegroundColor Cyan
        & $python "tests\$script"
        if ($LASTEXITCODE -ne 0) { throw "tests\$script failed" }
    }
}

# PyInstaller only bundles what the current Python can import: refuse to build an executable without OpenCV etc.
# (Windows PowerShell turns a native command's error output into an exception: check with errors allowed.)
$missing = @()
foreach ($module in 'cv2', 'numpy', 'PIL', 'send2trash', 'PyInstaller') {
    $ErrorActionPreference = 'Continue'
    & $python -c "import $module" 2>&1 | Out-Null
    $failed = $LASTEXITCODE -ne 0
    $ErrorActionPreference = 'Stop'
    if ($failed) { $missing += $module }
}
if ($missing) {
    throw "Missing Python packages ($($missing -join ', ')) in $python. Run:  & $python -m pip install -r requirements.txt"
}

Write-Host '== Building dist\RobRunCollections.exe' -ForegroundColor Cyan
$data = (Resolve-Path 'src\data').Path
$icon = Join-Path $data 'app.ico'
& $python -m PyInstaller --noconfirm --onefile --windowed --name RobRunCollections `
    --add-data "$data;data" --icon "$icon" `
    --distpath dist --workpath build --specpath build src\app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

if ($Version) {
    $makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
    if (-not $makensis) {
        $makensis = @("${env:ProgramFiles(x86)}\NSIS\makensis.exe", "$env:ProgramFiles\NSIS\makensis.exe") |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $makensis) { throw 'NSIS 3 not found. Install it with:  winget install NSIS.NSIS  (or https://nsis.sourceforge.io)' }
    Write-Host "== Building dist\RobRunCollections-Setup-$Version.exe" -ForegroundColor Cyan
    & $makensis "-DVERSION=$Version" 'installer\RobRunCollections.nsi'
    if ($LASTEXITCODE -ne 0) { throw 'makensis failed' }
}
Write-Host 'Done.' -ForegroundColor Green
