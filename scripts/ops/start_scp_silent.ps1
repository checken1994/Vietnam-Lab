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
if (!(Test-Path "$Root\.env")) {
    $Root = "D:\scp"
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
Write-Host "[0/4] Don dep cua so console va tien trinh trung lap..." -ForegroundColor Cyan
taskkill /f /fi "WINDOWTITLE eq SCP-Loop-Scheduler*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP-Python*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP-Dashboard*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP-LLM-Bridge*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP-Desktop-App*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq SCP Start*" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq Administrator: SCP*" 2>$null | Out-Null

if ($ShouldRestart) {
    @(11434, 3030, 8000, 3000) | ForEach-Object {
        $p = $_
        $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
}

# 2. LLM Bridge (Port 11434)
if (!(Test-HttpPort 11434 "/api/tags")) {
    Write-Host "[1/4] Khoi dong LLM Bridge (port 11434) ngam (Zero-Window)..." -ForegroundColor Gray
    $env:SCP_ENV_FILE = Join-Path $Root ".env"
    $env:SCP_BASE_URL = "http://127.0.0.1:8000"
    $env:ZAI_BRIDGE_PORT = "11434"
    $env:ZAI_BRIDGE_HOST = "127.0.0.1"
    Start-ZeroWindowProcess -Command "bun run dev" `
        -WorkingDirectory (Join-Path $Root "mini-services\llm-bridge") `
        -LogPrefix "bridge"
} else {
    Write-Host "[1/4] LLM Bridge dang hoat dong san (port 11434)." -ForegroundColor Green
}

# 3. Loop Scheduler (Port 3030)
if (!(Test-HttpPort 3030 "/")) {
    Write-Host "[2/4] Khoi dong Loop Scheduler (port 3030) ngam (Zero-Window)..." -ForegroundColor Gray
    $env:SCP_ENV_FILE = Join-Path $Root ".env"
    $env:SCP_BASE_URL = "http://127.0.0.1:8000"
    $env:SCP_INTERNAL_URL = "http://127.0.0.1:8000"
    $env:LOOP_SCHEDULER_URL = "http://127.0.0.1:3030"
    $env:LLM_BRIDGE_URL = "http://127.0.0.1:11434"
    $env:LOOP_LOG_PATH = Join-Path $Root "data\loop_runs.jsonl"
    Start-ZeroWindowProcess -Command "bun run dev" `
        -WorkingDirectory (Join-Path $Root "mini-services\loop-scheduler") `
        -LogPrefix "scheduler"
} else {
    Write-Host "[2/4] Loop Scheduler dang hoat dong san (port 3030)." -ForegroundColor Green
}

# 4. SCP Python FastAPI (Port 8000)
if (!(Test-HttpPort 8000 "/health")) {
    Write-Host "[3/4] Khoi dong SCP Python Backend (port 8000) ngam (Zero-Window)..." -ForegroundColor Gray
    Start-ZeroWindowProcess -Command "python -m scp 8000" `
        -WorkingDirectory $Root `
        -LogPrefix "backend"
} else {
    Write-Host "[3/4] SCP Python Backend dang hoat dong san (port 8000)." -ForegroundColor Green
}

# 5. Dashboard Next.js (Port 3000)
if (!(Test-HttpPort 3000 "/")) {
    Write-Host "[4/4] Khoi dong Web Dashboard (port 3000) ngam (Zero-Window)..." -ForegroundColor Gray
    $env:SCP_INTERNAL_URL = "http://127.0.0.1:8000"
    $env:LOOP_SCHEDULER_URL = "http://127.0.0.1:3030"
    $env:LLM_BRIDGE_URL = "http://127.0.0.1:11434"
    Start-ZeroWindowProcess -Command "bun run start" `
        -WorkingDirectory (Join-Path $Root "dashboard") `
        -LogPrefix "dashboard"
} else {
    Write-Host "[4/4] Web Dashboard dang hoat dong san (port 3000)." -ForegroundColor Green
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
