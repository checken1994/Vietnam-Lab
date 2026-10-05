[CmdletBinding()]
param(
    # Order is part of the contract: the recovery watchdog is ended and
    # disabled BEFORE the supervisor task is touched. The watchdog fires once
    # per minute and resurrects the supervisor (Start-ScheduledTask) whenever
    # the supervisor state is neither Running nor Disabled, so touching the
    # supervisor first leaves a <=60s resurrect window mid-teardown
    # (Invoke-Recovery in scp_247_recovery_watchdog.ps1). The legacy
    # Hourly-Monitor task is not in the installer manifest
    # (install-manifest.json registers only Supervisor + Recovery-Watchdog);
    # it is disabled here as defense in depth and may already have been
    # deleted by a host op — MISSING is an acceptable terminal state.
    [string[]]$TaskNames = @(
        'SCP-247-Recovery-Watchdog',
        'SCP-247-Supervisor',
        'SCP-247-Hourly-Monitor'
    ),
    # Residual orphan-listener audit. Defaults are the supervisor-managed
    # llm-bridge (8081) and dashboard (3000) ports. Kill candidates on these
    # ports are ONLY accepted after their owning PID's command line/executable
    # path has been matched against SCP indicators via Get-CimInstance —
    # anything else is reported UNVERIFIED for human review, never killed.
    [int[]]$ResidualPorts = @(8081, 3000),
    [switch]$SkipDocker,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PrivateDir = Join-Path $Root '.private-secrets\release-audit\scp-247'
$KillSwitch = Join-Path $PrivateDir 'KILL'
$Ledger = Join-Path $PrivateDir 'supervisor-ledger.jsonl'
$Rows = [System.Collections.Generic.List[object]]::new()

New-Item -ItemType Directory -Force -Path $PrivateDir | Out-Null

function Add-Row {
    param([string]$Step, [string]$Item, [string]$Before, [string]$Action, [string]$After, [string]$Verdict)
    $Rows.Add([pscustomobject]@{
        Step = $Step; Item = $Item; Before = $Before; Action = $Action; After = $After; Verdict = $Verdict
    })
}

function Write-TeardownLedger {
    param([string]$Event, [string]$Detail = '', [string]$Verdict = '')
    $record = [ordered]@{
        ts = [DateTime]::UtcNow.ToString('o')
        event = $Event
        detail = $Detail
        verdict = $Verdict
        task = 'SCP-247-Teardown'
        pid = $PID
        dry_run = [bool]$DryRun
    }
    try {
        [IO.File]::AppendAllText($Ledger, ($record | ConvertTo-Json -Compress -Depth 5) + "`n", [Text.UTF8Encoding]::new($false))
    } catch {
        # Ledger contention must never fail the teardown itself.
    }
}

function Get-TaskState {
    param([string]$Name)
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) { return 'MISSING' }
    return [string]$task.State
}

# True only when the owning process is provably SCP-owned: its command line or
# executable path references this exact repo root (with a path boundary so
# sibling checkouts like "D:\scp-other" never match) or an SCP-specific
# service artifact. Anything else returns $false -> reported, never killed.
function Test-ScpOwnedProcess {
    param([object]$ProcessInfo)
    if ($null -eq $ProcessInfo) { return $false }
    $rootPattern = [regex]::Escape($Root) + '($|["\\\/])'
    foreach ($haystack in @([string]$ProcessInfo.CommandLine, [string]$ProcessInfo.ExecutablePath)) {
        if ([string]::IsNullOrWhiteSpace($haystack)) { continue }
        if ($haystack -match $rootPattern) { return $true }
        if ($haystack -match '(?i)standalone[/\\]server\.js') { return $true }
        if ($haystack -match '(?i)scp_247') { return $true }
        if ($haystack -match '(?i)llm-bridge') { return $true }
        if ($haystack -match '(?i)loop-scheduler') { return $true }
    }
    return $false
}

Write-TeardownLedger -Event 'TEARDOWN_BEGIN' -Detail "ports=$($ResidualPorts -join ',')" -Verdict 'STARTED'

# ---------------------------------------------------------------------------
# STEP 0: KILL switch (fail-safe against a watchdog tick that already fired
# between /End and /DISABLE, and against any accidental restart). Rollback of
# this state change is the documented control action: clear-kill.
# ---------------------------------------------------------------------------
if ($DryRun) {
    Add-Row -Step '0.KILL_SWITCH' -Item $KillSwitch -Before $(if (Test-Path -LiteralPath $KillSwitch) { 'present' } else { 'absent' }) -Action 'skipped (dry run)' -After 'unchanged' -Verdict 'DRYRUN'
} else {
    $killBefore = if (Test-Path -LiteralPath $KillSwitch) { 'present' } else { 'absent' }
    New-Item -ItemType File -Force -Path $KillSwitch | Out-Null
    Add-Row -Step '0.KILL_SWITCH' -Item $KillSwitch -Before $killBefore -Action 'created (rollback: clear-kill)' -After 'present' -Verdict 'PASS'
    Write-TeardownLedger -Event 'KILL_SWITCH_CREATED' -Verdict 'PASS'
}

