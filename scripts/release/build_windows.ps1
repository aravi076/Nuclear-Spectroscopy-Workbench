# Build the NSW Windows application folder and installer from the locked environment.
#
#   powershell -ExecutionPolicy Bypass -File scripts\release\build_windows.ps1
#
# Steps: uv sync --locked, tests are NOT run here (run them before), generate the
# synthetic demo spectra, collect licences,
# PyInstaller one-folder build, self-test of the built folder, Inno Setup installer,
# SHA-256 checksums. Output: build\dist\NSW, build\installer\NSW-Setup-<version>-x64.exe,
# build\installer\SHA256SUMS.txt.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

uv sync --locked --all-extras --inexact
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
$version = & $py -c "import nsw; print(nsw.__version__)"
Write-Host "NSW version $version"

# demo spectra for the installer: generated (git-ignored), always fresh and synthetic
& $py scripts\data\generate_synthetic.py
if ($LASTEXITCODE -ne 0) { throw "generating the synthetic demo spectra failed" }
& $py scripts\release\collect_licenses.py build\licenses
if ($LASTEXITCODE -ne 0) { throw "licence collection failed" }
& $py -m PyInstaller packaging\windows\NSW.spec --noconfirm --distpath build\dist --workpath build\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$selftest = Join-Path $env:TEMP ("nsw-build-selftest-" + [guid]::NewGuid().ToString("N"))
$p = Start-Process -FilePath "build\dist\NSW\NSW.exe" -ArgumentList "--self-test", "`"$selftest`"" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "self-test of the built folder failed (see $selftest\selftest.log)" }
Write-Host (Get-Content (Join-Path $selftest "selftest.log") -Tail 1)

$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$version" packaging\windows\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = Get-Item "build\installer\NSW-Setup-$version-x64.exe"
$hash = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash.ToLower()
"$hash  $($setup.Name)" | Out-File -Encoding ascii "build\installer\SHA256SUMS.txt"
Write-Host "Installer: $($setup.FullName) ($([math]::Round($setup.Length / 1MB, 1)) MB)"
Write-Host "SHA-256:   $hash"
