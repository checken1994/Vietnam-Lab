# PowerShell script to register SCP-System-Reporter-247 in Windows Task Scheduler
param(
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$TaskName = "SCP-System-Reporter-247"
$Root = "D:\scp"
$PythonExe = "$Root\.venv\Scripts\python.exe"
$ReporterScript = "$Root\scripts\ops\scp_system_reporter.py"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] Unregistered $TaskName" -ForegroundColor Yellow
    exit 0
}

# Action: Run python.exe scp_system_reporter.py --cadence all
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ReporterScript`" --cadence all" -WorkingDirectory $Root

# Trigger: Run every 1 hour, indefinitely
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)

# Settings: Start when available, don't stop on battery, hidden
$Settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

# Register task under current user
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null

Write-Host "[OK] Task $TaskName da duoc dang ky thanh cong vao Windows Task Scheduler (Chay ngam moi 1 gio doc lap)." -ForegroundColor Green
