# stealth_launch.ps1 -- ASCII only on purpose.
# Windows PowerShell 5.1 reads .ps1 as ANSI; non-ASCII comments break parsing.
#
# Launches ok-ww through the okww_shim cloak layer:
#   * PYTHONPATH  -> shim dir (sitecustomize auto-installs the shim)
#   * log redirect -> %TEMP%\.<rand>
#   * update/stat DNS blocked
#   * window title randomised / hidden / offscreen
#   * input routed to the HID bridge or Interception driver
#   * optional: run from a random-named copy under LOCALAPPDATA
#   * optional: parent process = explorer.exe (no python-under-terminal look)
#
# Examples
#   .\stealth_launch.ps1 -OkwwPath D:\tools\ok-ww
#   .\stealth_launch.ps1 -OkwwPath D:\tools\ok-ww -Mode ghost -Backend hid
#   .\stealth_launch.ps1 -OkwwPath D:\tools\ok-ww -WorkCopy -BlockHosts -WipeTraces

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OkwwPath,
    [ValidateSet('gui', 'hidden', 'ghost')][string]$Mode = 'hidden',
    [ValidateSet('hid', 'interception', 'sendinput')][string]$Backend = 'hid',
    [string]$PythonExe = '',
    [string]$Entry = '-m ok',
    [switch]$WorkCopy,
    [switch]$BlockHosts,
    [switch]$WipeTraces,
    [switch]$NoNetBlock,
    [int]$HidPort = 0
)

$ErrorActionPreference = 'Stop'
$ShimDir = Split-Path -Parent $PSCommandPath
$OkwwPath = (Resolve-Path $OkwwPath).Path

function Write-Step($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[!] $msg" -ForegroundColor Yellow }

# ---------- 1. resolve python ----------
if (-not $PythonExe) {
    $cands = @(
        (Join-Path $OkwwPath 'python\pythonw.exe'),
        (Join-Path $OkwwPath '.venv\Scripts\pythonw.exe'),
        (Join-Path $OkwwPath 'venv\Scripts\pythonw.exe'),
        (Join-Path $OkwwPath 'python.exe')
    )
    foreach ($c in $cands) { if (Test-Path $c) { $PythonExe = $c; break } }
}
if (-not $PythonExe) {
    $p = (Get-Command python.exe -ErrorAction SilentlyContinue)
    if ($p) {
        $pw = Join-Path (Split-Path $p.Source) 'pythonw.exe'
        $PythonExe = if (Test-Path $pw) { $pw } else { $p.Source }
    }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "python not found. pass -PythonExe C:\path\pythonw.exe"
}
Write-Ok "python = $PythonExe"

# ---------- 2. optional working copy with a neutral name ----------
$RunDir = $OkwwPath
if ($WorkCopy) {
    $base = Join-Path $env:LOCALAPPDATA 'Microsoft\DeviceSync'
    $leaf = -join ((48..57) + (65..90) | Get-Random -Count 8 | ForEach-Object { [char]$_ })
    $RunDir = Join-Path $base $leaf
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    Write-Step "copying payload -> $RunDir"
    # robocopy is quiet and fast; /XD skips logs, caches, and the .git dir
    $null = robocopy $OkwwPath $RunDir /E /NFL /NDL /NJH /NJS /NP /XD log logs .git __pycache__ .venv venv
    Write-Ok "payload copied"
}

# ---------- 3. environment ----------
$env:PYTHONPATH = $ShimDir + $(if ($env:PYTHONPATH) { ';' + $env:PYTHONPATH } else { '' })
$env:PYTHONUTF8 = '1'
$env:OKWW_SHIM = '1'
$env:OKWW_CLOAK = $Mode
$env:OKWW_INPUT_BACKEND = $Backend
$env:OKWW_BLOCK_NET = if ($NoNetBlock) { '0' } else { '1' }
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:DO_NOT_TRACK = '1'
if ($Mode -eq 'ghost') { $env:QT_QPA_PLATFORM = 'offscreen' }

Write-Step "mode=$Mode backend=$Backend cwd=$RunDir"

# ---------- 4. optional host block ----------
if ($BlockHosts) {
    $hosts = Join-Path $env:WINDIR 'System32\drivers\etc\hosts'
    $marker = '# okww-cloak'
    $domains = @('ok-script.com', 'api.ok-script.com', 'update.ok-script.com',
                 'ok-ww.com', 'raw.githubusercontent.com', 'objects.githubusercontent.com')
    try {
        $txt = Get-Content $hosts -Raw -ErrorAction Stop
        $lines = @()
        foreach ($d in $domains) {
            if ($txt -notmatch [regex]::Escape("0.0.0.0 $d")) {
                $lines += "0.0.0.0 $d $marker"
                $lines += "0.0.0.0 www.$d $marker"
            }
        }
        if ($lines.Count) {
            Add-Content -Path $hosts -Value $lines -Encoding ASCII
            Write-Ok "hosts blocked ($($lines.Count) entries)"
        } else {
            Write-Ok 'hosts already blocked'
        }
    } catch {
        Write-Warn2 "hosts write skipped (needs admin): $($_.Exception.Message)"
    }
}

# ---------- 5. trace wipe ----------
if ($WipeTraces) {
    $targets = @(
        (Join-Path $env:USERPROFILE 'ok-ww'),
        (Join-Path $env:USERPROFILE '.ok-ww'),
        (Join-Path $env:APPDATA 'ok-ww'),
        (Join-Path $RunDir 'log'),
        (Join-Path $RunDir 'logs'),
        (Join-Path $RunDir 'configs\last.json')
    )
    foreach ($t in $targets) {
        if (Test-Path $t) {
            try { Remove-Item $t -Recurse -Force; Write-Ok "wiped $t" }
            catch { Write-Warn2 "wipe failed $t" }
        }
    }
    # Prefetch / Recent entries for the launcher
    Get-ChildItem (Join-Path $env:WINDIR 'Prefetch') -Filter '*OK*WW*' -ErrorAction SilentlyContinue |
        ForEach-Object { try { Remove-Item $_.FullName -Force } catch {} }
}

# ---------- 6. launch ----------
$argLine = if ($Entry -match '^-[a-zA-Z]') { "$Entry" } else { "`"$Entry`"" }
if ($Entry -eq '-m ok') { $argLine = '-m ok' }

Write-Step "starting: $PythonExe $argLine"

# ShellExecute via explorer keeps the parent process out of the python tree,
# so any "child of a terminal / child of cmd" heuristic sees nothing.
try {
    $shell = New-Object -ComObject Shell.Application
    $shell.ShellExecute($PythonExe, $argLine, $RunDir, 'open', 0)
    Start-Sleep -Milliseconds 1200
    $proc = Get-Process -Name 'pythonw', 'python' -ErrorAction SilentlyContinue |
            Sort-Object StartTime -Descending | Select-Object -First 1
    if ($proc) { Write-Ok "running pid=$($proc.Id) name=$($proc.ProcessName)" }
    else { Write-Warn2 'process not detected yet (it may still be importing)' }
} catch {
    Write-Warn2 "ShellExecute failed, falling back to Start-Process: $($_.Exception.Message)"
    $p = Start-Process -FilePath $PythonExe -ArgumentList $argLine -WorkingDirectory $RunDir `
         -WindowStyle Hidden -PassThru
    Write-Ok "running pid=$($p.Id)"
}

Write-Host ''
Write-Host 'next:' -ForegroundColor DarkGray
Write-Host '  python -c "import okww_shim;print(okww_shim.status())"' -ForegroundColor DarkGray
Write-Host '  python -m okww_shim.behavior schedule.json' -ForegroundColor DarkGray
