$ErrorActionPreference = "Stop"

$InstallDir = "$env:LOCALAPPDATA\QwenRobloxAgent"
$Manager = Join-Path $InstallDir "qwen_full_auto_manager.py"
$Updater = Join-Path $InstallDir "qwen_controller_updater.py"
$Python = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
$PythonW = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\pythonw.exe"

if (-not (Test-Path $Manager)) { throw "Manager missing: $Manager" }
if (-not (Test-Path $Updater)) { throw "Updater missing: $Updater" }
if (-not (Test-Path $Python)) { throw "Python missing: $Python" }
if (-not (Test-Path $PythonW)) { $PythonW = $Python }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $InstallDir "v34-manager-fix-backup-$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item $Manager (Join-Path $backup "qwen_full_auto_manager.py") -Force
Copy-Item $Updater (Join-Path $backup "qwen_controller_updater.py") -Force

Write-Host "Stopping manager only..."
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "qwen_full_auto_manager\.py" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 1

# Fix 1: V3.3 disk/live comparison silently returned blank because re was never imported.
$mgr = Get-Content $Manager -Raw
if ($mgr -notmatch "(?m)^import re$") {
    $mgr = $mgr -replace "(?m)^import hashlib\s*$", "import hashlib`r`nimport re"
}
$mgr = $mgr.Replace('LAST["manager_version"] = "3.3.0"', 'LAST["manager_version"] = "3.4.0"')
[System.IO.File]::WriteAllText($Manager, $mgr, (New-Object System.Text.UTF8Encoding($false)))

# Fix 2: raw.githubusercontent branch URLs could return stale latest.json/controller content.
# Add a unique query value to both manifest and controller fetches.
$upd = Get-Content $Updater -Raw
$oldManifest = 'manifest = json.loads(fetch_bytes(MANIFEST_URL).decode("utf-8"))'
$newManifest = 'manifest_url = MANIFEST_URL + "?qwen_no_cache=" + str(int(time.time() * 1000))' + "`r`n        " +
               'manifest = json.loads(fetch_bytes(manifest_url).decode("utf-8"))'
if ($upd.Contains($oldManifest)) {
    $upd = $upd.Replace($oldManifest, $newManifest)
}

$oldController = 'url = f"{RAW_BASE}/{path}"'
$newController = 'url = f"{RAW_BASE}/{path}?qwen_no_cache={int(time.time() * 1000)}"'
if ($upd.Contains($oldController)) {
    $upd = $upd.Replace($oldController, $newController)
}
[System.IO.File]::WriteAllText($Updater, $upd, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Compiling patched manager/updater..."
& $Python -m py_compile $Manager
if ($LASTEXITCODE -ne 0) { throw "Manager compile failed; backup is $backup" }
& $Python -m py_compile $Updater
if ($LASTEXITCODE -ne 0) { throw "Updater compile failed; backup is $backup" }

Write-Host "Forcing one fresh controller check..."
& $Python $Updater --once
if ($LASTEXITCODE -ne 0) { throw "Controller updater failed; backup is $backup" }

Write-Host "Restarting manager..."
Start-Process -FilePath $PythonW -ArgumentList "`"$Manager`"" -WorkingDirectory $InstallDir -WindowStyle Hidden

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "V3.4 MANAGER/UPDATER FIX INSTALLED" -ForegroundColor Green
Write-Host " - disk/live controller version check fixed (missing import re)"
Write-Host " - GitHub controller manifest fetch now cache-busted"
Write-Host " - controller file fetch now cache-busted"
Write-Host " - manager version now reports 3.4.0"
Write-Host " - controller/Qwen were NOT manually killed by this patch"
Write-Host " - if 6.3.4 was installed, manager should auto-retire stale 6.3.3 after its 60s grace"
Write-Host "Backup: $backup"
