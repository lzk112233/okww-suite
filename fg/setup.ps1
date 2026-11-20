#Requires -Version 5.1
<#
    okww-suite setup
    -----------------
    One-time configuration:
      1. switch ok-ww from PostMessage to PyDirect (real foreground input)
      2. optionally register two scheduled tasks so run.bat never asks for UAC again

    Usage:
        powershell -ExecutionPolicy Bypass -File setup.ps1              # configure
        powershell -ExecutionPolicy Bypass -File setup.ps1 -Check       # report only
        powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallTask # + no-UAC launcher
        powershell -ExecutionPolicy Bypass -File setup.ps1 -RemoveTask  # undo the tasks

    -InstallTask / -RemoveTask need an elevated shell (the tasks run at Highest).

    Exit codes: 0 ok / 1 not found / 2 write failed
#>
param(
    [string]$OkwwRoot = "$env:LOCALAPPDATA\ok-ww",
    [switch]$Check,
    [switch]$InstallTask,
    [switch]$RemoveTask
)

$ErrorActionPreference = 'Stop'

function Say([string]$m) { Write-Host $m }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

$work = Join-Path $OkwwRoot 'data\apps\ok-ww\working'
$py   = Join-Path $OkwwRoot 'data\apps\ok-ww\python\python.exe'
$cfgP = Join-Path $work 'config.py'
$devP = Join-Path $work 'configs\devices.json'

$TASK_GUI   = 'okww-suite-gui'
$TASK_DAILY = 'okww-suite-daily'

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

# ---- 2. remove tasks ------------------------------------------------------
if ($RemoveTask) {
    Say ''
    if (-not (Test-Admin)) {
        Say 'Need an elevated shell for -RemoveTask.'
        exit 1
    }
    foreach ($t in @($TASK_GUI, $TASK_DAILY)) {
        $exists = $null -ne (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue)
        if ($exists) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Say "removed task $t"
        } else {
            Say "task $t was not present"
        }
    }
    exit 0
}

# ---- 3. interaction method ------------------------------------------------
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
Say 'interaction method:'
Say "  config.py    = $cfgVal"
Say "  devices.json = $devVal"

$needCfg = ($cfgM.Success -and $cfgVal -ne 'PyDirect')
$needDev = ($devM.Success -and $devVal -ne 'PyDirect')

if (-not $needCfg -and -not $needDev) {
    Say '  already PyDirect, nothing to change'
} elseif ($Check) {
    Say '  CHECK ONLY - would change:'
    if ($needCfg) { Say '    config.py    -> PyDirect' }
    if ($needDev) { Say '    devices.json -> PyDirect' }
} else {
    # backup
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $bak = Join-Path $OkwwRoot "_backup_$stamp"
    New-Item -ItemType Directory -Path $bak -Force | Out-Null
    Copy-Item $cfgP (Join-Path $bak 'config.py.orig') -Force
    if (Test-Path $devP) { Copy-Item $devP (Join-Path $bak 'devices.json.orig') -Force }
    Say "  backup -> $bak"

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
            Say '  wrote config.py    -> PyDirect'
        }
        if ($needDev) {
            $new = Set-Interaction $devText 'PyDirect'
            [IO.File]::WriteAllText($devP, $new, (New-Object Text.UTF8Encoding $false))
            Say '  wrote devices.json -> PyDirect'
        }
    } catch {
        Say "WRITE FAILED: $($_.Exception.Message)"
        Say "Restore from: $bak"
        exit 2
    }

    # verify
    Say ''
    Say 'verify:'
    $cfg2 = [regex]::Match([IO.File]::ReadAllText($cfgP), $rx)
    $dev2 = [regex]::Match([IO.File]::ReadAllText($devP), $rx)
    $ok = $true
    if ($cfg2.Success -and $cfg2.Groups['val'].Value -eq 'PyDirect') {
        Say '  config.py    OK'
    } else { Say '  config.py    FAILED'; $ok = $false }
    if (-not (Test-Path $devP)) {
        Say '  devices.json (absent, fine)'
    } elseif ($dev2.Success -and $dev2.Groups['val'].Value -eq 'PyDirect') {
        Say '  devices.json OK'
    } else { Say '  devices.json FAILED'; $ok = $false }

    if (-not $ok) { exit 2 }
}

# ---- 4. scheduled tasks (optional, removes the UAC prompt) ----------------
if ($InstallTask) {
    Say ''
    Say 'scheduled tasks:'
    if (-not (Test-Admin)) {
        Say '  Need an elevated shell for -InstallTask.'
        Say '  Right-click PowerShell -> Run as administrator, then run this again.'
        exit 1
    }
    if (-not (Test-Path $py)) {
        Say "  NOT FOUND: $py"
        exit 1
    }

    function Install-One([string]$name, [string]$arguments) {
        $act = New-ScheduledTaskAction -Execute $py -Argument $arguments -WorkingDirectory $work
        $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddYears(10)
        $sty = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew `
            -StartWhenAvailable
        $prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg `
            -Settings $sty -Principal $prn -Force | Out-Null
        Say "  registered $name"
    }

    try {
        Install-One $TASK_GUI   'main.py'
        Install-One $TASK_DAILY 'main.py -t DailyTask'
    } catch {
        Say "  FAILED: $($_.Exception.Message)"
        exit 2
    }

    Say '  run.bat will now start ok-ww through these tasks, with no UAC prompt.'
} else {
    $have = @()
    foreach ($t in @($TASK_GUI, $TASK_DAILY)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { $have += $t }
    }
    Say ''
    if ($have.Count -gt 0) {
        Say ('scheduled tasks: ' + ($have -join ', ') + '  (run.bat uses them, no UAC)')
    } else {
        Say 'scheduled tasks: none'
        Say '  run.bat will ask for UAC every time.'
        Say '  Run setup.ps1 -InstallTask once, as administrator, to stop that.'
    }
}

Say ''
Say 'DONE.'
Say '  Next: double-click run.bat (or run.bat daily).'
Say ''
Say 'NOTE: ok-ww auto-update rewrites config.py (it lives in repo/).'
Say '      If the top bar stops showing PyDirect, run setup.ps1 again.'
exit 0
