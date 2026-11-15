<#
rename_okww.ps1 -- single-machine identity scrub for the ok-ww (pyappify) install.

WHAT THIS CAN AND CANNOT DO
  Removes : install path string, launcher exe name, interpreter exe name,
            registry footprint, app manifest URLs, git history, log files.
  Keeps   : the module fingerprint (cv2.pyd / Qt6WebEngineCore.dll / openvino),
            the plaintext main.py on disk, the ok_ww strings in the .pyc heap,
            the elevated integrity level, and SendInput-based input.

  In other words this moves you from ~8 observable traits to ~5. It does not
  remove the process. Treat it as cost reduction, not as cover.

USAGE
  .\rename_okww.ps1                       # dry run, prints the plan only
  .\rename_okww.ps1 -Mode copy            # copy to a new tree, leave original intact
  .\rename_okww.ps1 -Mode move            # rename the tree in place (fast, see -Rollback)

PARAMETERS
  -Source   existing install root, default %LOCALAPPDATA%\ok-ww
  -Target   destination root for -Mode copy
  -AppName  replacement name for launcher + interpreter, default AudioHost
  -Mode     dryrun (default) | copy | move
  -Rollback undo the registry and directory changes recorded by the last -Mode move
#>
[CmdletBinding()]
param(
    [string]$Source   = "$env:LOCALAPPDATA\ok-ww",
    [string]$Target   = "$env:LOCALAPPDATA\Microsoft\DeviceSync\app",
    [string]$AppName  = "AudioHost",
    [ValidateSet("dryrun", "copy", "move")] [string]$Mode = "dryrun",
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
$stamp    = Get-Date -Format "yyyyMMdd-HHmmss"
$workDir  = Join-Path $PSScriptRoot "_state"
$backup   = Join-Path $workDir "backup-$stamp"
$manifest = Join-Path $workDir "manifest-$stamp.json"

function Say($m) { Write-Host $m }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Ok($m)  { Write-Host "  [ok] $m" -ForegroundColor Green }

# ---------------------------------------------------------------- rollback path
if ($Rollback) {
    $last = Get-ChildItem $workDir -Filter "manifest-*.json" |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $last) { throw "no manifest found under $workDir" }
    $m = Get-Content $last.FullName -Raw | ConvertFrom-Json
    Say "rollback from $($last.Name)"

    # kill anything still running out of the moved tree
    Get-Process -Name $m.app_name -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-Process -Name "python", "pythonw" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($m.new_root) } |
        Stop-Process -Force

    if ($m.old_root -ne $m.new_root -and (Test-Path $m.new_root)) {
        if (Test-Path $m.old_root) { Warn "old root exists, leaving both in place" }
        else { Move-Item -LiteralPath $m.new_root -Destination $m.old_root; Ok "tree restored to $($m.old_root)" }
    }

    if ($m.registry_keys) {
        foreach ($k in $m.registry_keys) {
            if (Test-Path $k.path) { Remove-Item -LiteralPath $k.path -Recurse -Force; Ok "removed $($k.path)" }
        }
    }
    if (Test-Path $m.reg_backup) {
        Say "registry backup still available: $($m.reg_backup)"
        Say "  import with: reg import `"$($m.reg_backup)`""
    }
    return
}

# ------------------------------------------------------------------- survey
if (-not (Test-Path $Source)) { throw "source not found: $Source" }

$exeLauncher = $null
$pyw         = $null
$shortcut    = $null
$manifestApp = $null
foreach ($cand in @("ok-ww.exe", "$AppName.exe")) {
    $p = Join-Path $Source $cand
    if (Test-Path $p) { $exeLauncher = $p; break }
}
$shortcut    = Join-Path $Source "data\apps\ok-ww\.pyappify-shortcut.py"
$manifestApp = Join-Path $Source "data\apps\ok-ww\app.json"
$appRoot     = Join-Path $Source "data\apps\ok-ww"
$pyDir       = Join-Path $appRoot "python"

foreach ($cand in @("pythonw.exe", "python.exe")) {
    $p = Join-Path $pyDir $cand
    if (Test-Path $p) { $pyw = $p; break }
}

Say ""
Say "=== ok-ww identity scrub ==="
Say "  mode      : $Mode"
Say "  source    : $Source"
Say "  target    : $(if ($Mode -eq 'move') { $Target } else { $Target })"
Say "  app name  : $AppName"
Say ""
Say "--- discovered pieces ---"
Say ("  launcher        : " + $(if ($exeLauncher) { $exeLauncher } else { "(none)" }))
Say ("  interpreter     : " + $(if ($pyw) { $pyw } else { "(none)" }))
Say ("  pyappify manifest: " + $(if (Test-Path $manifestApp) { $manifestApp } else { "(none)" }))
Say ("  shortcut wrapper : " + $(if (Test-Path $shortcut) { $shortcut } else { "(none)" }))
if (-not $pyw) { throw "interpreter not found under $pyDir" }

# what the modules say about themselves -- these survive every rename
Say ""
Say "--- traits that renaming does NOT touch ---"
$keep = @(
    @{ n = "cv2.pyd";              p = "data\apps\ok-ww\python\Lib\site-packages\cv2\cv2.pyd" },
    @{ n = "Qt6WebEngineCore.dll"; p = "data\apps\ok-ww\python\Lib\site-packages\PySide6\Qt6WebEngineCore.dll" },
    @{ n = "openvino libs";        p = "data\apps\ok-ww\python\Lib\site-packages\openvino" },
    @{ n = "main.py (plaintext)";  p = "data\apps\ok-ww\working\main.py" }
)
foreach ($k in $keep) {
    $full = Join-Path $Source $k.p
    if (Test-Path $full) { Say "  still visible : $($k.n)" } else { Say "  absent        : $($k.n)" }
}

$pywVi = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($pyw)
Say "  python PE     : $($pywVi.ProductName) / $($pywVi.CompanyName) -- survives a file rename"
Say ""

# ------------------------------------------------------------- plan assembly
$newRoot      = if ($Mode -eq "copy") { $Target } else { $Target }
$movedLauncher = Join-Path $newRoot "$AppName.exe"
$movedPyw      = "$AppName.exe"

$regKeys = @()
foreach ($k in @("HKCU:\Software\ok-ww",
                 "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ok-ww")) {
    if (Test-Path $k) { $regKeys += $k }
}

Say "--- planned changes ---"
Say "  1. relocate install tree            -> $newRoot"
Say "  2. copy interpreter exe             -> data\apps\ok-ww\python\$AppName.exe"
Say "     (must sit beside python312.dll, otherwise the stub starts and exits silently)"
Say "  3. rename launcher exe              -> $AppName.exe (kept, but start-*.cmd bypasses it)"
Say "  4. rewrite .pyappify-shortcut.py    (_main_script, _app_name, sys.executable path)"
Say "  5. rewrite app.json                 (name=$AppName, website cleared, update_method=NONE)"
Say "  6. drop repo\.git                   (src history, ~126 MB)"
Say "  7. drop logs\*                      (ACE FLTMGR has seen these being opened)"
Say "  8. remove registry keys             -> $($regKeys -join ', ')"
Say "  9. emit start-$AppName.cmd"
Say ""
Say "--- what remains after this ---"
Say "  * cv2.pyd / Qt6WebEngineCore.dll / openvino module combination"
Say "  * ok_ww strings in working\main.py and the imported .pyc heap"
Say "  * SendInput key/mouse events (ACEs HIDPARSE cross-check)"
Say "  * plaintext main.py on disk -- an interpreter reading game memory is the shape"
Say "  elevation is cleared by step 5 (admin=false); revert it only if the game runs elevated"
Say ""

if ($Mode -eq "dryrun") {
    Say "dry run only. re-run with -Mode copy (safe) or -Mode move (fast, -Rollback to undo)."
    return
}

# --------------------------------------------------------------- execute
New-Item -ItemType Directory -Force -Path $workDir  | Out-Null
New-Item -ItemType Directory -Force -Path $backup   | Out-Null

# stop anything running out of the old tree
Get-Process -Name "ok-ww", "pythonw", "python" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Source) } |
    ForEach-Object { Say "  stopping pid $($_.Id) $($_.ProcessName)"; Stop-Process -Id $_.Id -Force }

# registry backup before touching it
$regBackupFile = Join-Path $backup "okww-registry.reg"
if ($regKeys.Count -gt 0) {
    $lines = @("Windows Registry Editor Version 5.00", "")
    foreach ($k in $regKeys) { $lines += "[$($k -replace '^HKCU:', 'HKEY_CURRENT_USER')]"; $lines += "" }
    Set-Content -Path $regBackupFile -Value ($lines -join "`r`n") -Encoding ASCII
    Ok "registry backup -> $regBackupFile (keys are empty shells, values live below)"
}

# 1. relocate
if ($Mode -eq "copy") {
    Say "  copying tree (2 GB, this takes a while)..."
    New-Item -ItemType Directory -Force -Path $newRoot | Out-Null
    $rc = robocopy $Source $newRoot /E /NFL /NDL /NJH /NJS /NP `
            /XD ".git" "logs" "__pycache__" /XF "*.pyc" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed with $LASTEXITCODE" }
    Ok "copied to $newRoot"
} else {
    if (Test-Path $newRoot) { throw "target already exists: $newRoot" }
    New-Item -ItemType Directory -Force -Path (Split-Path $newRoot) | Out-Null
    Move-Item -LiteralPath $Source -Destination $newRoot
    Ok "moved $Source -> $newRoot"
}

