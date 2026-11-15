#Requires -Version 5.1
<#
    okww-suite setup
    -----------------
    One-time configuration: switch ok-ww from PostMessage to PyDirect (real foreground).

    Usage:
        powershell -ExecutionPolicy Bypass -File setup.ps1
        powershell -ExecutionPolicy Bypass -File setup.ps1 -Check      # report only

    Exit codes: 0 ok / 1 not found / 2 write failed
#>
param(
    [string]$OkwwRoot = "$env:LOCALAPPDATA\ok-ww",
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$ASCII = $true   # keep every console line ASCII-safe

function Say([string]$m) { Write-Host $m }

$work = Join-Path $OkwwRoot 'data\apps\ok-ww\working'
$cfgP = Join-Path $work 'config.py'
$devP = Join-Path $work 'configs\devices.json'

Say '=== okww-suite setup ==='

# ---- 1. locate ------------------------------------------------------------
if (-not (Test-Path $cfgP)) {
    Say "NOT FOUND: $cfgP"
    Say 'Point -OkwwRoot at the ok-ww install dir (the one containing data\apps\ok-ww).'
    exit 1
}
Say "ok-ww   : $OkwwRoot"
Say "config  : $cfgP"
Say "devices : $devP"

# ---- 2. read current values ----------------------------------------------
$cfgText = [IO.File]::ReadAllText($cfgP)
$devText = ''
if (Test-Path $devP) { $devText = [IO.File]::ReadAllText($devP) }

$rx = "(?<pre>['""]interaction['""]\s*:\s*)(?<q>['""])(?<val>[^'""]*)\k<q>"

$cfgM = [regex]::Match($cfgText, $rx)
$devM = [regex]::Match($devText, $rx)

$cfgVal = '(absent)'
if ($cfgM.Success) { $cfgVal = $cfgM.Groups['val'].Value }
$devVal = '(no file)'
if ($devM.Success) { $devVal = $devM.Groups['val'].Value }

Say ''
Say 'current:'
Say "  config.py    interaction = $cfgVal"
Say "  devices.json interaction = $devVal"

$needCfg = ($cfgVal -ne 'PyDirect')
$needDev = ($devM.Success -and $devVal -ne 'PyDirect')

if (-not $needCfg -and -not $needDev) {
    Say ''
    Say 'ALREADY OK - both are PyDirect. Nothing to do.'
    exit 0
}

if ($Check) {
    Say ''
    Say 'CHECK ONLY - would change:'
    if ($needCfg) { Say '  config.py    -> PyDirect' }
    if ($needDev) { Say '  devices.json -> PyDirect' }
    exit 0
}

# ---- 3. backup ------------------------------------------------------------
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$bak = Join-Path $OkwwRoot "_backup_$stamp"
New-Item -ItemType Directory -Path $bak -Force | Out-Null
Copy-Item $cfgP (Join-Path $bak 'config.py.orig') -Force
if (Test-Path $devP) { Copy-Item $devP (Join-Path $bak 'devices.json.orig') -Force }
Say ''
Say "backup  : $bak"

# ---- 4. rewrite -----------------------------------------------------------
function Set-Interaction([string]$text, [string]$value) {
    return [regex]::Replace($text, $rx, {
        param($m)
        $q = $m.Groups['q'].Value
        return $m.Groups['pre'].Value + $q + $value + $q
    })
}

try {
    if ($needCfg) {
        $new = Set-Interaction $cfgText 'PyDirect'
        [IO.File]::WriteAllText($cfgP, $new, (New-Object Text.UTF8Encoding $false))
        Say 'wrote   : config.py    -> PyDirect'
    }
    if ($needDev) {
        $new = Set-Interaction $devText 'PyDirect'
        [IO.File]::WriteAllText($devP, $new, (New-Object Text.UTF8Encoding $false))
        Say 'wrote   : devices.json -> PyDirect'
    }
} catch {
    Say "WRITE FAILED: $($_.Exception.Message)"
    Say "Restore from: $bak"
    exit 2
}

# ---- 5. verify ------------------------------------------------------------
Say ''
Say 'verify:'
$cfg2 = [regex]::Match([IO.File]::ReadAllText($cfgP), $rx)
$dev2 = [regex]::Match([IO.File]::ReadAllText($devP), $rx)
$ok = $true
if ($cfg2.Success -and $cfg2.Groups['val'].Value -eq 'PyDirect') {
    Say '  config.py    OK'
} else { Say '  config.py    FAILED'; $ok = $false }
if ($dev2.Success -and $dev2.Groups['val'].Value -eq 'PyDirect') {
    Say '  devices.json OK'
} else { Say '  devices.json FAILED'; $ok = $false }

if (-not $ok) { exit 2 }

Say ''
Say 'DONE. Now run run.bat as administrator.'
Say ''
Say 'NOTE: ok-ww auto-update resets config.py (it lives in repo/).'
Say '      If the top bar stops showing PyDirect, run setup.ps1 again.'
exit 0
