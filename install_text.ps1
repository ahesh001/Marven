# install_text.ps1
# Minimal deps for Marven text-only chat in .venv311

$venvPy = "C:\Marven\.venv311\Scripts\python.exe"

if (!(Test-Path $venvPy)) {
  Write-Host "`nERROR: Can't find $venvPy"
  Write-Host "Create it first:"
  Write-Host "  py -3.11 -m venv C:\Marven\.venv311"
  exit 1
}

Write-Host "`n>>> Using interpreter:" ( & $venvPy -V )

Write-Host "`n>>> Upgrading pip ..."
& $venvPy -m pip install --upgrade pip

Write-Host "`n>>> Installing minimal text-chat dependencies ..."
& $venvPy -m pip install langchain-ollama langchain langchain-community langchain-core

Write-Host "`nDone. Run:" 
Write-Host "  & $venvPy C:\Marven\marven_text_chat.py -s chat -m tinyllama"