# ---------------------------------------------------------------------------
# STEP 1: end + disable scheduled tasks, watchdog first.
# ---------------------------------------------------------------------------
foreach ($name in $TaskNames) {
    $before = Get-TaskState -Name $name
    if ($before -eq 'MISSING') {
        Add-Row -Step '1.SCHEDULED_TASK' -Item $name -Before 'MISSING' -Action 'skipped (absent)' -After 'MISSING' -Verdict 'PASS'
        continue
    }
    if ($DryRun) {
        Add-Row -Step '1.SCHEDULED_TASK' -Item $name -Before $before -Action 'skipped (dry run)' -After $before -Verdict 'DRYRUN'
        continue
    }
    # /End stops an in-flight instance. Tolerate "task is not running" (the
    # common steady-state) — only a genuine failure to disable blocks PASS.
    $endOutput = & schtasks.exe /End /TN $name 2>&1
    $endExit = $LASTEXITCODE
    $disableOutput = & schtasks.exe /Change /TN $name /DISABLE 2>&1
    $disableExit = $LASTEXITCODE
    $after = Get-TaskState -Name $name
    $verdict = 'PASS'
    $action = "end(exit=$endExit) + disable"
    if ($disableExit -ne 0 -or $after -ne 'Disabled') {
        $verdict = 'FAIL'
        $action = "end(exit=$endExit) + disable(exit=$disableExit)"
        Write-TeardownLedger -Event 'TASK_DISABLE_FAILED' -Detail "$name disable_exit=$disableExit" -Verdict 'FAIL'
    }
    Add-Row -Step '1.SCHEDULED_TASK' -Item $name -Before $before -Action $action -After $after -Verdict $verdict
    Write-TeardownLedger -Event 'TASK_STOPPED_DISABLED' -Detail "$name before=$before after=$after" -Verdict $verdict
}

# ---------------------------------------------------------------------------
# STEP 2: residual listener audit — verified PID kill-tree only.
# ---------------------------------------------------------------------------
foreach ($port in $ResidualPorts) {
    $conns = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($conns.Count -eq 0) {
        Add-Row -Step '2.LISTENER' -Item "port $port" -Before 'no listener' -Action 'none needed' -After 'no listener' -Verdict 'PASS'
        continue
    }
    $seen = @{}
    foreach ($conn in $conns) {
        $ownerPid = [int]$conn.OwningProcess
        if ($seen.ContainsKey($ownerPid)) { continue }
        $seen[$ownerPid] = $true
        $proc = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
        $before = if ($proc) { "pid=$ownerPid listening" } else { "pid=$ownerPid listening (process info unavailable)" }
        if (-not (Test-ScpOwnedProcess -ProcessInfo $proc)) {
            # Fail-closed: no verified ownership -> never kill, escalate.
            $cmdlineHint = if ($proc) { [string]$proc.CommandLine } else { '<unavailable>' }
            if ($cmdlineHint.Length -gt 160) { $cmdlineHint = $cmdlineHint.Substring(0, 160) }
            Add-Row -Step '2.LISTENER' -Item "port $port" -Before $before -Action 'NOT killed (no verified SCP ownership)' -After "pid=$ownerPid still listening" -Verdict "HUMAN_REVIEW: $cmdlineHint"
            Write-TeardownLedger -Event 'RESIDUAL_LISTENER_UNVERIFIED' -Detail "port=$port pid=$ownerPid" -Verdict 'HUMAN_REVIEW'
            continue
        }
        if ($DryRun) {
            Add-Row -Step '2.LISTENER' -Item "port $port" -Before $before -Action 'taskkill skipped (dry run)' -After $before -Verdict 'DRYRUN'
            continue
        }
        & taskkill.exe /PID $ownerPid /T /F *> $null
        Start-Sleep -Milliseconds 500
        $still = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $ownerPid })
        $after = if ($still.Count -eq 0) { "pid=$ownerPid terminated, listener gone" } else { "pid=$ownerPid STILL LISTENING" }
        $verdict = if ($still.Count -eq 0) { 'PASS' } else { 'FAIL' }
        Add-Row -Step '2.LISTENER' -Item "port $port" -Before $before -Action "taskkill /T /F (cmdline verified: $([string]$proc.CommandLine))" -After $after -Verdict $verdict
        Write-TeardownLedger -Event 'RESIDUAL_LISTENER_KILLED' -Detail "port=$port pid=$ownerPid" -Verdict $verdict
    }
}

