# One command for a new computer or server (Windows): virtual environment, packages, settings and the first sign-in.
#   .\setup.ps1                     (extra words go to the setup command, e.g. .\setup.ps1 --ask)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Making the virtual environment (.venv)...'
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv } else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is needed: https://www.python.org/downloads/' }
}
Write-Host 'Installing the packages...'
& $python -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'The packages could not be installed (see above).' }
& $python -m backend.app.setup @args
exit $LASTEXITCODE
