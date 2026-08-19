# Launcher so you don't have to remember the venv path.
#
#   .\run.ps1                          -> chat panel, blank page
#   .\run.ps1 -Start google.com        -> chat panel, opens Google
#   .\run.ps1 -Start news.ycombinator.com -Shots
#   .\run.ps1 -Task 'list the top 5 stories'
#   .\run.ps1 -Task 'find plans under $500' -Allow 'pivothealth.com'
#
# Note: single-quote any task containing a $ -- PowerShell expands $ inside
# double quotes, so "under $500" would silently become "under 00".

param(
    [string]$Task  = "",
    [string]$Start = "",
    [string]$Allow = "",
    [switch]$Shots,
    [switch]$Headless
)

# Deliberately NOT "Stop": the agent writes its progress log to stderr, and
# PowerShell turns native stderr into error records, which would abort the run.
$ErrorActionPreference = "Continue"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "No virtualenv found. Creating one..." -ForegroundColor Yellow
    python -m venv (Join-Path $PSScriptRoot ".venv")
    & $py -m pip install --quiet --disable-pip-version-check openai-agents websockets httpx
    Write-Host "Done." -ForegroundColor Green
}

# $args is an automatic variable in PowerShell -- use our own name.
$cliArgs = @()
if ($Start)    { $cliArgs += @("--start", $Start) }
if ($Allow)    { $cliArgs += @("--allow", $Allow) }
if ($Shots)    { $cliArgs += "--shots" }
if ($Headless) { $cliArgs += "--headless" }

if ($Task) {
    & $py (Join-Path $PSScriptRoot "run_task.py") @cliArgs $Task
} else {
    Write-Host "Starting Cuaexp. The chat panel appears in the Chrome window." -ForegroundColor Cyan
    Write-Host "Press Ctrl+C here to stop.`n" -ForegroundColor DarkGray
    & $py (Join-Path $PSScriptRoot "daemon.py") @cliArgs
}
