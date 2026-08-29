$ErrorActionPreference = 'Stop'
$installDir = Join-Path $env:LOCALAPPDATA 'QwenRobloxAgent'
$manager = Join-Path $installDir 'qwen_full_auto_manager.py'
$pythonw = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\pythonw.exe'
$python = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'

$alreadyRunning = Get-CimInstance Win32_Process | Where-Object {
    ([string]$_.CommandLine) -match 'qwen_full_auto_manager\.py'
}
if ($alreadyRunning) {
    Write-Host '[QWEN] Power is already ON.'
    exit 0
}
if (-not (Test-Path -LiteralPath $manager)) {
    throw "Qwen full-auto manager not found: $manager"
}
if (-not (Test-Path -LiteralPath $pythonw)) {
    $pythonw = $python
}
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "Python launcher not found: $pythonw"
}

Write-Host '[QWEN] Powering ON...'
Start-Process -FilePath $pythonw -ArgumentList ('"' + $manager + '"') -WorkingDirectory $installDir -WindowStyle Hidden
Start-Sleep -Seconds 2
$started = Get-CimInstance Win32_Process | Where-Object {
    ([string]$_.CommandLine) -match 'qwen_full_auto_manager\.py'
}
if (-not $started) {
    throw 'Qwen full-auto manager did not start.'
}
Write-Host '[QWEN] Power is ON.'
