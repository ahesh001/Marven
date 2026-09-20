# run_marven.ps1
# Launches the Marven voice server with the Python 3.11 venv

$venvPy = "C:\Marven\.venv311\Scripts\python.exe"
$repo   = "C:\Marven\marven-voice-interrupt-prototype_cpu_tuned"
$server = Join-Path $repo "server.py"
$client = Join-Path $repo "client.html"

if (!(Test-Path $venvPy)) {
  Write-Host "`nERROR: 3.11 venv not found at $venvPy"
  Write-Host "Create it first:"
  Write-Host "  py -3.11 -m venv C:\Marven\.venv311"
  exit 1
}

if (!(Test-Path $server)) {
  Write-Host "`nERROR: Can't find server at $server"
  exit 1
}

Write-Host "Using interpreter:" (& $venvPy -V)

# Optional: launch client in your default browser (uncomment if you want auto-open)
# Start-Process $client

Write-Host "`nStarting server..."
Write-Host "Press Ctrl+C to stop."
Push-Location $repo
& $venvPy $server
Pop-Location
