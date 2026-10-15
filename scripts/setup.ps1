# ExitCapsule bootstrap (Windows). Idempotent: safe to re-run.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

$tools = @(
    @{ cmd = "git";  id = "Git.Git" },
    @{ cmd = "gh";   id = "GitHub.cli" },
    @{ cmd = "node"; id = "OpenJS.NodeJS.LTS" }
)
foreach ($t in $tools) {
    if (-not (Test-Cmd $t.cmd)) {
        Write-Host "Installing $($t.cmd) via winget..."
        winget install --id $t.id -e --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
    }
}
if (-not (Test-Cmd "uv")) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

uv python install 3.12 3.13
uv sync
if (Test-Path "ui/package.json") { Push-Location ui; npm install; Pop-Location }
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Created .env - put your key on the line: GEMINI_API_KEY=<your key>"
}
uv run exitcapsule doctor
