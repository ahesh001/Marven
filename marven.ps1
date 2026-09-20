# marven.ps1
# Quick command wrapper for Marven CLI using the Python 3.11 venv

param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$ArgsRest
)

$venvPy = "C:\Marven\.venv311\Scripts\python.exe"
$cli    = "C:\Marven\marven_cli.py"

if (!(Test-Path $venvPy)) {
  Write-Host "\nERROR: 3.11 venv not found at $venvPy"
  Write-Host "Create it first:"
  Write-Host "  py -3.11 -m venv C:\Marven\.venv311"
  exit 1
}
if (!(Test-Path $cli)) {
  Write-Host "\nERROR: Can't find CLI at $cli"
  exit 1
}

& $venvPy $cli @ArgsRest

