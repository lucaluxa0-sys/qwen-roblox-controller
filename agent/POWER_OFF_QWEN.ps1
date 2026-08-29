$ErrorActionPreference = 'SilentlyContinue'
Start-Sleep -Seconds 2
$targets = Get-CimInstance Win32_Process | Where-Object {
    if ($_.ProcessId -eq $PID) { return $false }
    $cmd = [string]$_.CommandLine
    $exe = [string]$_.ExecutablePath
    (
        ($_.Name -ieq 'llama-server.exe' -and ($cmd -match '\.lmstudio\extensions\backends\' -or $exe -match '\.lmstudio\extensions\backends\')) -or
        ($cmd -match 'qwen_full_auto_manager\.py') -or
        ($cmd -match 'qwen_direct_autopilot_runner\.py') -or
        ($cmd -match 'qwen_autopilot_runner\.py') -or
        ($cmd -match 'qwen_controller_launcher\.py') -or
        ($cmd -match 'qwen_roblox_enforced_proxy_current\.py') -or
        ($cmd -match 'qwen_model_auto_updater\.py') -or
        ($cmd -match 'qwen_controller_updater\.py')
    )
}
$targets | Sort-Object ProcessId -Descending | ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
}
Write-Host '[QWEN] Power is OFF. Roblox Studio and LM Studio GUI were left open.'
