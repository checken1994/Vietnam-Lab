[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$TaskName = 'SCP-247-Supervisor'
$RecoveryTaskName = 'SCP-247-Recovery-Watchdog'
$Supervisor = Join-Path $Root 'scripts\ops\scp_247_supervisor.ps1'
$RecoveryWatchdog = Join-Path $Root 'scripts\ops\scp_247_recovery_watchdog.ps1'
$PrivateDir = Join-Path $Root '.private-secrets\release-audit\scp-247'
$LogDir = Join-Path $PrivateDir 'logs'
$Manifest = Join-Path $PrivateDir 'install-manifest.json'
# Resolve the PowerShell host executable used to launch the supervisor.
# pwsh.exe (PowerShell 7+) is preferred; fall back to Windows PowerShell.
$pwshCmd = Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue
$pwsh = if ($pwshCmd) { $pwshCmd.Source } else {
    $psCmd = Get-Command 'powershell.exe' -ErrorAction SilentlyContinue
    if ($psCmd) { $psCmd.Source } else { $null }
}
if (-not $pwsh) { throw 'Cannot locate pwsh.exe or powershell.exe — cannot register scheduled task.' }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Resolve-PythonwExecutable {
    foreach ($cand in @(
        $env:SCP_PYTHONW_EXE,
        "D:\opt\scp-venv-w16\Scripts\pythonw.exe",
        (Join-Path $Root ".venv\Scripts\pythonw.exe"),
        (Join-Path $Root "venv\Scripts\pythonw.exe"),
        (Join-Path $Root "scp\venv\Scripts\pythonw.exe")
    )) {
        if ($cand -and (Test-Path $cand)) { return (Resolve-Path $cand).Path }
    }
    $cmd = Get-Command 'pythonw.exe' -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { return $cmd.Source }
    return 'pythonw.exe'
}
$pythonw = Resolve-PythonwExecutable
$SilentRunner = Join-Path $PSScriptRoot 'run_silent.py'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$SilentRunner`" `"$pwsh`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Supervisor`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Background reliability: restart only after an abnormal non-zero/termination result.
# The supervisor exits normally for KILL, so an intentional kill switch is not
# turned into a restart loop. MultipleInstances=IgnoreNew prevents duplicate
# supervisors when AtLogOn and recovery overlap.
$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'SCP fail-closed 24/7 supervisor; policy learning remains disabled'

# Independent recovery task. It is a short-lived one-shot action repeated every
# minute, so a hung watchdog cannot accumulate resident processes. It uses the
# same user session because registering SYSTEM requires an elevated service
# installation boundary that is not available to this user-level installer.
$watchdogAction = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$SilentRunner`" `"$pwsh`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RecoveryWatchdog`""
$watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$watchdogSettings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 30) -MultipleInstances IgnoreNew
$watchdogPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$watchdogTask = New-ScheduledTask -Action $watchdogAction -Trigger $watchdogTrigger -Settings $watchdogSettings -Principal $watchdogPrincipal -Description 'SCP fail-closed recovery watchdog; honors KILL switch'

$record = [ordered]@{
    timestamp = [DateTime]::UtcNow.ToString('o')
    task_name = $TaskName
    recovery_task_name = $RecoveryTaskName
    user = $env:USERNAME
    pythonw = $pythonw
    trigger = 'AtLogOn'
    restart_count = 3
    restart_interval_minutes = 1
    multiple_instances = 'IgnoreNew'
    supervisor = $Supervisor
    pwsh = $pwsh
    policy_learning = 'not_enabled_by_installer'
    production_env_modified = $false
    recovery_trigger = 'Once+Every1Minute'
    recovery_principal = $env:USERNAME
    recovery_logon = 'Interactive'
    recovery_scope = 'active_user_session_only'
    recovery_execution_limit_seconds = 30
    dry_run = [bool]$DryRun
}

if ($DryRun) {
    $record.mode = 'DRY_RUN'
} else {
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    Register-ScheduledTask -TaskName $RecoveryTaskName -InputObject $watchdogTask -Force | Out-Null
    $record.mode = 'REGISTERED'
}
$record | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $Manifest
Write-Output ($record | ConvertTo-Json -Depth 5)

if ($StartNow -and -not $DryRun) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output 'START_REQUESTED=True'
}
