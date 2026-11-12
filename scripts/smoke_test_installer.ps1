# Install, self-test, upgrade-reinstall and uninstall an NSW installer silently.
#
#   powershell -ExecutionPolicy Bypass -File scripts\smoke_test_installer.ps1 NSW-Setup-<v>-x64.exe [-CleanMachine]
#
# Uses a throw-away install folder under %TEMP%, never the user's real installation
# folder, and a marker file in %LOCALAPPDATA%\NSW to prove user data survives. Prints
# PASS/FAIL per check and exits non-zero on any failure. On a clean machine (no Python,
# Git or uv) this is the acceptance test of docs/WINDOWS_INSTALLER_VALIDATION.md.
param([Parameter(Mandatory = $true)][string]$Installer, [switch]$CleanMachine)
$ErrorActionPreference = "Stop"
$failed = 0
function Check($name, $ok, $detail = "") {
    if ($ok) { Write-Host "PASS  $name $detail" } else { Write-Host "FAIL  $name $detail"; $script:failed++ }
}

$work = Join-Path $env:TEMP ("nsw-smoke-" + [guid]::NewGuid().ToString("N"))
$dir = Join-Path $work "NSW"
$selftest = Join-Path $work "selftest"
New-Item -ItemType Directory -Force $work | Out-Null
$userData = Join-Path $env:LOCALAPPDATA "NSW"
New-Item -ItemType Directory -Force $userData | Out-Null
$marker = Join-Path $userData "smoke-test-marker.txt"
"user data written before installation" | Out-File -Encoding utf8 $marker

$noPython = -not (Get-Command python -ErrorAction SilentlyContinue)
if ($CleanMachine) { Check "no Python on PATH (clean machine)" $noPython }
else { Write-Host ("INFO  Python on PATH: " + (-not $noPython) + " (use -CleanMachine on the test machine)") }

$args1 = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$dir`"", "/LOG=`"$work\install.log`"")
$p = Start-Process -FilePath $Installer -ArgumentList $args1 -Wait -PassThru
Check "installation exit code" ($p.ExitCode -eq 0) "($($p.ExitCode))"
Check "NSW.exe installed" (Test-Path (Join-Path $dir "NSW.exe"))
Check "licences installed" (Test-Path (Join-Path $dir "_internal\licenses\LGPL-3.0.txt"))
Check "demo spectra installed" (Test-Path (Join-Path $dir "Demo spectra (synthetic)\sources\syn-cal-source.nswsyn.json"))

if (-not (Test-Path (Join-Path $dir "NSW.exe"))) {
    Write-Host "STOP  nothing installed; see $work\install.log"
    exit ($failed + 1)
}
$t0 = Get-Date
$p = Start-Process -FilePath (Join-Path $dir "NSW.exe") -ArgumentList "--self-test", "`"$selftest`"" -Wait -PassThru
$seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
$log = Join-Path $selftest "selftest.log"
$last = if (Test-Path $log) { Get-Content $log -Tail 1 } else { "no log" }
Check "self-test of the installed program" ($p.ExitCode -eq 0) "($last; $seconds s)"

$p = Start-Process -FilePath $Installer -ArgumentList $args1 -Wait -PassThru
Check "reinstall over the existing installation (upgrade path)" ($p.ExitCode -eq 0)

$uninstaller = Join-Path $dir "unins000.exe"
$p = Start-Process -FilePath $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
Start-Sleep -Seconds 2
Check "uninstall exit code" ($p.ExitCode -eq 0) "($($p.ExitCode))"
Check "program files removed" (-not (Test-Path (Join-Path $dir "NSW.exe")))
Check "user data kept" (Test-Path $marker)
Check "self-test output kept" (Test-Path $log)

Remove-Item $marker -Force
if ($failed -eq 0) { Write-Host "ALL CHECKS PASSED ($work)" } else { Write-Host "$failed CHECK(S) FAILED ($work)" }
exit $failed
