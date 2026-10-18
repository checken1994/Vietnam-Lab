# =============================================================================
# Silent Background Launcher for SCP (Zero-Window Execution)
# Khởi chạy ngầm toàn bộ dịch vụ SCP không mở bất kỳ cửa sổ CMD nào.
# =============================================================================

param(
    [string]$OpenBrowser = "true",
    [string]$Restart = "false"
)

$ShouldOpenBrowser = ($OpenBrowser -eq "true" -or $OpenBrowser -eq "1" -or $OpenBrowser -eq "$true")
$ShouldRestart = ($Restart -eq "true" -or $Restart -eq "1" -or $Restart -eq "$true")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (!(Test-Path "$Root\scp")) {
    $Root = (Resolve-Path "$PSScriptRoot\..\..").Path
}

$LogDir = Join-Path $Root "data\service-logs"
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Load .env into process environment
if (Test-Path "$Root\.env") {
    Get-Content "$Root\.env" | ForEach-Object {
        $l = $_.Trim()
        if ($l -and !$l.StartsWith("#") -and $l.Contains("=")) {
            $parts = $l.Split("=", 2)
            $k = $parts[0].Trim()
            $v = $parts[1].Trim().Trim('"').Trim("'")
            if ($k -and ![System.Environment]::GetEnvironmentVariable($k)) {
                [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
            }
        }
    }
}

function Test-HttpPort([int]$port, [string]$path = "/") {
    try {
        $req = [System.Net.WebRequest]::Create("http://127.0.0.1:$port$path")
        $req.Timeout = 1500
        $req.Method = "GET"
        $resp = $req.GetResponse()
        $resp.Close()
        return $true
    } catch {
        return $false
    }
}

function Start-ZeroWindowProcess {
    param(
        [string]$Command,
        [string]$WorkingDirectory,
        [string]$LogPrefix
    )
    $out = Join-Path $LogDir "$LogPrefix.log"
    $err = Join-Path $LogDir "$LogPrefix-error.log"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "cmd.exe"
    $psi.Arguments = "/c `"$Command > `"$out`" 2> `"$err`"`""
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    [System.Diagnostics.Process]::Start($psi) | Out-Null
}

# 1. Close any visible CMD console windows if requested or on startup
Write-Host "[0/3] Don dep cua so console va tien trinh trung lap..." -ForegroundColor Cyan
taskkill /f /fi "WINDOWTITLE eq SCP-Loop-Scheduler*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP-Python*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP-Dashboard*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP-Desktop-App*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP Start*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq Administrator: SCP*" 2>$null | Out-Null

if ($ShouldRestart) {
    @(3030, 8000, 3000) | ForEach-Object {
        $p = $_
        $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
}

# 2. Loop Scheduler (Port 3030)
if (!(Test-HttpPort 3030 "/")) {
    Write-Host "[1/3] Khoi dong Loop Scheduler (port 3030) ngam (Zero-Window)..." -ForegroundColor Gray
    $env:SCP_ENV_FILE = Join-Path $Root ".env"
    $env:SCP_BASE_URL = "http://127.0.0.1:8000"
    $env:SCP_INTERNAL_URL = "http://127.0.0.1:8000"
    $env:LOOP_SCHEDULER_URL = "http://127.0.0.1:3030"
    $env:LOOP_LOG_PATH = Join-Path $Root "data\loop_runs.jsonl"
    Start-ZeroWindowProcess -Command "node index.js" `
        -WorkingDirectory (Join-Path $Root "mini-services\loop-scheduler") `
        -LogPrefix "scheduler"
} else {
    Write-Host "[1/3] Loop Scheduler dang hoat dong san (port 3030)." -ForegroundColor Green
}

# 3. SCP Python FastAPI (Port 8000)
if (!(Test-HttpPort 8000 "/health")) {
    Write-Host "[2/3] Khoi dong SCP Python Backend (port 8000) ngam (Zero-Window)..." -ForegroundColor Gray
    # [L-01 fix 2026-09-28] Pin a python that actually has the scp deps.
# PATH python may be an unrelated embedded runtime (observed: "No module named scp").
$ScpPython = $null
foreach ($cand in @(
        $env:SCP_PYTHON_EXE,
        "D:\opt\scp-venv-w16\Scripts\python.exe",
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root "venv\Scripts\python.exe")
)) {
    if ($cand -and (Test-Path $cand)) { $ScpPython = $cand; break }
}
if (-not $ScpPython) {
    $py312 = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $py312) { $ScpPython = $py312.Trim() }
}
if (-not $ScpPython) {
    $probe = & python -c "import scp" 2>&1
    if ($LASTEXITCODE -eq 0) { $ScpPython = "python" }
}
if (-not $ScpPython) {
    Write-Host "[FAIL] Khong tim duoc Python co deps scp (venv/py -3.12/PATH). Chay install-scp.bat truoc." -ForegroundColor Red
    exit 1
}
Write-Host "[2/3] SCP Python Backend dung: $ScpPython" -ForegroundColor Gray
Start-ZeroWindowProcess -Command "`"$ScpPython`" -m scp 8000" `
        -WorkingDirectory $Root `
        -LogPrefix "backend"
} else {
    Write-Host "[2/3] SCP Python Backend dang hoat dong san (port 8000)." -ForegroundColor Green
}

# 4. Dashboard Next.js (Port 3000)
if (!(Test-HttpPort 3000 "/")) {
    $env:SCP_INTERNAL_URL = "http://127.0.0.1:8000"
    $env:SCP_API_URL = "http://127.0.0.1:8000"
    $env:SCP_BASE_URL = "http://127.0.0.1:8000"
    $env:LOOP_SCHEDULER_URL = "http://127.0.0.1:3030"
    if (Test-Path (Join-Path $Root ".env")) {
        Get-Content (Join-Path $Root ".env") | ForEach-Object {
            if ($_ -match "^(SCP_PC_CONTROLLER_TOKEN|SCP_AUTH_TOKEN_SECRET)=(.+)$") {
                $envKey = $matches[1]
                $envVal = $matches[2].Trim().Trim('"').Trim("'")
                [Environment]::SetEnvironmentVariable($envKey, $envVal)
            }
        }
    }
    $env:HOSTNAME = "127.0.0.1"
    $env:PORT = "3000"
    Start-ZeroWindowProcess -Command "npm run start" `
        -WorkingDirectory (Join-Path $Root "dashboard") `
        -LogPrefix "dashboard"
} else {
    Write-Host "[3/3] Web Dashboard dang hoat dong san (port 3000)." -ForegroundColor Green
}

# 6. Wait for Backend /health readiness
Write-Host "Dang kiem tra tinh san sang cua he thong..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    if (Test-HttpPort 8000 "/health") {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}

if ($ready) {
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  [OK] SCP SERVICES DANG CHAY NGAM (KHONG CUA SO CMD)       " -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Dashboard:       http://localhost:3000" -ForegroundColor White
    Write-Host "  Loop Scheduler:  http://127.0.0.1:3030" -ForegroundColor White
    Write-Host "  Python API:      http://127.0.0.1:8000/health" -ForegroundColor White
    Write-Host "  Logs:            $LogDir" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Green

    if ($ShouldOpenBrowser) {
        Start-Process "http://localhost:3000"
    }
} else {
    Write-Host "[WARN] Backend port 8000 chua san sang sau 30s. Kiem tra $LogDir\backend-error.log" -ForegroundColor Yellow
}
