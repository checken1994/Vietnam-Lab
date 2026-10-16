<#
.SYNOPSIS
    Script tu dong sinh bao cao truc quan hoa ngay, tuan, thang cho SCP 24/7.
.DESCRIPTION
    Doc du lieu vat ly thoi gian thuc va sinh anh SVG kem bao cao Markdown va Dashboard HTML.
#>
param(
    [ValidateSet("hourly", "daily", "weekly", "monthly", "all")]
    [string]$Cadence = "all"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent (Split-Path -Parent $ScriptDir)

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    $PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (!$PythonExe) { $PythonExe = "python.exe" }
}

$ToolScript = Join-Path $Root "tools\generate_visual_reports.py"

Write-Host "=== SCP VISUAL INTELLIGENCE REPORT GENERATOR ===" -ForegroundColor Cyan
Write-Host "Thoi diem: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray
Write-Host "Khung thoi gian: $Cadence" -ForegroundColor Gray

& $PythonExe $ToolScript --cadence $Cadence

if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Da tao thanh cong bao cao truc quan hoa tai: $Root\reports\visual_reports" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Co loi khi sinh bao cao truc quan hoa." -ForegroundColor Red
}
