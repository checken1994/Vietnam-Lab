[CmdletBinding()]
param(
    [switch]$Once,
    [switch]$DryRun,
    [int]$IntervalSeconds = 15,
    [int]$MaxRestartsPerWindow = 5,
    [int]$RestartWindowSeconds = 900
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PrivateDir = Join-Path $Root '.private-secrets\release-audit\scp-247'
$LogDir = Join-Path $PrivateDir 'logs'
$LedgerPath = Join-Path $PrivateDir 'supervisor-ledger.jsonl'
$StatePath = Join-Path $PrivateDir 'supervisor-state.json'
$KillSwitchPath = Join-Path $PrivateDir 'KILL'
$SafeChildEnvFile = Join-Path $PrivateDir 'child-safe.env'
$AdminTokenFile = Join-Path $PrivateDir 'scp-admin-token'
$MutexName = 'Global\SCP_247_Supervisor'
$TaskName = 'SCP-247-Supervisor'

# Windows Job Object is the containment boundary. If Task Scheduler, pwsh,
# or this supervisor is terminated externally, closing the process handle
# kills every child service and prevents orphan runtime writers.
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

public static class ScpJobObjectNative
{
    private const uint JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectLimitKillOnJobClose = 0x2000;

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimitInformation
    {
        public BasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, uint infoClass, ref ExtendedLimitInformation info, uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr CreateKillOnCloseJob()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new InvalidOperationException("CreateJobObject failed: " + Marshal.GetLastWin32Error());
        var info = new ExtendedLimitInformation();
        info.BasicLimitInformation.LimitFlags = JobObjectLimitKillOnJobClose;
        if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, ref info, (uint)Marshal.SizeOf(typeof(ExtendedLimitInformation))))
        {
            int error = Marshal.GetLastWin32Error();
            CloseHandle(job);
            throw new InvalidOperationException("SetInformationJobObject failed: " + error);
        }
        return job;
    }

    public static void Assign(IntPtr job, int processId)
    {
        using (var process = Process.GetProcessById(processId))
        {
            if (!AssignProcessToJobObject(job, process.Handle))
                throw new InvalidOperationException("AssignProcessToJobObject failed: " + Marshal.GetLastWin32Error());
        }
    }

    public static void Close(IntPtr job)
    {
        if (job != IntPtr.Zero) CloseHandle(job);
    }
}
'@

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
# Explicit child env boundary: only safe OFF flags. Auth values are injected
# directly into the child process environment and never written here.
$safeEnvText = @(
    'SCP_DEV_MODE=0',
    'SCP_SKIP_STARTUP_GATE=0',
    'SCP_AUTO_APPROVE_TIER3=0',
    'SCP_TIER3_ALLOW_RELAXATION=0',
    'SCP_TIER3_ALLOW_BAREEXCEPTPASS=0',
    'SCP_ENABLE_CLOSED_LOOP=0',
    'SCP_AUTOFIX_MODE=apply',
    'SCP_MAX_AUDIT_BUGS=5',
    'SCP_AUTOFIX_DETERMINISTIC_ONLY=0',
    'SCP_EVOLUTION_AUTO=0',
    'SCP_EVOLUTION_ENABLED=0',
    'SCP_WHY_LLM_ENABLED=0',
    'SCP_SUBSYSTEM_TELEMETRY_ENABLED=1',
    'SCP_FAST_LEARNING_CYCLE_TIMEOUT_SECONDS=300'
) -join [Environment]::NewLine
$safeEnvTmp = "$SafeChildEnvFile.tmp"
[IO.File]::WriteAllText($safeEnvTmp, $safeEnvText + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $safeEnvTmp -Destination $SafeChildEnvFile -Force
# Create a local SCP admin token once if the private runtime boundary has none.
# This is an SCP control token, not an OpenRouter/provider key, and is never
# printed, committed, or written to production .env.
if (-not (Test-Path -LiteralPath $AdminTokenFile -PathType Leaf)) {
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 32
        $rng.GetBytes($bytes)
        [IO.File]::WriteAllText($AdminTokenFile, [Convert]::ToBase64String($bytes) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    } finally {
        $rng.Dispose()
    }
}
# Include only the private token-file reference in the explicit child env.
# Bun resolves *_FILE during its explicit env loader; Python resolves it via
# auth_config. The token value itself is never copied into this env file.
$capSecretLine = Get-Content (Join-Path $Root '.env') -ErrorAction SilentlyContinue | Where-Object { $_ -match '^SCP_CAPABILITY_SECRET=' } | Select-Object -Last 1
$jwtSecretLine = Get-Content (Join-Path $Root '.env') -ErrorAction SilentlyContinue | Where-Object { $_ -match '^SCP_JWT_SECRET=' } | Select-Object -Last 1
$adminKeyLine = Get-Content (Join-Path $Root '.env') -ErrorAction SilentlyContinue | Where-Object { $_ -match '^SCP_ADMIN_KEY=' } | Select-Object -Last 1
$openRouterLines = Get-Content (Join-Path $Root '.env') -ErrorAction SilentlyContinue | Where-Object { $_ -match '^OPENROUTER_API_KEY' }
$childEnvText = $safeEnvText + [Environment]::NewLine + "SCP_AUTH_TOKEN_SECRET_FILE=$AdminTokenFile" + [Environment]::NewLine + "SCP_SCHEDULER_ADMIN_TOKEN_FILE=$AdminTokenFile" + [Environment]::NewLine
if ($capSecretLine) { $childEnvText += $capSecretLine + [Environment]::NewLine }
if ($jwtSecretLine) { $childEnvText += $jwtSecretLine + [Environment]::NewLine }
if ($adminKeyLine) { $childEnvText += $adminKeyLine + [Environment]::NewLine }
if ($openRouterLines) { foreach ($line in $openRouterLines) { $childEnvText += $line + [Environment]::NewLine } }
[IO.File]::WriteAllText($safeEnvTmp, $childEnvText, [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $safeEnvTmp -Destination $SafeChildEnvFile -Force

$mutex = [Threading.Mutex]::new($false, $MutexName)
    $ownsMutex = $false
$jobHandle = [IntPtr]::Zero
try {
    $ownsMutex = $mutex.WaitOne(0)
    if (-not $ownsMutex) {
        exit 17
    }

    function Write-Ledger {
        param(
            [string]$Event,
            [string]$Service = '',
            [string]$Reason = '',
            [hashtable]$Extra = @{}
        )
        $record = [ordered]@{
            ts = [DateTime]::UtcNow.ToString('o')
            event = $Event
            service = $Service
            reason = $Reason
            task = $TaskName
            pid = $PID
        }
        foreach ($key in $Extra.Keys) {
            $record[$key] = $Extra[$key]
        }
        [IO.File]::AppendAllText($LedgerPath, ($record | ConvertTo-Json -Compress -Depth 5) + "`n", [Text.UTF8Encoding]::new($false))
    }

    function Get-EnvFlag {
        param([string]$Name)
        $envFile = Join-Path $Root '.env'
        $value = $null
        if (Test-Path $envFile) {
            $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "^$([regex]::Escape($Name))\s*=" } | Select-Object -Last 1
            if ($line) {
                $value = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
            }
        }
        if ($null -eq $value -and $null -ne [Environment]::GetEnvironmentVariable($Name)) {
            $value = [Environment]::GetEnvironmentVariable($Name)
        }
        if ($null -eq $value -or $value -eq '') { return 'MISSING' }
        if ($value -match '^(1|true|on|yes|active)$') { return 'ON' }
        if ($value -match '^(0|false|off|no|inactive)$') { return 'OFF' }
        return 'INVALID'
    }

    function Assert-Guardrails {
        $dangerous = @(
            'SCP_DEV_MODE',
            'SCP_SKIP_STARTUP_GATE',
            'SCP_AUTO_APPROVE_TIER3',
            'SCP_TIER3_ALLOW_RELAXATION',
            'SCP_TIER3_ALLOW_BAREEXCEPTPASS'
        )
        $bad = @()
        foreach ($name in $dangerous) {
            $state = Get-EnvFlag $name
            if ($state -eq 'ON' -or $state -eq 'INVALID') { $bad += $name }
            Write-Ledger -Event 'GUARDRAIL' -Reason "$name=$state"
        }
        $closedLoop = Get-EnvFlag 'SCP_ENABLE_CLOSED_LOOP'
        Write-Ledger -Event 'GUARDRAIL' -Reason "SCP_ENABLE_CLOSED_LOOP=$closedLoop"
        if ($closedLoop -eq 'ON') { $bad += 'SCP_ENABLE_CLOSED_LOOP' }
        if ($bad.Count -gt 0) {
            Write-Ledger -Event 'BLOCKED' -Reason 'dangerous_or_closed_loop_flag_active' -Extra @{ flags = ($bad -join ',') }
            throw 'SCP 24/7 blocked by guardrail flags'
        }
    }

    function Resolve-Executable {
        param([string]$Name)
        $command = Get-Command $Name -ErrorAction SilentlyContinue
        if (-not $command) { throw "Executable not found: $Name" }
        return $command.Source
    }

    $bun = Resolve-Executable 'bun'
    $python = Resolve-Executable 'python'
    $DashboardDir = Join-Path $Root 'dashboard'
    $DashboardStandaloneServer = Join-Path $DashboardDir '.next\standalone\server.js'
    $DashboardBuildId = Join-Path $DashboardDir '.next\BUILD_ID'
    $DashboardNextCli = Join-Path $DashboardDir 'node_modules\next\dist\bin\next'

    $services = @(
        [ordered]@{ Name = 'llm-bridge'; File = $bun; Args = @('run', 'dev'); Dir = (Join-Path $Root 'mini-services\llm-bridge'); Port = 8081; Url = 'http://127.0.0.1:8081/api/tags' },
        [ordered]@{ Name = 'loop-scheduler'; File = $bun; Args = @('run', 'dev'); Dir = (Join-Path $Root 'mini-services\loop-scheduler'); Port = 3030; Url = 'http://127.0.0.1:3030/' },
        [ordered]@{ Name = 'scp-python'; File = $python; Args = @('-m', 'scp', '8000'); Dir = $Root; Port = 8000; Url = 'http://127.0.0.1:8000/health' },
        [ordered]@{ Name = 'autofix-worker'; File = $python; Args = @('-m', 'scp.autofix.deterministic_worker', '--max-jobs', '1', '--watch'); Dir = $Root; Port = 0; Url = '' },
        [ordered]@{ Name = 'dashboard'; File = $bun; Args = @('run', 'start'); Dir = (Join-Path $Root 'dashboard'); Port = 3000; Url = 'http://127.0.0.1:3000/' }
    )

    function Test-PortInUse {
        param([int]$Port)
        if ($Port -le 0) { return $false }
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    }

    function Test-HttpHealthy {
        param([string]$Url)
        if ([string]::IsNullOrWhiteSpace($Url)) { return $true }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4 -SkipHttpErrorCheck
            return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
        } catch {
            return $false
        }
    }

    function Get-DashboardBuildState {
        $sourceFiles = @()
        foreach ($path in @(
            (Join-Path $DashboardDir 'package.json'),
            (Join-Path $DashboardDir 'next.config.ts'),
            (Join-Path $DashboardDir 'next.config.mjs'),
            (Join-Path $DashboardDir 'tsconfig.json'),
            (Join-Path $DashboardDir 'postcss.config.mjs')
        )) {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $sourceFiles += Get-Item -LiteralPath $path
            }
        }
        $sourceFiles += @(Get-ChildItem -LiteralPath (Join-Path $DashboardDir 'src') -Recurse -File -Force -ErrorAction SilentlyContinue)
        $sourceFiles += @(Get-ChildItem -LiteralPath (Join-Path $DashboardDir 'public') -Recurse -File -Force -ErrorAction SilentlyContinue)
        $artifacts = @()
        foreach ($path in @($DashboardStandaloneServer, $DashboardBuildId)) {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $artifacts += Get-Item -LiteralPath $path
            }
        }
        if ($sourceFiles.Count -eq 0 -or $artifacts.Count -lt 2) {
            return [pscustomobject]@{ Fresh = $false; SourceUtc = ''; ArtifactUtc = ''; Reason = 'build_or_source_missing' }
        }
        $latestSource = $sourceFiles | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        $oldestArtifact = $artifacts | Sort-Object LastWriteTimeUtc | Select-Object -First 1
        $fresh = $oldestArtifact.LastWriteTimeUtc -ge $latestSource.LastWriteTimeUtc
        return [pscustomobject]@{
            Fresh = [bool]$fresh
            SourceUtc = $latestSource.LastWriteTimeUtc.ToString('o')
            ArtifactUtc = $oldestArtifact.LastWriteTimeUtc.ToString('o')
            Reason = if ($fresh) { 'build_covers_current_dashboard_sources' } else { 'build_older_than_dashboard_sources' }
        }
    }

    function Ensure-DashboardDependencies {
        if (Test-Path -LiteralPath $DashboardNextCli -PathType Leaf) {
            return $true
        }
        if (Test-PortInUse 3000) {
            Write-Ledger -Event 'DASHBOARD_DEPENDENCY_INSTALL_BLOCKED' -Service 'dashboard' -Reason 'next_cli_missing_but_port_3000_occupied'
            return $false
        }
        $logRunId = "$(Get-Date -AsUTC -Format 'yyyyMMddTHHmmssfffffffZ').$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
        $stdout = Join-Path $LogDir "dashboard-deps.$logRunId.out.log"
        $stderr = Join-Path $LogDir "dashboard-deps.$logRunId.err.log"
        try {
            $install = Start-Process -FilePath $bun -ArgumentList @('install', '--frozen-lockfile') -WorkingDirectory $DashboardDir -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -Wait
            if ($install.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $DashboardNextCli -PathType Leaf)) {
                Write-Ledger -Event 'DASHBOARD_DEPENDENCY_INSTALL_FAILED' -Service 'dashboard' -Reason 'bun_install_nonzero_or_next_cli_missing' -Extra @{ exit_code = $install.ExitCode }
                return $false
            }
        } catch {
            Write-Ledger -Event 'DASHBOARD_DEPENDENCY_INSTALL_FAILED' -Service 'dashboard' -Reason $_.Exception.GetType().Name
            return $false
        }
        Write-Ledger -Event 'DASHBOARD_DEPENDENCY_INSTALLED' -Service 'dashboard' -Reason 'next_cli_available'
        return $true
    }

    function Ensure-DashboardBuild {
        if (-not (Ensure-DashboardDependencies)) {
            return $false
        }
        $state = Get-DashboardBuildState
        if ($state.Fresh) {
            Write-Ledger -Event 'DASHBOARD_BUILD_FRESH' -Service 'dashboard' -Reason $state.Reason -Extra @{ source_utc = $state.SourceUtc; artifact_utc = $state.ArtifactUtc }
            return $true
        }
        if (Test-PortInUse 3000) {
            Write-Ledger -Event 'DASHBOARD_BUILD_REFRESH_BLOCKED' -Service 'dashboard' -Reason 'stale_or_missing_build_but_port_3000_occupied'
            return $false
        }
        $logRunId = "$(Get-Date -AsUTC -Format 'yyyyMMddTHHmmssfffffffZ').$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
        $stdout = Join-Path $LogDir "dashboard-build.$logRunId.out.log"
        $stderr = Join-Path $LogDir "dashboard-build.$logRunId.err.log"
        try {
            $build = Start-Process -FilePath $bun -ArgumentList @('run', 'build') -WorkingDirectory $DashboardDir -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -Wait
            if ($build.ExitCode -ne 0) {
                Write-Ledger -Event 'DASHBOARD_BUILD_FAILED' -Service 'dashboard' -Reason 'bun_build_nonzero' -Extra @{ exit_code = $build.ExitCode }
                return $false
            }
        } catch {
            Write-Ledger -Event 'DASHBOARD_BUILD_FAILED' -Service 'dashboard' -Reason $_.Exception.GetType().Name
            return $false
        }
        $state = Get-DashboardBuildState
        if (-not $state.Fresh) {
            Write-Ledger -Event 'DASHBOARD_BUILD_FAILED' -Service 'dashboard' -Reason 'build_artifact_still_stale_or_missing'
            return $false
        }
        Write-Ledger -Event 'DASHBOARD_BUILD_REFRESHED' -Service 'dashboard' -Reason $state.Reason -Extra @{ source_utc = $state.SourceUtc; artifact_utc = $state.ArtifactUtc }
        return $true
    }



    function Start-ScpService {
        param([object]$Service)
        if (Test-PortInUse $Service.Port) {
            Write-Ledger -Event 'PORT_OCCUPIED' -Service $Service.Name -Reason 'listener_exists_before_supervisor_start'
            return $null
        }
        if (-not (Test-Path $Service.Dir)) {
            Write-Ledger -Event 'START_REJECTED' -Service $Service.Name -Reason 'working_directory_missing'
            return $null
        }
        # Use immutable per-start log files. Reusing one path would let a later
        # healthy restart truncate the previous process's stderr and erase the
        # only evidence of a transient crash. The supervisor ledger records the
        # basenames for provenance without exposing private absolute paths.
        $logRunId = "$(Get-Date -AsUTC -Format 'yyyyMMddTHHmmssfffffffZ').$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
        $stdout = Join-Path $LogDir "$($Service.Name).$logRunId.out.log"
        $stderr = Join-Path $LogDir "$($Service.Name).$logRunId.err.log"
        $oldLoopLog = $env:LOOP_LOG_PATH
        $oldScpBaseUrl = $env:SCP_BASE_URL
        $oldScpInternalUrl = $env:SCP_INTERNAL_URL
        $oldLoopSchedulerUrl = $env:LOOP_SCHEDULER_URL
        # LLM_BRIDGE_URL remains a compatibility alias for older callers; the
        # actual provider contract below is Ollama-only.
        $oldLlmBridgeUrl = $env:LLM_BRIDGE_URL
        $oldOllamaHost = $env:OLLAMA_HOST
        $oldOllamaEnabled = $env:OLLAMA_ENABLED
        $oldProviderMode = $env:SCP_LLM_PROVIDER_MODE
        $oldClosedLoop = $env:SCP_ENABLE_CLOSED_LOOP
        $oldScpEnvFile = $env:SCP_ENV_FILE
        $oldAuthToken = $env:SCP_AUTH_TOKEN_SECRET
        $oldAuthPassword = $env:SCP_AUTH_PASSWORD
        $oldAuthTokenFile = $env:SCP_AUTH_TOKEN_SECRET_FILE
        $oldAuthPasswordFile = $env:SCP_AUTH_PASSWORD_FILE
        $oldSchedulerAdminToken = $env:SCP_SCHEDULER_ADMIN_TOKEN
        $oldSchedulerAdminTokenFile = $env:SCP_SCHEDULER_ADMIN_TOKEN_FILE
        $oldAutofixMode = $env:SCP_AUTOFIX_MODE
        $oldAutofixDeterministicOnly = $env:SCP_AUTOFIX_DETERMINISTIC_ONLY
        $oldAutofixWorkerMode = $env:SCP_AUTOFIX_WORKER_MODE
        $oldAutofixWorkerRoot = $env:SCP_AUTOFIX_WORKER_ROOT
        $oldAutofixWorkerDataDir = $env:SCP_AUTOFIX_WORKER_DATA_DIR
        $oldAutofixWorkerRisk = $env:SCP_AUTOFIX_WORKER_AUTO_APPLY_RISK
        $oldPythonPath = $env:PYTHONPATH
        $oldDangerous = @{}
        foreach ($flag in @('SCP_DEV_MODE','SCP_SKIP_STARTUP_GATE','SCP_AUTO_APPROVE_TIER3','SCP_TIER3_ALLOW_RELAXATION','SCP_TIER3_ALLOW_BAREEXCEPTPASS')) {
            $oldDangerous[$flag] = [Environment]::GetEnvironmentVariable($flag, 'Process')
        }
        $env:SCP_ENABLE_CLOSED_LOOP = '0'
        try {
            if ($Service.Name -in @('llm-bridge','loop-scheduler','scp-python','autofix-worker','dashboard')) {
                $env:LOOP_LOG_PATH = Join-Path $Root 'data\loop_runs.jsonl'
                $env:SCP_BASE_URL = 'http://127.0.0.1:8000'
                # Dashboard proxy contract is explicit rather than relying on
                # a stale build's localhost fallback. This keeps the running
                # process aligned with the supervisor's service map.
                $env:SCP_INTERNAL_URL = 'http://127.0.0.1:8000'
                $env:LOOP_SCHEDULER_URL = 'http://127.0.0.1:3030'
                # Use the new managed llm-bridge on port 8081
                $env:SCP_LLM_BRIDGE_PORT = '8081'
                $env:OLLAMA_HOST = 'http://127.0.0.1:8081'
                $env:OLLAMA_ENABLED = 'true'
                $env:SCP_LLM_PROVIDER_MODE = 'ollama_only'
                $env:LLM_BRIDGE_URL = 'http://127.0.0.1:8081'
                $env:SCP_AUTOFIX_MODE = 'apply'
                $env:SCP_AUTOFIX_DETERMINISTIC_ONLY = '0'
                $env:SCP_AUTOFIX_WORKER_MODE = 'inline'
                $env:SCP_AUTOFIX_WORKER_ROOT = $Root
                $env:SCP_AUTOFIX_WORKER_DATA_DIR = Join-Path $Root 'data'
                $env:SCP_AUTOFIX_WORKER_AUTO_APPLY_RISK = 'low'
                # Force `python -m scp` to resolve the checked working tree first.
                # Without this, an older user-site package can shadow the repo even
                # when the venv executable and working directory are correct.
                $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($oldPythonPath)) { $Root } else { $Root + [IO.Path]::PathSeparator + $oldPythonPath }
                # Bun/Python child processes must not receive the entire
                # production env file: it may contain unrelated/dangerous flags.
                # Read only auth values into the child environment, never print
                # or write them, and force all mutation/learning flags OFF.
                foreach ($flag in @('SCP_DEV_MODE','SCP_SKIP_STARTUP_GATE','SCP_AUTO_APPROVE_TIER3','SCP_TIER3_ALLOW_RELAXATION','SCP_TIER3_ALLOW_BAREEXCEPTPASS','SCP_ENABLE_CLOSED_LOOP')) {
                    Set-Item -Path "Env:$flag" -Value '0'
                }
                # [M-04 fix 2026-10-01] Prefer the repo-root .env (the file this
                # script already reads auth values from at lines ~154-157); fall
                # back to the legacy parent-directory .env so installs that keep
                # auth state one level above the checkout keep working.
                # Existence gate only — the file is never read or printed here.
                $explicitEnvFile = Join-Path $Root '.env'
                if (-not (Test-Path -LiteralPath $explicitEnvFile -PathType Leaf)) {
                    $explicitEnvFile = Join-Path (Split-Path $Root -Parent) '.env'
                }
                if (-not (Test-Path -LiteralPath $explicitEnvFile -PathType Leaf)) {
                    Write-Ledger -Event 'START_REJECTED' -Service $Service.Name -Reason 'explicit_auth_env_file_missing'
                    return $null
                }
                $adminToken = (Get-Content -LiteralPath $AdminTokenFile -Raw -Encoding UTF8).Trim()
                if ([string]::IsNullOrWhiteSpace($adminToken)) {
                    Write-Ledger -Event 'START_REJECTED' -Service $Service.Name -Reason 'private_admin_token_empty'
                    return $null
                }
                $env:SCP_AUTH_TOKEN_SECRET = $adminToken
                $env:SCP_SCHEDULER_ADMIN_TOKEN = $adminToken
                $env:SCP_SCHEDULER_ADMIN_TOKEN_FILE = $AdminTokenFile
                Remove-Item Env:SCP_AUTH_PASSWORD -ErrorAction SilentlyContinue
                $env:SCP_AUTH_TOKEN_SECRET_FILE = $AdminTokenFile
                Remove-Item Env:SCP_AUTH_PASSWORD_FILE -ErrorAction SilentlyContinue
                $env:SCP_ENV_FILE = $SafeChildEnvFile
            }
            if ($DryRun) {
                Write-Ledger -Event 'DRYRUN_START' -Service $Service.Name -Reason 'start_would_be_requested'
                return [pscustomobject]@{ Id = 0; Name = $Service.Name; StartedAt = [DateTime]::UtcNow }
            }
            $process = Start-Process -FilePath $Service.File -ArgumentList $Service.Args -WorkingDirectory $Service.Dir -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
            try {
                Add-ScpProcessToJob -ChildProcessId $process.Id
            } catch {
                & taskkill.exe /PID $process.Id /T /F *> $null
                throw
            }
            Write-Ledger -Event 'START' -Service $Service.Name -Reason 'supervisor_start' -Extra @{ child_pid = $process.Id; port = $Service.Port; contained_by_job = (-not $DryRun); stdout_log = [IO.Path]::GetFileName($stdout); stderr_log = [IO.Path]::GetFileName($stderr) }
            return [pscustomobject]@{ Id = $process.Id; Name = $Service.Name; Process = $process; StdoutStream = $null; StderrStream = $null; StdoutCopyTask = $null; StderrCopyTask = $null; StartedAt = [DateTime]::UtcNow }
        } finally {
            $env:LOOP_LOG_PATH = $oldLoopLog
            $env:SCP_BASE_URL = $oldScpBaseUrl
            if ($null -eq $oldScpInternalUrl) { Remove-Item Env:SCP_INTERNAL_URL -ErrorAction SilentlyContinue } else { $env:SCP_INTERNAL_URL = $oldScpInternalUrl }
            if ($null -eq $oldLoopSchedulerUrl) { Remove-Item Env:LOOP_SCHEDULER_URL -ErrorAction SilentlyContinue } else { $env:LOOP_SCHEDULER_URL = $oldLoopSchedulerUrl }
            $env:LLM_BRIDGE_URL = $oldLlmBridgeUrl
            if ($null -eq $oldOllamaHost) { Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue } else { $env:OLLAMA_HOST = $oldOllamaHost }
            if ($null -eq $oldOllamaEnabled) { Remove-Item Env:OLLAMA_ENABLED -ErrorAction SilentlyContinue } else { $env:OLLAMA_ENABLED = $oldOllamaEnabled }
            if ($null -eq $oldProviderMode) { Remove-Item Env:SCP_LLM_PROVIDER_MODE -ErrorAction SilentlyContinue } else { $env:SCP_LLM_PROVIDER_MODE = $oldProviderMode }
            $env:SCP_ENABLE_CLOSED_LOOP = $oldClosedLoop
            if ($null -eq $oldScpEnvFile) { Remove-Item Env:SCP_ENV_FILE -ErrorAction SilentlyContinue } else { $env:SCP_ENV_FILE = $oldScpEnvFile }
            if ($null -eq $oldAuthToken) { Remove-Item Env:SCP_AUTH_TOKEN_SECRET -ErrorAction SilentlyContinue } else { $env:SCP_AUTH_TOKEN_SECRET = $oldAuthToken }
            if ($null -eq $oldAuthPassword) { Remove-Item Env:SCP_AUTH_PASSWORD -ErrorAction SilentlyContinue } else { $env:SCP_AUTH_PASSWORD = $oldAuthPassword }
            if ($null -eq $oldAuthTokenFile) { Remove-Item Env:SCP_AUTH_TOKEN_SECRET_FILE -ErrorAction SilentlyContinue } else { $env:SCP_AUTH_TOKEN_SECRET_FILE = $oldAuthTokenFile }
            if ($null -eq $oldAuthPasswordFile) { Remove-Item Env:SCP_AUTH_PASSWORD_FILE -ErrorAction SilentlyContinue } else { $env:SCP_AUTH_PASSWORD_FILE = $oldAuthPasswordFile }
            if ($null -eq $oldSchedulerAdminToken) { Remove-Item Env:SCP_SCHEDULER_ADMIN_TOKEN -ErrorAction SilentlyContinue } else { $env:SCP_SCHEDULER_ADMIN_TOKEN = $oldSchedulerAdminToken }
            if ($null -eq $oldSchedulerAdminTokenFile) { Remove-Item Env:SCP_SCHEDULER_ADMIN_TOKEN_FILE -ErrorAction SilentlyContinue } else { $env:SCP_SCHEDULER_ADMIN_TOKEN_FILE = $oldSchedulerAdminTokenFile }
            if ($null -eq $oldAutofixMode) { Remove-Item Env:SCP_AUTOFIX_MODE -ErrorAction SilentlyContinue } else { $env:SCP_AUTOFIX_MODE = $oldAutofixMode }
            if ($null -eq $oldAutofixDeterministicOnly) { Remove-Item Env:SCP_AUTOFIX_DETERMINISTIC_ONLY -ErrorAction SilentlyContinue } else { $env:SCP_AUTOFIX_DETERMINISTIC_ONLY = $oldAutofixDeterministicOnly }
            if ($null -eq $oldAutofixWorkerMode) { Remove-Item Env:SCP_AUTOFIX_WORKER_MODE -ErrorAction SilentlyContinue } else { $env:SCP_AUTOFIX_WORKER_MODE = $oldAutofixWorkerMode }
            if ($null -eq $oldAutofixWorkerRoot) { Remove-Item Env:SCP_AUTOFIX_WORKER_ROOT -ErrorAction SilentlyContinue } else { $env:SCP_AUTOFIX_WORKER_ROOT = $oldAutofixWorkerRoot }
            if ($null -eq $oldAutofixWorkerDataDir) { Remove-Item Env:SCP_AUTOFIX_WORKER_DATA_DIR -ErrorAction SilentlyContinue } else { $env:SCP_AUTOFIX_WORKER_DATA_DIR = $oldAutofixWorkerDataDir }
            if ($null -eq $oldAutofixWorkerRisk) { Remove-Item Env:SCP_AUTOFIX_WORKER_AUTO_APPLY_RISK -ErrorAction SilentlyContinue } else { $env:SCP_AUTOFIX_WORKER_AUTO_APPLY_RISK = $oldAutofixWorkerRisk }
            if ($null -eq $oldPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $oldPythonPath }
            foreach ($flag in $oldDangerous.Keys) {
                if ($null -eq $oldDangerous[$flag]) { Remove-Item "Env:$flag" -ErrorAction SilentlyContinue } else { Set-Item "Env:$flag" $oldDangerous[$flag] }
            }
        }
    }

    function Add-ScpProcessToJob {
        param([int]$ChildProcessId)
        if ($DryRun) { return }
        if ($jobHandle -eq [IntPtr]::Zero) { throw 'Job Object is not initialized' }
        [ScpJobObjectNative]::Assign($jobHandle, $ChildProcessId)
    }

    function Stop-ScpService {
        param([object]$Runtime, [string]$Reason)
        if ($null -eq $Runtime -or $Runtime.Id -le 0 -or $DryRun) { return }
        & taskkill.exe /PID $Runtime.Id /T /F *> $null
        if ($null -ne $Runtime.Process) { try { $Runtime.Process.WaitForExit(2000) } catch {} }
        foreach ($task in @($Runtime.StdoutCopyTask, $Runtime.StderrCopyTask)) {
            if ($null -ne $task) { try { $task.Wait(2000) } catch {} }
        }
        foreach ($stream in @($Runtime.StdoutStream, $Runtime.StderrStream)) {
            if ($null -ne $stream) { try { $stream.Dispose() } catch {} }
        }
        Write-Ledger -Event 'STOP' -Service $Runtime.Name -Reason $Reason -Extra @{ child_pid = $Runtime.Id }
    }

    Assert-Guardrails
    if ($DryRun) {
        Write-Ledger -Event 'OLLAMA_DEPENDENCY_CHECK_SKIPPED' -Service 'ollama' -Reason 'dry_run_does_not_require_external_provider'
    }
    if (-not $DryRun) {
        $jobHandle = [ScpJobObjectNative]::CreateKillOnCloseJob()
        Write-Ledger -Event 'JOB_OBJECT_CREATED' -Reason 'kill_on_job_close_child_containment'
    }
    if (Test-Path $KillSwitchPath) {
        Write-Ledger -Event 'KILL_SWITCH_PRESENT' -Reason 'startup_abort'
        exit 20
    }
    if (-not (Ensure-DashboardBuild)) {
        Write-Ledger -Event 'SUPERVISOR_ABORTED' -Service 'dashboard' -Reason 'dashboard_build_not_current_or_refresh_blocked'
        exit 22
    }

    $restartHistory = @{}
    $ollamaRestartHistory = @()
    $runtime = @{}
    foreach ($service in $services) {
        $runtime[$service.Name] = Start-ScpService $service
        $restartHistory[$service.Name] = @()
    }

    Write-Ledger -Event 'SUPERVISOR_STARTED' -Reason $(if ($DryRun) { 'dry_run' } else { 'canary_or_service_mode' }) -Extra @{ interval_seconds = $IntervalSeconds; root = $Root }

    do {
        Start-Sleep -Seconds $IntervalSeconds
        if (Test-Path $KillSwitchPath) {
            Write-Ledger -Event 'KILL_SWITCH' -Reason 'operator_file_present'
            foreach ($service in $services) { Stop-ScpService $runtime[$service.Name] 'kill_switch' }
            break
        }
        $ollamaHealthy = $true # [DNA #6] API-first: skip local ollama check
        if ($ollamaHealthy) {
            if ($ollamaRestartHistory.Count -gt 0) {
                Write-Ledger -Event 'CIRCUIT_CLOSED' -Service 'ollama' -Reason 'external_dependency_recovered' -Extra @{ cleared_restart_count = $ollamaRestartHistory.Count }
                $ollamaRestartHistory = @()
            }
        }
        foreach ($service in $services) {
            $entry = $runtime[$service.Name]
            $processAlive = $false
            if ($null -ne $entry -and $entry.Id -gt 0) {
                $processAlive = [bool](Get-Process -Id $entry.Id -ErrorAction SilentlyContinue)
            } elseif ($DryRun) {
                $processAlive = $true
            }
            $buildFresh = $true
            if ($service.Name -eq 'dashboard') {
                $dashboardBuildState = Get-DashboardBuildState
                $buildFresh = $dashboardBuildState.Fresh
                if (-not $buildFresh) {
                    Write-Ledger -Event 'DASHBOARD_BUILD_STALE' -Service 'dashboard' -Reason $dashboardBuildState.Reason -Extra @{ source_utc = $dashboardBuildState.SourceUtc; artifact_utc = $dashboardBuildState.ArtifactUtc }
                }
            }
            $healthy = $processAlive -and (Test-HttpHealthy $service.Url) -and $buildFresh
            # A listener can pre-date this Supervisor (for example after an
            # interrupted task restart). It cannot be safely adopted into this
            # Job Object, but a healthy listener must not consume restart budget
            # every interval. Distinguish it in the ledger and start a contained
            # child only after the listener actually disappears.
            $unmanagedHealthy = ($null -eq $entry) -and ($service.Port -gt 0) -and $buildFresh -and (Test-HttpHealthy $service.Url)
            if ($unmanagedHealthy) {
                Write-Ledger -Event 'UNMANAGED_HEALTHY' -Service $service.Name -Reason 'healthy_listener_not_owned_by_supervisor'
                continue
            }
            if ($healthy) {
                if ($restartHistory[$service.Name].Count -gt 0) {
                    Write-Ledger -Event 'CIRCUIT_CLOSED' -Service $service.Name -Reason 'service_recovered' -Extra @{ cleared_restart_count = $restartHistory[$service.Name].Count }
                    $restartHistory[$service.Name] = @()
                }
                Write-Ledger -Event 'HEALTHY' -Service $service.Name -Reason $(if ([string]::IsNullOrWhiteSpace($service.Url)) { 'process_ok_no_http_probe' } else { 'process_and_http_ok' })
                continue
            }
            $now = [DateTime]::UtcNow
            $restartHistory[$service.Name] = @($restartHistory[$service.Name] | Where-Object { ($now - $_).TotalSeconds -lt $RestartWindowSeconds })
            if ($restartHistory[$service.Name].Count -ge $MaxRestartsPerWindow) {
                Write-Ledger -Event 'CIRCUIT_OPEN' -Service $service.Name -Reason 'restart_budget_exhausted' -Extra @{ restart_count = $restartHistory[$service.Name].Count }
                continue
            }
            $failureReason = if ($service.Name -eq 'dashboard' -and -not $buildFresh) { 'stale_dashboard_build' } else { 'health_failure' }
            Stop-ScpService $entry $failureReason
            $restartHistory[$service.Name] += $now
            if ($service.Name -eq 'dashboard' -and -not (Ensure-DashboardBuild)) {
                Write-Ledger -Event 'RESTART_BLOCKED' -Service 'dashboard' -Reason 'dashboard_build_refresh_not_completed' -Extra @{ restart_count = $restartHistory[$service.Name].Count }
                continue
            }
            $runtime[$service.Name] = Start-ScpService $service
            Write-Ledger -Event 'RESTART' -Service $service.Name -Reason $failureReason -Extra @{ restart_count = $restartHistory[$service.Name].Count }
        }
    } while (-not $Once)

    foreach ($service in $services) { Stop-ScpService $runtime[$service.Name] 'supervisor_exit' }
    Write-Ledger -Event 'SUPERVISOR_STOPPED' -Reason $(if (Test-Path $KillSwitchPath) { 'kill_switch' } else { 'once_or_exit' })
} catch {
    Write-Host "Exception: $_"
    Write-Ledger -Event 'SUPERVISOR_ERROR' -Reason $_.Exception.Message
    exit 1
} finally {
    if ($jobHandle -ne [IntPtr]::Zero) {
        [ScpJobObjectNative]::Close($jobHandle)
        $jobHandle = [IntPtr]::Zero
    }
    if ($ownsMutex) { $mutex.ReleaseMutex() | Out-Null }
    $mutex.Dispose()
}