$srcRoot = $newRoot
$appDir  = Join-Path $srcRoot "data\apps\ok-ww"

# 2/3. rename executables
$oldPyw = Join-Path $appDir "python\pythonw.exe"
if (Test-Path $oldPyw) {
    Copy-Item $oldPyw (Join-Path $appDir "python\$movedPyw") -Force
    Ok "interpreter copy -> python\$movedPyw"
}
$oldLauncher = Get-ChildItem $srcRoot -Filter "*.exe" -File | Where-Object { $_.Name -ne "uninstall.exe" } | Select-Object -First 1
if ($oldLauncher) {
    $newLauncher = Join-Path $srcRoot "$AppName.exe"
    if ($oldLauncher.FullName -ne $newLauncher) {
        Move-Item -LiteralPath $oldLauncher.FullName -Destination $newLauncher -Force
        Ok "launcher -> $AppName.exe"
    }
}

# 4. rewrite the supervisor wrapper
$sc = Join-Path $appDir ".pyappify-shortcut.py"
if (Test-Path $sc) {
    Copy-Item $sc (Join-Path $backup ".pyappify-shortcut.py.bak") -Force
    $t = Get-Content $sc -Raw -Encoding UTF8
    $mainScript = Join-Path $appDir "working\main.py"
    $t = [regex]::Replace($t, '_main_script\s*=\s*"[^"]*"', '_main_script = "' + ($mainScript -replace '\\', '\\') + '"')
    $t = [regex]::Replace($t, '_app_name\s*=\s*"[^"]*"', '_app_name = "' + $AppName + '"')
    Set-Content -Path $sc -Value $t -Encoding UTF8 -NoNewline
    Ok ".pyappify-shortcut.py rewritten"
}

