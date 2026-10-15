# Start backend (FastAPI on :8000) and UI (Vite on :5173) together.
Set-Location (Split-Path -Parent $PSScriptRoot)
$api = Start-Process -PassThru -NoNewWindow uv -ArgumentList "run","uvicorn","backend.app:app","--port","8000","--reload"
try {
    Push-Location ui; npm run dev; Pop-Location
} finally {
    Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
}
