$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root "scp\venv\Scripts\python.exe"
$BridgeDir = Join-Path $Root "mini-services\llm-bridge"
$SchedulerDir = Join-Path $Root "mini-services\loop-scheduler"
$DashboardDir = Join-Path $Root "dashboard"
$BaseEnv = Join-Path $Root ".env.test"
if (-not (Test-Path $Python)) { throw "Missing test Python: $Python" }
if (-not (Test-Path $BaseEnv)) { throw "Missing safe env: $BaseEnv" }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = Join-Path $Root (".private-secrets\fullstack-http-ready-" + $stamp)
New-Item -ItemType Directory -Force $Evidence | Out-Null
$TempEnv = Join-Path $Evidence "fullstack.env"
$TempScripts = Join-Path $Evidence "launchers"
New-Item -ItemType Directory -Force $TempScripts | Out-Null

# Copy only the safe test env and override test topology. Secret values are never printed.
$envLines = @([IO.File]::ReadAllLines($BaseEnv))
$override = @{
  "SCP_PORT" = "8000"
  "LLM_BRIDGE_PORT" = "11435"
  "LOOP_SCHEDULER_PORT" = "3031"
  "SCP_BASE_URL" = "http://127.0.0.1:8000"
  "LLM_BRIDGE_URL" = "http://127.0.0.1:11435"
  "SCP_DEV_MODE" = "0"
  "SCP_SKIP_STARTUP_GATE" = "1"
  "SCP_AUTO_APPROVE_TIER3" = "0"
  "SCP_EVOLUTION_AUTO" = "0"
  "SCP_ENABLE_CLOSED_LOOP" = "0"
  "SCP_TIER3_ALLOW_RELAXATION" = "0"
  "SCP_TIER3_ALLOW_BAREEXCEPTPASS" = "0"
  "SCP_ENV_FILE" = $TempEnv
  "SCP_SIDECAR_ENV_FILE" = $TempEnv
  "SCP_EGRESS_MODE" = "deny"
}
$seen = @{}
$out = New-Object System.Collections.Generic.List[string]
foreach ($line in $envLines) {
  $trim = $line.Trim()
  if ($trim -and -not $trim.StartsWith("#") -and $trim.Contains("=")) {
    $k = $trim.Split("=", 2)[0].Trim()
    if ($override.ContainsKey($k)) { $out.Add($k + "=" + $override[$k]); $seen[$k] = $true } else { $out.Add($line) }
  } else { $out.Add($line) }
}
foreach ($k in $override.Keys) { if (-not $seen.ContainsKey($k)) { $out.Add($k + "=" + $override[$k]) } }
# Provider keys must be empty in this test harness.
foreach ($line in $out) {
  if ($line -match '^(OPENROUTER_API_KEY(?:_2|_3)?|OPENROUTER_API_KEY_FILE)=([^#\r\n]*)' -and $Matches[2].Trim()) {
    throw "Refusing test run: provider key is non-empty in .env.test"
  }
}
[IO.File]::WriteAllLines($TempEnv, $out)

$services = @{}
function New-Launcher($name, $body) {
  $path = Join-Path $TempScripts ($name + ".ps1")
  [IO.File]::WriteAllText($path, $body, [Text.UTF8Encoding]::new($false))
  return $path
}
$services.backend = New-Launcher "backend" "Set-Location '$Root'; & '$Python' -m scp"
$services.bridge = New-Launcher "bridge" "Set-Location '$BridgeDir'; & node index.js"
$services.scheduler = New-Launcher "scheduler" "Set-Location '$SchedulerDir'; & node index.js"
$services.dashboard = New-Launcher "dashboard" "Set-Location '$DashboardDir'; & npm run dev -- --hostname 127.0.0.1 --port 3000"

