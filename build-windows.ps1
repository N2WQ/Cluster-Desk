param([string]$OutputDirectory = 'dist')
$ErrorActionPreference = 'Stop'
$BuildPython = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
$IconPath = Join-Path $PSScriptRoot 'assets\clusterdesk.ico'
if (-not (Test-Path -LiteralPath $BuildPython)) {
    throw 'Build environment is missing. Run: python -m venv .build-venv'
}
Push-Location $PSScriptRoot
try {
    & $BuildPython -m PyInstaller --noconfirm --onefile --windowed --name ClusterDesk --icon $IconPath --add-data "${IconPath};assets" --distpath $OutputDirectory --workpath build\pyinstaller --specpath build app.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