# 5. rewrite the manifest
$aj = Join-Path $appDir "app.json"
if (Test-Path $aj) {
    Copy-Item $aj (Join-Path $backup "app.json.bak") -Force
    $j = Get-Content $aj -Raw -Encoding UTF8 | ConvertFrom-Json
    $j.name          = $AppName
    $j.website       = ""
    $j.update_method = "NONE"
    $j.auto_start    = $false
    $j.show_add_defender = $false
    foreach ($prof in $j.profiles) {
        $prof.show_add_defender = $false
        # drop the elevation requirement: an elevated script interpreter is a far
        # stronger signal than any filename. Only revert this if the game itself
        # runs elevated and window messages start getting UIPI-refused.
        $prof.admin = $false
    }
    $j | ConvertTo-Json -Depth 8 | Set-Content -Path $aj -Encoding UTF8
    Ok "app.json rewritten (name=$AppName, update_method=NONE, admin=false)"
}

# 6. drop git history
$git = Join-Path $appDir "repo\.git"
if (Test-Path $git) {
    $sz = (Get-ChildItem $git -Recurse -File -Force | Measure-Object Length -Sum).Sum / 1MB
    Remove-Item -LiteralPath $git -Recurse -Force
    Ok ("dropped repo\.git ({0:N0} MB)" -f $sz)
}

# 7. drop logs
$logs = Join-Path $srcRoot "logs"
if (Test-Path $logs) {
    $n = (Get-ChildItem $logs -File).Count
    Remove-Item (Join-Path $logs "*") -Force -Recurse -ErrorAction SilentlyContinue
    Ok "cleared $n log files"
}

# 8. registry
foreach ($k in $regKeys) {
    Remove-Item -LiteralPath $k -Recurse -Force -ErrorAction SilentlyContinue
    Ok "removed $k"
}

# 9. launcher
#    Go through .pyappify-shortcut.py, not main.py directly: the wrapper is what
#    resets PATH and pops the inherited PYTHON* vars. Because the wrapper calls
#    subprocess.run([sys.executable, main_script]) it will re-exec our renamed
#    interpreter, so both processes carry the new name and neither is pythonw.exe.
$cmdPath = Join-Path $srcRoot "start-$AppName.cmd"
$cmdBody = @"
@echo off
cd /d "%~dp0"
start "" "%~dp0data\apps\ok-ww\python\$AppName.exe" "%~dp0data\apps\ok-ww\.pyappify-shortcut.py"
"@
Set-Content -Path $cmdPath -Value $cmdBody -Encoding ASCII
Ok "wrote start-$AppName.cmd"

# manifest for rollback
@{
    app_name      = $AppName
    old_root      = $Source
    new_root      = $newRoot
    registry_keys = $regKeys | ForEach-Object { @{ path = $_ } }
    reg_backup    = $regBackupFile
    created       = $stamp
} | ConvertTo-Json -Depth 5 | Set-Content -Path $manifest -Encoding UTF8

Say ""
Ok "done. rollback with: .\rename_okww.ps1 -Rollback"
Say "  manifest: $manifest"
Say "  backups : $backup"
Say ""
Say "launch: $cmdPath"