$procs = @{}
function Start-Service($name) {
  $outLog = Join-Path $Evidence ($name + ".out.log")
  $errLog = Join-Path $Evidence ($name + ".err.log")
  $p = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $services[$name]) -WorkingDirectory $Root -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
  $procs[$name] = $p
  return $p
}
function Wait-Http($label, $url, $timeoutSec = 90) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  do {
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5; if ([int]$r.StatusCode -eq 200) { return $true } } catch {}
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)
  throw "HTTP readiness timeout: $label $url"
}
function Probe($label, $method, $url) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Method $method -Uri $url -TimeoutSec 15; return @{label=$label; status=[int]$r.StatusCode} }
  catch { $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { -1 }; return @{label=$label; status=$code} }
}
$results = New-Object System.Collections.Generic.List[object]
try {
  # Set process env for all child services; no production .env is loaded.
  foreach ($line in $out) { if ($line -match '^\s*([^#=][^=]*)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2], "Process") } }
  [Environment]::SetEnvironmentVariable("SCP_ENV_FILE", $TempEnv, "Process")
  [Environment]::SetEnvironmentVariable("SCP_SIDECAR_ENV_FILE", $TempEnv, "Process")
  [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
  [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8", "Process")
  Start-Service "backend" | Out-Null
  Wait-Http "backend" "http://127.0.0.1:8000/health" 180 | Out-Null
  $results.Add((Probe "backend_health" "GET" "http://127.0.0.1:8000/health"))
  $results.Add((Probe "backend_detailed" "GET" "http://127.0.0.1:8000/health/detailed"))
  $results.Add((Probe "backend_auth_negative" "POST" "http://127.0.0.1:8000/v105/autofix/run-audit"))
  Start-Service "bridge" | Out-Null
  Wait-Http "bridge" "http://127.0.0.1:11435/api/tags" | Out-Null
  $results.Add((Probe "bridge_root" "GET" "http://127.0.0.1:11435/"))
  $results.Add((Probe "bridge_tags" "GET" "http://127.0.0.1:11435/api/tags"))
  Start-Service "scheduler" | Out-Null
  Wait-Http "scheduler" "http://127.0.0.1:3031/healthz" | Out-Null
  $results.Add((Probe "scheduler_healthz" "GET" "http://127.0.0.1:3031/healthz"))
  $results.Add((Probe "scheduler_pause" "POST" "http://127.0.0.1:3031/pause"))
  $results.Add((Probe "scheduler_resume" "POST" "http://127.0.0.1:3031/resume"))
  Start-Service "dashboard" | Out-Null
  Wait-Http "dashboard" "http://127.0.0.1:3000/" | Out-Null
  $results.Add((Probe "dashboard" "GET" "http://127.0.0.1:3000/"))
  Start-Sleep -Seconds 3
  $bridgeText = [IO.File]::ReadAllText((Join-Path $Evidence "bridge.out.log"))
  $schedulerText = [IO.File]::ReadAllText((Join-Path $Evidence "scheduler.out.log"))
  $dashboardText = [IO.File]::ReadAllText((Join-Path $Evidence "dashboard.out.log"))
  $assert = [ordered]@{
    bridge_explicit_env = $bridgeText.Contains("env source=$TempEnv")
    scheduler_explicit_env = $schedulerText.Contains("env source=$TempEnv")
    no_implicit_dotenv = (-not $bridgeText.Contains("loaded .env from") -and -not $schedulerText.Contains("loaded .env from"))
    scheduler_backend_8000 = $schedulerText.Contains("scp=http://127.0.0.1:8000")
    dashboard_not_lan_exposed = (-not $dashboardText.Contains("192.168."))
    scheduler_initial_scp_online = $schedulerText.Contains("initial SCP liveness: online")
  }
  $external = @()
  foreach ($name in @("backend","bridge","scheduler","dashboard")) { $p = Join-Path $Evidence ($name + ".err.log"); if (Test-Path $p) { $external += @(Select-String -Path $p -Pattern 'wikipedia|case\.law|courtlistener|api\.openrouter|api\.case\.law' -CaseSensitive:$false | ForEach-Object { $_.Line }) } }
  $report = [ordered]@{ timestamp=$stamp; evidence_dir=$Evidence; results=$results; assertions=$assert; outbound_observation_count=$external.Count; outbound_observation_samples=@($external | Select-Object -First 20); teardown_ports=@{} }
  foreach ($port in @(3000,3031,8000,11435)) { $report.teardown_ports[$port.ToString()] = $false }
  [IO.File]::WriteAllText((Join-Path $Evidence "report.json"), ($report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
  Write-Output ("EVIDENCE=" + $Evidence)
  Write-Output ("ASSERTIONS=" + (($assert.GetEnumerator() | ForEach-Object { $_.Key + ":" + $_.Value }) -join ","))
  Write-Output ("OUTBOUND_OBSERVATIONS=" + $external.Count)
} catch {`r`n  [IO.File]::WriteAllText((Join-Path $Evidence "harness-error.txt"), ($_ | Out-String), [Text.UTF8Encoding]::new($false))`r`n  throw`r`n} finally {
  foreach ($p in $procs.Values) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
  foreach ($port in @(3000,3031,8000,11435)) { Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }
  Start-Sleep -Seconds 2
  foreach ($port in @(3000,3031,8000,11435)) { $free = -not [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue); Write-Output ("PORT_" + $port + "_FREE=" + $free) }
}