# ---------------------------------------------------------------------------
# STEP 3: docker compose --profile loop down (never up).
# ---------------------------------------------------------------------------
if ($SkipDocker) {
    Add-Row -Step '3.COMPOSE_DOWN' -Item 'docker compose --profile loop down' -Before '-' -Action 'skipped (-SkipDocker)' -After '-' -Verdict 'SKIPPED'
} elseif ($DryRun) {
    Add-Row -Step '3.COMPOSE_DOWN' -Item 'docker compose --profile loop down' -Before '-' -Action 'skipped (dry run)' -After '-' -Verdict 'DRYRUN'
} else {
    $composeDownOutput = $null
    $composeExit = $null
    try {
        Push-Location $Root
        $composeDownOutput = & docker compose --profile loop down 2>&1
        $composeExit = $LASTEXITCODE
    } catch {
        $composeDownOutput = @("docker compose invocation failed: $($_.Exception.Message)")
        $composeExit = -1
    } finally {
        Pop-Location
    }
    $tail = (($composeDownOutput | ForEach-Object { [string]$_ }) -join ' | ')
    if ($tail.Length -gt 200) { $tail = $tail.Substring($tail.Length - 200) }
    $verdict = if ($composeExit -eq 0) { 'PASS' } else { 'FAIL' }
    Add-Row -Step '3.COMPOSE_DOWN' -Item 'docker compose --profile loop down' -Before '-' -Action "exit=$composeExit" -After $tail -Verdict $verdict
    Write-TeardownLedger -Event 'COMPOSE_DOWN' -Detail "exit=$composeExit" -Verdict $verdict
}

# ---------------------------------------------------------------------------
# STEP 4: final verified end state.
# ---------------------------------------------------------------------------
foreach ($name in $TaskNames) {
    $state = Get-TaskState -Name $name
    $verdict = if ($state -eq 'Disabled' -or $state -eq 'MISSING') { 'PASS' } else { 'FAIL' }
    Add-Row -Step '4.VERIFY_TASK' -Item $name -Before '-' -Action 'expected Disabled or MISSING' -After $state -Verdict $verdict
}

if (-not $SkipDocker) {
    $containerCount = $null
    $dockerReachable = $true
    try {
        Push-Location $Root
        $ids = @(& docker compose --profile loop ps -aq 2>$null)
        $containerCount = $ids.Count
        if ($LASTEXITCODE -ne 0) { $dockerReachable = $false }
    } catch {
        $dockerReachable = $false
    } finally {
        Pop-Location
    }
    if (-not $dockerReachable) {
        Add-Row -Step '4.VERIFY_CONTAINERS' -Item 'compose project containers' -Before '-' -Action 'expected 0' -After 'UNKNOWN (docker unreachable)' -Verdict 'HUMAN_REVIEW'
    } else {
        $verdict = if ($containerCount -eq 0) { 'PASS' } else { 'FAIL' }
        Add-Row -Step '4.VERIFY_CONTAINERS' -Item 'compose project containers' -Before '-' -Action 'expected 0' -After "$containerCount container(s)" -Verdict $verdict
    }
}

foreach ($port in $ResidualPorts) {
    $remaining = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    $verdict = if ($remaining.Count -eq 0) { 'PASS' } else { 'HUMAN_REVIEW' }
    $after = if ($remaining.Count -eq 0) { 'no listener' } else { "$($remaining.Count) listener(s): $((($remaining | ForEach-Object { $_.OwningProcess }) | Select-Object -Unique) -join ',')" }
    Add-Row -Step '4.VERIFY_LISTENER' -Item "port $port" -Before '-' -Action 'expected 0 listeners' -After $after -Verdict $verdict
}

# ---------------------------------------------------------------------------
# Verdict + table.
# ---------------------------------------------------------------------------
$failRows = @($Rows | Where-Object { $_.Verdict -like 'FAIL*' })
$humanRows = @($Rows | Where-Object { $_.Verdict -like 'HUMAN_REVIEW*' })
if ($failRows.Count -gt 0) { $overall = 'FAIL' }
elseif ($humanRows.Count -gt 0) { $overall = 'HUMAN_REVIEW' }
elseif ($DryRun) { $overall = 'DRYRUN (no enforcement this run)' }
else { $overall = 'PASS' }

Write-Output '== SCP 24/7 stop-stack teardown result =='
$Rows | Format-Table -AutoSize | Out-String -Width 240 | Write-Output
Write-Output "OVERALL_VERDICT=$overall"
Write-TeardownLedger -Event 'TEARDOWN_COMPLETE' -Verdict $overall
# Exit contract: 0 only for a real verified PASS. DRYRUN gets its own code so
# automation can never mistake an unenforced run for a completed teardown.
if ($overall -eq 'PASS') { exit 0 }
elseif ($DryRun) { exit 2 }
else { exit 1 }
