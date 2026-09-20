# run_marven_text.ps1
# Launch Marven in text-only chat mode using the Python 3.11 venv

$venvPy = "C:\Marven\.venv311\Scripts\python.exe"
$script = "C:\Marven\marven_text_chat.py"

if (!(Test-Path $venvPy)) {
  Write-Host "`nERROR: 3.11 venv not found at $venvPy"
  Write-Host "Create it first:"
  Write-Host "  py -3.11 -m venv C:\Marven\.venv311"
  exit 1
}

if (!(Test-Path $script)) {
  Write-Host "`nERROR: Can't find $script"
  exit 1
}

Write-Host "Using interpreter:" (& $venvPy -V)

# Pass through any extra args to the Python script (e.g., -s mysession -m tinyllama)
& $venvPy $script @args

